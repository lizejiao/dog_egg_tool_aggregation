"""引擎设置 API：列举、更新配置（apiKey/模型串/开关）、连接测试、暖机/释放。"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app import providers as provider_registry
from app.providers.cosyvoice_local import release_worker, warm_info
from app.store import config as config_store

logger = logging.getLogger("vss.api.providers")
router = APIRouter(prefix="/api/providers", tags=["providers"])


class UpdateProviderBody(BaseModel):
    """白名单字段：enabled / api_key / model / base_url / proxy / timeout_s /
    max_concurrency / fp16 / idle_kill_s。"""
    enabled: Optional[bool] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    proxy: Optional[str] = None
    timeout_s: Optional[int] = None
    max_concurrency: Optional[int] = None
    fp16: Optional[bool] = None          # 本地引擎：半精度推理
    idle_kill_s: Optional[int] = None    # 本地引擎：空闲回收秒数


@router.get("")
def list_providers() -> Dict:
    """引擎列表（apiKey 掩码显示）。"""
    return {"providers": provider_registry.list_available()}


@router.put("/{provider_id}")
def update_provider(provider_id: str, body: UpdateProviderBody) -> Dict:
    """更新引擎配置。apiKey 传入空字符串 = 不修改（前端只显掩码）。

    本地引擎配置变更（fp16/idle_kill_s 等）→ 立即释放 worker 并回未暖机，
    避免配置漂移与暖机唯一性打架（用户需重新暖机生效新配置）。
    """
    if provider_id not in config_store.load_config()["providers"]:
        raise HTTPException(404, f"未知 provider: {provider_id}")
    patch = {k: v for k, v in body.dict().items() if v is not None}
    if not patch:
        raise HTTPException(400, "无有效更新字段")
    if provider_id == "cosyvoice_local" \
            and any(k in patch for k in ("fp16", "idle_kill_s", "timeout_s")):
        rel = release_worker()
        if not rel["ok"] and rel["message"].startswith("有任务"):
            raise HTTPException(409, rel["message"] + "；配置暂未保存，请稍后再试")
    try:
        config_store.update_provider_config(provider_id, patch)
    except (KeyError, PermissionError) as e:
        raise HTTPException(400, str(e))
    logger.info("更新引擎配置 %s：%s", provider_id,
                [k for k in patch if k != "api_key"])  # 日志不落 key 字段名以外内容
    return {"providers": provider_registry.list_available()}


@router.get("/warm-state")
def get_warm_state() -> Dict:
    """本地引擎暖机状态（前端倒计时数据源；30s 轮询 + 页面聚焦时刷新）。"""
    return warm_info()


@router.post("/cosyvoice_local/release")
def release_local_worker() -> Dict:
    """手动释放本地引擎（设置页「释放引擎」按钮）。合成进行中返回 409。"""
    rel = release_worker()
    if not rel["ok"]:
        raise HTTPException(409, rel["message"])
    return rel


@router.post("/{provider_id}/test")
def test_provider(provider_id: str) -> Dict:
    """连接/鉴权测试（设置页「测试连接」按钮）。"""
    try:
        provider = provider_registry.get_provider(provider_id)
    except (KeyError, PermissionError) as e:
        raise HTTPException(400, str(e))
    result = provider.test_connection()
    # test_connection 内部已脱敏；此处再兜一层防实现遗漏
    return {"ok": bool(result.get("ok")),
            "message": config_store.sanitize(str(result.get("message", "")))}


@router.post("/{provider_id}/warmup")
def warmup_provider(provider_id: str) -> Dict:
    """暖机（设置页「暖机引擎」按钮）：本地引擎加载权重，云端引擎建连。"""
    try:
        provider = provider_registry.get_provider(provider_id)
    except (KeyError, PermissionError) as e:
        raise HTTPException(400, str(e))
    result = provider.warmup()
    return {"ok": bool(result.get("ok")),
            "message": config_store.sanitize(str(result.get("message", "")))}
