"""任务 API：创建、进度查询、重试/续跑、音频下载。"""
import logging
import os
from typing import Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.pipeline.runner import TaskRunner
from app.store import db

logger = logging.getLogger("vss.api.tasks")
router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class CreateTaskBody(BaseModel):
    """创建任务请求体。text 为清洗前原文（Markdown 可直接粘贴）。"""
    title: Optional[str] = ""
    text: str = Field(min_length=1)
    voice_key: str
    provider_id: str
    params: Optional[Dict] = None


@router.post("")
def create_task(body: CreateTaskBody) -> Dict:
    """清洗→分句→落库→启动合成线程。"""
    try:
        task_id = TaskRunner.create_task(
            title=body.title or "未命名稿件", raw_text=body.text,
            voice_key=body.voice_key, provider_id=body.provider_id,
            params=body.params,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (KeyError, PermissionError) as e:
        raise HTTPException(400, str(e))
    return TaskRunner.start_or_resume(task_id)


@router.get("")
def list_tasks() -> Dict:
    """任务历史（最近 50 条）。"""
    return {"tasks": db.list_tasks()}


@router.get("/{task_id}")
def get_task(task_id: str) -> Dict:
    """任务详情 + 段级进度（前端 2s 轮询此端点）。"""
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    segments = db.get_segments(task_id)
    return {
        "task": {k: v for k, v in task.items()
                 if k not in ("params_json",)},
        "segments": segments,
        "running": TaskRunner.is_running(task_id),
    }


@router.post("/{task_id}/retry")
def retry_task(task_id: str) -> Dict:
    """断点续跑/失败重跑：只处理未完成段（ok 段自动跳过）。"""
    try:
        return TaskRunner.start_or_resume(task_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{task_id}/segments/{seq}/retry")
def retry_segment(task_id: str, seq: int) -> Dict:
    """单段重试（失败段标红按钮调用）。"""
    try:
        return TaskRunner.retry_segment(task_id, seq)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{task_id}/audio")
def download_audio(task_id: str, format: str = "wav") -> FileResponse:
    """下载整稿（?format=wav|mp3|m4a，默认 wav 兼容旧链接）。

    mp3/m4a 按需转码并缓存（母带重新生成后自动失效），见 pipeline/encoder.py。
    """
    from app.pipeline import encoder

    fmt = format.lower()
    if fmt not in encoder.FORMATS:
        raise HTTPException(400, f"不支持的格式：{format}（可选 wav / mp3 / m4a）")
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if not task.get("output_path") or not os.path.isfile(task["output_path"]):
        raise HTTPException(409, "整稿尚未生成（任务未完成或存在失败段）")
    try:
        path = encoder.encode(task["output_path"], fmt)
    except RuntimeError as e:
        raise HTTPException(400, str(e))  # 如缺 lameenc 的安装提示
    return FileResponse(path, media_type=encoder.media_type(fmt),
                        filename=encoder.export_filename(task["title"], fmt))


@router.get("/{task_id}/segments/{seq}/audio")
def segment_audio(task_id: str, seq: int) -> FileResponse:
    """单段试听（失败段排查用）。"""
    for s in db.get_segments(task_id):
        if s["seq"] == seq and s["status"] == "ok" and s.get("audio_path"):
            if os.path.isfile(s["audio_path"]):
                return FileResponse(s["audio_path"], media_type="audio/wav")
    raise HTTPException(404, "段不存在或未成功合成")
