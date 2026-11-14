"""视觉算子（图片真实性 / 图文一致性 / OCR）。

mock：读取 fixture 图片的 `mock` 提示字段，输出确定性结论；
live：调用多模态大模型（结构化输出，见 config/prompts/vision_authenticity.md）。
"""

from __future__ import annotations

from listing_guard.settings import get_settings


def judge_image(image: dict) -> dict:
    """单图判定：真实性、是否盗图、识别房间数、OCR 文本。"""
    s = get_settings()
    if s.env.audit_mode == "live":
        return _judge_image_live(image)
    return _judge_image_mock(image)


def _judge_image_mock(image: dict) -> dict:
    mock = image.get("mock") or {}
    return {
        "path": image.get("path"),
        "authenticity": float(mock.get("authenticity", 0.9)),
        "is_duplicate": bool(mock.get("is_duplicate", False)),
        "duplicate_of": mock.get("duplicate_of"),
        "phash": mock.get("phash"),
        "detected_rooms": int(mock.get("detected_rooms", 0)),
        "ocr_text": mock.get("ocr_text", ""),
    }


def _judge_image_live(image: dict) -> dict:
    """live 模式：多模态大模型 + 感知哈希查重（需 uv sync --group live）。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("live 模式需要安装 live 依赖组：uv sync --group live") from exc

    s = get_settings()
    from listing_guard.settings import CONFIG_DIR  # noqa: PLC0415

    prompt = (CONFIG_DIR / "prompts" / "vision_authenticity.md").read_text(encoding="utf-8")
    llm = ChatOpenAI(
        model=s.env.vision_model,
        api_key=s.env.openai_api_key,
        base_url=s.env.openai_base_url,
    ).with_structured_output(
        {
            "authenticity": "number",
            "is_duplicate": "boolean",
            "detected_rooms": "integer",
            "ocr_text": "string",
            "reason": "string",
        }
    )
    result = llm.invoke(
        [
            ("system", prompt),
            ("user", [{"type": "image_url", "image_url": {"url": image.get("url", "")}}, "判断该房源图片"]),
        ]
    )
    return {
        "path": image.get("path"),
        "authenticity": float(result.get("authenticity", 0.5)),
        "is_duplicate": bool(result.get("is_duplicate", False)),
        "detected_rooms": int(result.get("detected_rooms", 0)),
        "ocr_text": result.get("ocr_text", ""),
        "reason": result.get("reason", ""),
    }
