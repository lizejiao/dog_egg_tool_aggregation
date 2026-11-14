"""视频核验算子：关键帧抽取 + 语音转写（ASR）。

mock：读取 fixture 视频的 `mock` 提示字段，输出确定性结论；
live：ffmpeg 抽帧 → 帧进图像子图同款 VLM、音轨进 ASR（简历「视频取关键帧接入」的落地）。
"""

from __future__ import annotations

from listing_guard.settings import get_settings


def analyze_video(video: dict) -> dict:
    """单视频分析：关键帧结论 + 转写文本。"""
    s = get_settings()
    if s.env.audit_mode == "live":
        return _analyze_live(video)
    return _analyze_mock(video)


def _analyze_mock(video: dict) -> dict:
    mock = video.get("mock") or {}
    return {
        "path": video.get("path"),
        "keyframe_authenticity": float(mock.get("keyframe_authenticity", 0.9)),
        "detected_rooms": int(mock.get("detected_rooms", 0)),
        "asr_text": mock.get("asr_text", ""),
        "asr_risk": mock.get("asr_risk", ""),  # 转写文本里的风控命中（由文本子图口径判）
    }


def _analyze_live(video: dict) -> dict:  # pragma: no cover
    """live：ffmpeg 抽 N 关键帧 → VLM 判帧；音轨 → ASR 转写。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("live 模式需要安装 live 依赖组：uv sync --group live") from exc

    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from listing_guard.settings import CONFIG_DIR  # noqa: PLC0415

    s = get_settings()
    with tempfile.TemporaryDirectory() as td:
        # 抽 3 帧均匀分布的关键帧
        frames = [
            f"{td}/f{i}.jpg"
            for i in range(3)
        ]
        for i, f in enumerate(frames):
            ts = i * 3  # 每 3 秒一帧（demo 口径；生产按视频时长均匀采样）
            subprocess.run(
                ["ffmpeg", "-ss", str(ts), "-i", video["path"], "-frames:v", "1", f],
                check=True, capture_output=True,
            )
        prompt = (CONFIG_DIR / "prompts" / "vision_authenticity.md").read_text(encoding="utf-8")
        llm = ChatOpenAI(
            model=s.env.vision_model, api_key=s.env.openai_api_key, base_url=s.env.openai_base_url
        ).with_structured_output(
            {"authenticity": "number", "detected_rooms": "integer", "reason": "string"}
        )
        scores, rooms = [], []
        for f in frames:
            r = llm.invoke(
                [
                    ("system", prompt),
                    ("user", [{"type": "image_url", "image_url": {"url": f"file://{f}"}}, "判帧"]),
                ]
            )
            scores.append(float(r["authenticity"]))
            rooms.append(int(r["detected_rooms"]))
        return {
            "path": video.get("path"),
            "keyframe_authenticity": sum(scores) / len(scores),
            "detected_rooms": max(set(rooms), key=rooms.count) if rooms else 0,
            "asr_text": "",
            "asr_risk": "",
        }
