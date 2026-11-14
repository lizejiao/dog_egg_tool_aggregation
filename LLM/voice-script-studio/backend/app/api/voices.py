"""音色 API：预置音色列举、上传克隆、注册、试听、删除。

试听机制（docs/ARCHITECTURE.md §4.5）：
- POST /api/voices/preview {provider_id, voice_key} → 固定示例句**只真实合成一次**，
  缓存为 data/voices/previews/<hash>.wav，返回回放 URL
- GET  /api/voices/preview/<hash>.wav → 静态回放缓存（支持 Range，可拖动）
（实现与文档 §5 的路径略有出入：预置音色键是引擎相关的，API 需同时携带
 provider_id，故合并为独立子路由——行为语义与文档一致）
"""
import hashlib
import logging
import os
import time
from typing import Any, Dict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import settings
from app.providers import get_provider
from app.providers.base import SynthParams, TTSProviderError, strip_or_keep_tags
from app.store import config as config_store
from app.store import db

logger = logging.getLogger("vss.api.voices")
router = APIRouter(prefix="/api/voices", tags=["voices"])

# 固定试听示例句：中英混读 + 情绪标签（标签按引擎能力自动剥离）
_PREVIEW_TEXT = "大家好，这是一段试听示例。Hello, this is a voice preview. [pause] 感谢试听。"

# 预置音色结果缓存：{provider_id: (timestamp, data)}，TTL 60 秒。
# 避免每次刷新页面都打一遍云端接口（免费档限流保护）。
_PRESET_CACHE: Dict[str, Any] = {}
_PRESET_TTL = 60


# ---------------------------------------------------------------- 列举

@router.get("")
def list_voices(refresh: bool = False) -> Dict:
    """音色总表：克隆音色（本地 DB）+ 各启用引擎的预置音色（实时+缓存）。

    refresh=true 时清空预置音色的 60s TTL 缓存、强制回源拉取
    （网页端刚创建的音色无需等缓存过期即可同步到本地）。
    """
    if refresh:
        _PRESET_CACHE.clear()
    clones = []
    for v in db.list_voices():
        clones.append({
            "id": v["id"], "name": v["name"], "ref_text": v["ref_text"],
            "registrations": v["registrations"], "status": v["status"],
        })
    return {"clones": clones, "presets": _load_presets()}


def _load_presets() -> Dict[str, list]:
    """各启用引擎的预置音色（带 60s TTL 缓存）。引擎异常时降级为错误信息。"""
    cfg = config_store.load_config()
    out: Dict[str, list] = {}
    for pid, prov_cfg in cfg["providers"].items():
        if not prov_cfg.get("enabled"):
            continue
        try:
            provider = get_provider(pid)
        except (KeyError, PermissionError):
            continue  # 注册表中无实现或未启用 → 跳过
        cached = _PRESET_CACHE.get(pid)
        if cached and time.time() - cached[0] < _PRESET_TTL:
            out[pid] = cached[1]
            continue
        try:
            caps = provider.capabilities()
            if not caps.has_preset_voices:
                out[pid] = []
            else:
                out[pid] = [
                    {"voice_key": m.voice_key, "name": m.name, "state": m.state}
                    for m in provider.list_preset_voices()
                ]
        except TTSProviderError as e:
            out[pid] = {"error": str(e)}  # 例如 apiKey 未配置：页面显示原因而非崩溃
        _PRESET_CACHE[pid] = (time.time(), out[pid])
    return out


# ---------------------------------------------------------------- 上传克隆

@router.post("")
async def create_voice(name: str = Form(...), ref_text: str = Form(...),
                       audio: UploadFile = File(...)) -> Dict:
    """上传参考音频 + 逐字稿，创建克隆音色并自动注册到已启用引擎。"""
    ref_text = ref_text.strip()
    if not ref_text:
        raise HTTPException(400, "逐字稿必填（与录音内容一字不差，克隆质量的第一要素）")
    if not name.strip():
        raise HTTPException(400, "音色名称必填")

    # 文件白名单 + 大小校验
    ext = os.path.splitext(audio.filename or "")[1].lower()
    if ext not in settings.VOICE_EXTS:
        raise HTTPException(400, f"仅支持 {'/'.join(sorted(settings.VOICE_EXTS))}，收到 {ext or '无扩展名'}")
    data = await audio.read()
    if len(data) > settings.MAX_VOICE_BYTES:
        raise HTTPException(400, f"参考音频超过 {settings.MAX_VOICE_BYTES // 1024 // 1024}MB 上限")
    if len(data) < 1024:
        raise HTTPException(400, "参考音频过小，请上传有效录音")

    # 归档：原名保存（云端支持 wav/mp3/m4a/opus，直接上传原文件）
    ref_path = os.path.join(settings.VOICES_DIR, f"{db.new_id()}{ext}")
    with open(ref_path, "wb") as f:
        f.write(data)

    vid = db.create_voice(name.strip(), ref_path, ref_text)
    registrations = {}
    register_errors = {}

    # 自动注册到所有已启用且需要注册的引擎（失败不回滚：用户可稍后补注册）
    cfg = config_store.load_config()
    for pid in cfg["providers"]:
        try:
            provider = get_provider(pid)
        except (KeyError, PermissionError):
            continue
        try:
            meta = provider.register_clone_voice(ref_path, ref_text, name.strip())
            # 本地引用型引擎（如 cosyvoice_local）回传占位 key，
            # 统一覆写为本地 uuid——runner 按此 key 查库拿参考音频
            key = meta.voice_key
            if key.startswith("local_ref:"):
                key = f"clone:{vid}"
            registrations[pid] = {"voice_key": key, "state": meta.state}
        except TTSProviderError as e:
            register_errors[pid] = str(e)  # 如 apiKey 未配置——明确告知而非失败
    db.update_voice_registrations(vid, registrations)

    logger.info("创建克隆音色 %s(%s)，注册 %s", name, vid, list(registrations))
    return {"id": vid, "name": name.strip(), "registrations": registrations,
            "register_errors": register_errors}


