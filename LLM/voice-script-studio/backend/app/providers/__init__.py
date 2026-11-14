"""providers 包：引擎注册表与工厂。

每次调用 get_provider() 都从 config.json 现建实例——设置页改完 apiKey
立即生效，无需重启服务。
"""
import logging
from typing import Dict, List

from app.providers.base import (  # noqa: F401  (对外统一出口)
    Caps, SynthParams, TTSProvider, TTSProviderError, VoiceMeta,
    strip_or_keep_tags,
)
from app.providers.cosyvoice_local import CosyVoiceLocalProvider
from app.providers.fish_cloud import FishCloudProvider
from app.providers.mock import MockProvider
from app.store import config as config_store

logger = logging.getLogger("vss.providers")

# 注册表：新增引擎 = 加一行 + 实现类
_REGISTRY = {
    "fish_cloud": FishCloudProvider,
    "mock": MockProvider,
    "cosyvoice_local": CosyVoiceLocalProvider,   # M3：本地 CosyVoice3（离线）
    # 预留位（见 docs/ARCHITECTURE.md 里程碑）：
    # "mlx_fish_s2pro": MlxFishS2ProProvider,
    # "mlx_qwen_tts":  MlxQwenTtsProvider,
}


def get_provider(provider_id: str) -> TTSProvider:
    """按 id 构造 provider 实例（读取当前配置）。未启用或未知 id 直接报错。"""
    cls = _REGISTRY.get(provider_id)
    if cls is None:
        raise KeyError(f"未知 provider: {provider_id}")
    cfg = config_store.load_config()
    prov_cfg = cfg["providers"].get(provider_id)
    if prov_cfg is None:
        raise KeyError(f"配置缺失: providers.{provider_id}")
    if not prov_cfg.get("enabled", False):
        raise PermissionError(f"provider 未启用: {provider_id}（请在引擎设置页开启）")
    return cls(prov_cfg)


def list_available() -> List[Dict]:
    """列出所有已注册引擎（供设置页展示；key 掩码）。"""
    cfg = config_store.load_config()
    out = []
    for pid in _REGISTRY:
        prov_cfg = cfg["providers"].get(pid, {})
        item: Dict = {
            "id": pid,
            "enabled": bool(prov_cfg.get("enabled", False)),
        }
        try:
            inst = _REGISTRY[pid](prov_cfg)
            caps = inst.capabilities()
            item.update({
                "kind": caps.kind,
                "display_name": caps.display_name,
                "supports_emotion_tags": caps.supports_emotion_tags,
                "has_preset_voices": caps.has_preset_voices,
                "max_concurrency": caps.max_concurrency,
                "notes": caps.notes,
                "implemented": True,
            })
            if pid == "fish_cloud":
                item["model"] = prov_cfg.get("model", "s2.1-pro-free")
                item["proxy"] = prov_cfg.get("proxy", "")
                item["api_key_masked"] = config_store.mask_key(
                    config_store.get_fish_api_key({"providers": {"fish_cloud": prov_cfg}}))
                item["api_key_configured"] = bool(item["api_key_masked"])
        except Exception as e:
            # 单个引擎构造失败不影响其他引擎展示
            item["error"] = str(e)
            item["implemented"] = False
        out.append(item)
    return out
