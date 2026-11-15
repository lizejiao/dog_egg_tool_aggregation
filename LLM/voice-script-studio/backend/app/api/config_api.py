"""全局配置 API：合成默认参数读写（设置页「全局合成参数」）。

- GET  /api/config/synthesis：读当前默认值（任务级 params 可覆盖）
- PUT  /api/config/synthesis：写回（白名单 + 范围校验，非法值 400）
"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.store import config as config_store

logger = logging.getLogger("vss.api.config")
router = APIRouter(prefix="/api/config", tags=["config"])


class SynthesisBody(BaseModel):
    chars_per_segment: Optional[int] = None
    max_retries: Optional[int] = None
    pause_between: Optional[float] = None
    pause_between_paragraphs: Optional[float] = None


@router.get("/synthesis")
def get_synthesis() -> Dict:
    """全局合成默认参数（全部引擎共用）。"""
    return {"synthesis": config_store.load_config()["synthesis"]}


@router.put("/synthesis")
def put_synthesis(body: SynthesisBody = Body(...)) -> Dict:
    """更新全局合成默认参数；越界返回 400 并说明范围。"""
    patch = {k: v for k, v in body.dict().items() if v is not None}
    if not patch:
        raise HTTPException(400, "无有效更新字段")
    try:
        synth = config_store.update_synthesis_config(patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info("更新全局合成参数：%s", list(patch))
    return {"synthesis": synth}
