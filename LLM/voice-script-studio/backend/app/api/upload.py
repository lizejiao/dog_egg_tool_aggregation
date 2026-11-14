"""稿件上传 API：.txt / .md 白名单校验 + 清洗预览（docs/ARCHITECTURE.md §4.3）。"""
import logging
import os

from fastapi import APIRouter, File, HTTPException, UploadFile

from app import settings
from app.pipeline import splitter, text_clean

logger = logging.getLogger("vss.api.upload")
router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("/script")
async def upload_script(file: UploadFile = File(...)) -> dict:
    """上传稿件 → 返回清洗后文本 + 预估段数（前端确认后再 POST /api/tasks）。

    仅接受 .txt / .md；超 2MB 拒绝。UTF-8 解码失败时尝试 GBK 兜底。
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.SCRIPT_EXTS:
        raise HTTPException(400, f"仅支持 {'/'.join(sorted(settings.SCRIPT_EXTS))}，"
                                 f"收到 {ext or '无扩展名'}")
    data = await file.read()
    if len(data) > settings.MAX_SCRIPT_BYTES:
        raise HTTPException(400, f"稿件超过 {settings.MAX_SCRIPT_BYTES // 1024 // 1024}MB 上限")
    if not data.strip():
        raise HTTPException(400, "稿件为空")

    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            raw = data.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(400, "稿件编码无法识别（请另存为 UTF-8 后重新上传）")

    cleaned = text_clean.clean_script(raw)
    if not cleaned:
        raise HTTPException(400, "清洗后无有效文本（稿件可能全部是代码块/链接）")
    return {
        "title": os.path.splitext(file.filename)[0],
        "cleaned_text": cleaned,
        "segment_count": splitter.estimate_segments(cleaned),
    }
