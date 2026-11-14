"""子图 2：图片 VLM 核验 image_vlm_subgraph —— 上下文隔离的关键载体。

- 内部用 Send API 按图片数动态扇出，每张图一个并行判图任务；
- 子图内部才持有图片明细（live 模式下是 base64 / OCR 全文），
  collect 节点收敛后只回传两个结构化因子：image_authenticity、image_text_consistency；
- 主图 state 里永远只有结构化结论，不带原始图片数据。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from listing_guard.state import append_list
from listing_guard.tools.registry import make_factor
from listing_guard.tools.vision import judge_image


class ImageState(TypedDict, total=False):
    images: list
    rooms: int
    image_results: Annotated[list, append_list]  # 子图内部中间数据（不外泄）
    factors: dict                                # 收敛后的结构化结论


def _fanout_images(state: ImageState) -> list[Send]:
    return [Send("judge_image", {"image": img}) for img in state["images"]]


def _judge_image(payload: dict) -> dict:
    result = judge_image(payload["image"])
    return {"image_results": [result]}


def _collect(state: ImageState) -> dict:
    results = state.get("image_results") or []
    rooms = state.get("rooms") or 0
    n = len(results)

    if n == 0:
        empty_auth = make_factor("image_authenticity", 0.0, "无图片", hit=True)
        empty_consist = make_factor("image_text_consistency", 0.0, "无图片", hit=True)
        return {"factors": {"image_authenticity": empty_auth, "image_text_consistency": empty_consist}}

    avg_auth = sum(r["authenticity"] for r in results) / n
    duplicates = [r for r in results if r["is_duplicate"]]
    dup_detail = f"（重复于 {', '.join(str(r.get('duplicate_of', '?')) for r in duplicates)}）" if duplicates else ""

    auth = make_factor(
        "image_authenticity",
        avg_auth,
        f"{n} 张图并行判定，平均真实性 {avg_auth:.2f}；盗图 {len(duplicates)} 张{dup_detail}",
        hit=avg_auth < 0.7,
        veto_triggered=bool(duplicates) or avg_auth < 0.25,
    )

    if rooms > 0 and all(r["detected_rooms"] > 0 for r in results):
        ratio = min(1.0, sum(r["detected_rooms"] for r in results) / n / rooms)
        consist_detail = f"实拍识别房间数均值 {sum(r['detected_rooms'] for r in results) / n:.1f} vs 描述 {rooms} 室"
        consist = make_factor(
            "image_text_consistency", ratio, consist_detail, hit=ratio < 0.9
        )
    else:
        consist = make_factor("image_text_consistency", 0.5, "房间数识别不完整，按中性偏严处理", hit=True)

    return {"factors": {"image_authenticity": auth, "image_text_consistency": consist}}


def build_image_subgraph() -> StateGraph:
    g: StateGraph = StateGraph(ImageState)
    g.add_node("judge_image", _judge_image)
    g.add_node("collect", _collect)
    g.add_conditional_edges(START, _fanout_images, ["judge_image"])
    g.add_edge("judge_image", "collect")
    g.add_edge("collect", END)
    return g.compile()
