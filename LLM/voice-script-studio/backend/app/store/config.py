"""配置与密钥管理。

- data/config.json 承载所有 Provider 配置与合成参数，权限 0600（含 apiKey，绝不入 git）
- 环境变量优先级高于配置文件（FISH_API_KEY 优先），便于 CI / CLI 场景
- 提供 apiKey 掩码与日志脱敏工具，任何日志输出前必须过 sanitize()
"""
import json
import logging
import os
import tempfile
from typing import Any, Dict

from app import settings

logger = logging.getLogger("vss.config")

# 默认配置：结构与 docs/ARCHITECTURE.md §4.6 保持一致
DEFAULT_CONFIG: Dict[str, Any] = {
    "providers": {
        # 云端 Fish（REST 直调，不用官方 SDK，端点见 docs/ARCHITECTURE.md）
        "fish_cloud": {
            "enabled": True,
            "api_key": "",                    # 留空 = 未配置，WebUI 设置页填入
            "model": "s2.1-pro-free",         # 可切 s2.1-pro（付费档）
            "base_url": "https://api.fish.audio",
            "proxy": "",                      # 出网代理，如 http://127.0.0.1:7890（留空走环境变量）
            "timeout_s": 60,                  # 单段合成超时
            "max_concurrency": 2,             # 免费档新账号限流 5，保守取 2
        },
        # 本地 mock（离线自测，无网络无模型也能跑全流程）
        "mock": {
            "enabled": True,
        },
        # 本地 CosyVoice3（M3：离线免费，Apache-2.0 可商用）
        "cosyvoice_local": {
            "enabled": True,
            "fp16": False,               # 半精度（更快，16G 内存建议后续开启试测）
            "timeout_s": 600,            # 单段合成超时（本地慢于实时，给足）
            "idle_kill_s": 300,          # 空闲多久回收 worker 释放内存
        },
    },
    # 合成默认参数（任务级可覆盖）
    "synthesis": {
        "chars_per_segment": 80,       # 分句目标上限
        "pause_between": 0.35,        # 同段内句间停顿（秒）
        "pause_between_paragraphs": 0.8,  # 段落间停顿（秒）
        "max_retries": 3,             # 段级可重试错误的最大重试次数
    },
}


def load_config() -> Dict[str, Any]:
    """读取配置；文件不存在或损坏时写入默认配置并返回。"""
    settings.ensure_dirs()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.isfile(settings.CONFIG_PATH):
        try:
            with open(settings.CONFIG_PATH, encoding="utf-8") as f:
                stored = json.load(f)
            _deep_merge(cfg, stored)
        except (json.JSONDecodeError, OSError) as e:
            # 配置损坏不致命：备份后重建
            logger.warning("config.json 读取失败(%s)，已重建默认配置", e)
            os.replace(settings.CONFIG_PATH, settings.CONFIG_PATH + ".bak")
    else:
        save_config(cfg)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """原子写入配置并强制 0600 权限（防 race + 防泄漏）。"""
    settings.ensure_dirs()
    fd, tmp = tempfile.mkstemp(dir=settings.DATA_DIR, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, settings.CONFIG_PATH)  # 原子替换
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def update_provider_config(provider_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """更新单个 Provider 配置（设置页调用），只允许白名单字段。"""
    cfg = load_config()
    prov = cfg["providers"].get(provider_id)
    if prov is None:
        raise KeyError(f"未知 provider: {provider_id}")
    for key in ("enabled", "api_key", "model", "base_url", "proxy", "timeout_s",
                "max_concurrency", "fp16", "idle_kill_s"):
        if key in patch:
            prov[key] = patch[key]
    save_config(cfg)
    return cfg


# 全局合成参数：字段 → (类型, 最小值, 最大值)
SYNTHESIS_RULES = {
    "chars_per_segment": (int, 30, 200),
    "max_retries": (int, 1, 5),
    "pause_between": (float, 0.0, 2.0),
    "pause_between_paragraphs": (float, 0.0, 3.0),
}


def update_synthesis_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """更新全局合成参数（设置页调用）。越界或类型非法抛 ValueError。"""
    cfg = load_config()
    synth = cfg["synthesis"]
    for key, (typ, lo, hi) in SYNTHESIS_RULES.items():
        if key not in patch or patch[key] is None:
            continue
        try:
            val = typ(patch[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} 需要数字")
        if not lo <= val <= hi:
            raise ValueError(f"{key} 超出范围（{lo}~{hi}）")
        synth[key] = val
    save_config(cfg)
    return cfg["synthesis"]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    """递归合并：override 中的键覆盖 base，未知顶层键忽略（防配置污染）。"""
    for key, val in override.items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def get_fish_api_key(cfg: Dict[str, Any]) -> str:
    """取 Fish apiKey：环境变量 > 配置文件。"""
    env = os.environ.get("FISH_API_KEY", "")
    if env:
        return env.strip()
    return str(cfg["providers"]["fish_cloud"].get("api_key", "") or "").strip()


def mask_key(key: str) -> str:
    """apiKey 掩码显示：只露末 4 位。"""
    if not key:
        return ""
    return "****" + key[-4:]


def sanitize(text: str, *secrets: str) -> str:
    """日志/错误信息脱敏：替换传入的敏感串与 FISH_API_KEY 环境变量。

    用法约定：持有 apiKey 的调用方（provider、API 层）把自己手上的 key 传进来，
    本函数不做隐式配置读取，保持纯函数、可测试。
    """
    out = str(text)
    for sec in secrets:
        if sec and len(sec) >= 8:
            out = out.replace(sec, "***")
    env_key = os.environ.get("FISH_API_KEY", "")
    if env_key and len(env_key) >= 8:
        out = out.replace(env_key, "***")
    return out