@router.get("/{vid}/audio")
def download_ref_audio(vid: str) -> FileResponse:
    """下载克隆音色的归档参考音频。

    用途：Fish 免费 API 档无音色创建配额（0/0）时，用户可在 fish.audio 网页端
    「创建音色」上传同一段录音（网页版免费含 3 个槽位），创建后归属账号、
    本地引擎预置音色会自动同步——本端点让归档的录音可以取出来复用。
    """
    voice = db.get_voice(vid)
    if voice is None:
        raise HTTPException(404, "音色不存在")
    if not os.path.isfile(voice["ref_path"]):
        raise HTTPException(404, "参考音频文件缺失")
    ext = os.path.splitext(voice["ref_path"])[1] or ".wav"
    return FileResponse(voice["ref_path"], filename=f"{voice['name']}{ext}",
                        media_type="application/octet-stream")


@router.post("/{vid}/register/{provider_id}")
def register_voice(vid: str, provider_id: str) -> Dict:
    """把克隆音色补注册到指定引擎（失败原因以 400 返回，如配额不足/网络）。"""
    voice = db.get_voice(vid)
    if voice is None:
        raise HTTPException(404, "音色不存在")
    provider = get_provider(provider_id)
    try:
        meta = provider.register_clone_voice(
            voice["ref_path"], voice["ref_text"], voice["name"])
    except TTSProviderError as e:
        raise HTTPException(400, str(e))
    key = meta.voice_key
    if key.startswith("local_ref:"):   # 本地引用型引擎 → 回写本地 uuid
        key = f"clone:{vid}"
    regs = dict(voice["registrations"])
    regs[provider_id] = {"voice_key": key, "state": meta.state}
    db.update_voice_registrations(vid, regs)
    return {"registrations": regs}


@router.delete("/{vid}")
def delete_voice(vid: str) -> Dict:
    """删除克隆音色：引擎侧模型尽力删除 + 本地文件清理 + 软删除。"""
    voice = db.get_voice(vid)
    if voice is None:
        raise HTTPException(404, "音色不存在")
    delete_errors = {}
    for pid, reg in voice["registrations"].items():
        try:
            get_provider(pid).delete_voice(reg["voice_key"])
        except (Exception,) as e:  # 引擎侧删除尽力而为，失败提示用户手动清理
            delete_errors[pid] = str(e)
    if os.path.isfile(voice["ref_path"]):
        os.unlink(voice["ref_path"])
    db.delete_voice(vid)
    return {"deleted": vid, "delete_errors": delete_errors}


# ---------------------------------------------------------------- 试听

@router.post("/preview")
def make_preview(provider_id: str, voice_key: str) -> Dict:
    """生成试听（同音色只真实合成一次，之后回放缓存）。"""
    provider = get_provider(provider_id)
    caps = provider.capabilities()
    text = strip_or_keep_tags(_PREVIEW_TEXT, caps, SynthParams())
    cache_id = hashlib.sha1(f"{provider_id}:{voice_key}".encode()).hexdigest()[:16]
    out_path = os.path.join(settings.PREVIEWS_DIR, f"{cache_id}.wav")

    if os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
        return {"preview_url": f"/api/voices/preview/{cache_id}.wav", "cached": True}

    # 克隆音色先解析到引擎侧 key（预置音色原样透传）
    engine_key = voice_key
    if voice_key.startswith("clone:"):
        voice = db.get_voice(voice_key[len("clone:"):])
        if voice is None:
            raise HTTPException(404, "克隆音色不存在")
        reg = voice["registrations"].get(provider_id)
        if not reg or not reg.get("voice_key"):
            raise HTTPException(400, f"该音色尚未注册到 {provider_id}")
        engine_key = reg["voice_key"]

    try:
        provider.synthesize(text, engine_key, out_path, SynthParams())
    except TTSProviderError as e:
        raise HTTPException(502, str(e))
    return {"preview_url": f"/api/voices/preview/{cache_id}.wav", "cached": False}


@router.get("/preview/{filename}")
def get_preview(filename: str) -> FileResponse:
    """回放试听缓存。文件名仅接受本服务生成的 hash 形式（防路径穿越）。"""
    if "/" in filename or ".." in filename or not filename.endswith(".wav"):
        raise HTTPException(400, "非法文件名")
    path = os.path.join(settings.PREVIEWS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "试听缓存不存在，请先 POST /api/voices/preview")
    return FileResponse(path, media_type="audio/wav")
