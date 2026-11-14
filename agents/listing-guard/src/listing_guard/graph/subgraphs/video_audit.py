"""子图 3：视频核验 video_subgraph —— ASR 转写 + 关键帧识别。

简历「视频取关键帧接入」的落地：
- 抽帧判真：关键帧逐一鉴伪（与图像子图同源 VLM 能力）；
- 语音转写：ASR 文本作为用户可控输入，风控扫描（违禁词/联系方式/注入）
  在子图内完成，只回传结构化结论——转写全文不进主图 state（上下文隔离）；
- 帧数动态扇出（Send）：N 个关键帧 N 个并行判帧任务。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from listing_guard.state import append_list
from listing_guard.tools.compliance import scan_banned_words, scan_contact, scan_injection
from listing_guard.tools.registry import make_factor
from listing_guard.tools.video import analyze_video


class VideoState(TypedDict, total=False):
    videos: list
    rooms: int
    video_results: Annotated[list, append_list]  # 子图内部：逐视频明细（不外泄）
    asr_texts: Annotated[list, append_list]      # 子图内部：转写文本（不外泄）
    factors: dict


def _fanout_videos(state: VideoState) -> list[Send]:
    return [Send("analyze", {"video": v}) for v in state["videos"]]


def _analyze(payload: dict) -> dict:
    result = analyze_video(payload["video"])
    return {"video_results": [result]}


def _scan_asr(state: VideoState) -> dict:
    """转写文本风控：子图内完成扫描，只回传命中结论。"""
    texts = [r.get("asr_text", "") for r in state.get("video_results") or []]
    corpus = "\n".join(texts)
    return {
        "asr_texts": [
            {
                "banned": scan_banned_words(corpus),
                "contact": scan_contact(corpus),
                "injection": scan_injection(corpus),
            }
        ]
    }


def _collect(state: VideoState) -> dict:
    results = state.get("video_results") or []
    rooms = state.get("rooms") or 0
    asr = (state.get("asr_texts") or [{}])[0]

    if not results:
        # 无视频不是违规（视频为可选材料）：返回满分但注明未参与
        factor = make_factor("video_authenticity", 1.0, "未提供视频（可选项），因子满分不参与扣分")
        return {"factors": {"video_authenticity": factor}}

    avg_auth = sum(r["keyframe_authenticity"] for r in results) / len(results)
    asr_hits = []
    if asr.get("banned"):
        asr_hits.append(f"语音违禁词：{'、'.join(asr['banned'])}")
    if asr.get("contact"):
        asr_hits.append(f"语音含联系方式（命中 {len(asr['contact'])} 个特征）")
    if asr.get("injection"):
        asr_hits.append("语音转写含注入特征")

    veto = avg_auth < 0.25 or bool(asr_hits)
    detail = (
        f"{len(results)} 个视频关键帧鉴伪均值 {avg_auth:.2f}；"
        f"识别房间数 {max((r['detected_rooms'] for r in results), default=0)}"
        + (f"；ASR 风控命中：{'；'.join(asr_hits)}" if asr_hits else "；ASR 转写无风控命中")
    )
    score = 0.05 if veto else avg_auth
    factor = make_factor(
        "video_authenticity", score, detail, hit=avg_auth < 0.7 or bool(asr_hits),
        veto_triggered=veto,
    )
    return {"factors": {"video_authenticity": factor}}


def build_video_subgraph() -> StateGraph:
    g: StateGraph = StateGraph(VideoState)
    g.add_node("analyze", _analyze)
    g.add_node("scan_asr", _scan_asr)
    g.add_node("collect", _collect)
    g.add_conditional_edges(START, _fanout_videos, ["analyze"])
    g.add_edge("analyze", "scan_asr")
    g.add_edge("scan_asr", "collect")
    g.add_edge("collect", END)
    return g.compile()
