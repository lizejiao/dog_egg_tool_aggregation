"""音色 API：预置音色列举、上传素材、注册（→音色实体）、试听、删除。

两级实体模型（P2-0，docs/UI_REDESIGN.md §4.2）：
- 素材（voices 表）：录音/上传的原材料，POST /api/voices 只做「保存素材」
- 音色（voice_entities 表）：素材注册引擎后的产物，自有名称、同引擎唯一
  注册入口收敛为 POST /api/voices/{vid}/register/{pid}（可指定音色名）
  + DELETE /api/voice-entities/{eid}（删音色不删素材）
- registrations JSON 仍由注册路径双写（旧前端 frontend/ P3 归档前依赖）

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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import settings
from app.providers import get_provider
from app.providers.base import SynthParams, TTSProviderError, strip_or_keep_tags
from app.store import config as config_store
from app.store import db

logger = logging.getLogger("vss.api.voices")
router = APIRouter(prefix="/api/voices", tags=["voices"])
# 音色实体独立子路由（P2-0）：名称即使用说明，与素材端点分离
ent_router = APIRouter(prefix="/api/voice-entities", tags=["voices"])

# 固定试听示例句：中英混读 + 情绪标签（标签按引擎能力自动剥离）
_PREVIEW_TEXT = "大家好，这是一段试听示例。Hello, this is a voice preview. [pause] 感谢试听。"

# 预置音色结果缓存：{provider_id: (timestamp, data)}，TTL 60 秒。
# 避免每次刷新页面都打一遍云端接口（免费档限流保护）。
_PRESET_CACHE: Dict[str, Any] = {}
_PRESET_TTL = 60


# ---------------------------------------------------------------- 列举

@router.get("")
def list_voices(refresh: bool = False) -> Dict:
    """音色总表：素材（本地 DB）+ 各启用引擎的预置音色（实时+缓存）+ 音色实体。

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
    return {"clones": clones,
            "voice_entities": db.list_voice_entities(),
            "presets": _load_presets()}


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
                       audio: UploadFile = File(...),
                       register_provider: str = Form("cosyvoice_local")) -> Dict:
    """创建声音素材并注册到「单个」引擎（单动作-单结局语义）。

    register_provider：
        引擎 id（fish_cloud / cosyvoice_local / mock）→ 注册失败则整体回滚
        （归档文件删除、不落库），返回 400 + 失败原因；
        "none" → 仅存入声音素材库（未注册，不可用于合成，可稍后补注册）。
    前置校验（按最严格引擎 CosyVoice3 收敛）：音频 ≤30 秒。
    """
    import wave as _wave

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

    # 时长校验（≤30s，取最严格引擎上限；wav 直接读头，其他格式跳过——
    # 注册路径（CosyVoice3）会在 worker 内再断言一次，双保险）
    if ext == ".wav":
        try:
            import io as _io
            with _wave.open(_io.BytesIO(data), "rb") as w:
                dur = w.getnframes() / max(w.getframerate(), 1)
            if dur > 30.0:
                raise HTTPException(
                    400, f"参考音频 {dur:.1f} 秒，超过 30 秒上限"
                         "（CosyVoice3 要求 ≤30 秒）——请截短或重录后再提交")
        except _wave.Error:
            pass  # 非法 wav 交由后续读取报错

    # 归档：原名保存（云端支持 wav/mp3/m4a/opus，直接上传原文件）
    ref_path = os.path.join(settings.VOICES_DIR, f"{db.new_id()}{ext}")
    with open(ref_path, "wb") as f:
        f.write(data)

    def _rollback() -> None:
        try:
            if os.path.isfile(ref_path):
                os.unlink(ref_path)
        except OSError:
            logger.warning("回滚清理参考音频失败：%s", ref_path)

    # 仅存素材库（不注册、不可合成；可稍后在音色卡补注册）
    if register_provider == "none":
        vid = db.create_voice(name.strip(), ref_path, ref_text)
        logger.info("声音素材入库 %s(%s)（未注册引擎）", name, vid)
        return {"id": vid, "name": name.strip(), "registrations": {},
                "register_errors": {}, "material_only": True}

    # 单引擎注册：失败 → 整体回滚（素材不落库，文件删除）
    try:
        provider = get_provider(register_provider)
    except (KeyError, PermissionError) as e:
        _rollback()
        raise HTTPException(400, f"引擎不可用（{e}），音色未保存——可改选其他引擎或「仅存素材库」")
    vid = db.create_voice(name.strip(), ref_path, ref_text)
    try:
        meta = provider.register_clone_voice(ref_path, ref_text, name.strip())
    except TTSProviderError as e:
        db.delete_voice(vid)
        _rollback()
        raise HTTPException(
            400, f"注册到 {register_provider} 失败：{e}。音色未保存——"
                 "可更换引擎（推荐 CosyVoice3 本地）重试，或选「仅保存到声音素材库」")
    key = meta.voice_key
    if key.startswith("local_ref:"):   # 本地引用型引擎 → 回写本地 uuid
        key = f"clone:{vid}"
    # P2-0：同步产音色实体（名称缺省=素材名，自动避让重名；旧前端无感）
    ent = _upsert_registration_entity(vid, register_provider, key, meta.state, None)
    logger.info("创建音色 %s(%s)，注册 %s ✓（音色实体 %s）",
                name, vid, register_provider, ent["id"])
    return {"id": vid, "name": name.strip(),
            "registered_provider": register_provider,
            "registrations": {register_provider: {"voice_key": key, "state": meta.state}},
            "voice_entity": ent,
            "register_errors": {}, "material_only": False}


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


class RegisterBody(BaseModel):
    """注册新音色请求体（P2-0）。voice_name 缺省时沿用素材名（自动避让重名）。"""
    voice_name: Optional[str] = None


def _auto_entity_name(engine: str, base: str) -> str:
    """自动避让同引擎重名（追加 " 2"/" 3"...）。供旧版/缺省路径使用。"""
    taken = {e["name"] for e in db.list_voice_entities(engine)}
    name = (base or "").strip() or "未命名音色"
    if name not in taken:
        return name
    i = 2
    while f"{name} {i}" in taken:
        i += 1
    return f"{name} {i}"


def _upsert_registration_entity(vid: str, provider_id: str, key: str,
                                state: str,
                                voice_name: Optional[str]) -> Dict[str, Any]:
    """注册成功后的落库：registrations 双写（旧前端）+ 音色实体（新模型）。

    - voice_name 显式指定：重名抛 VoiceNameTaken → API 层 409
    - 缺省：沿用已有实体名或素材名，自动避让重名（旧前端兼容）
    - 同素材×同引擎已存在音色 → 覆盖（对齐旧版重复注册的覆盖语义）
    返回新建的音色实体。
    """
    voice = db.get_voice(vid)
    regs = dict(voice["registrations"])
    regs[provider_id] = {"voice_key": key, "state": state}
    db.update_voice_registrations(vid, regs)

    # 覆盖语义：同素材×同引擎的既有音色先软删（保留名称时优先沿用）
    old = [e for e in db.list_voice_entities(provider_id)
           if e["material_id"] == vid]
    keep_name = old[0]["name"] if old else None
    for e in old:
        db.delete_voice_entity(e["id"])

    if voice_name:
        ent = db.create_voice_entity(voice_name, provider_id, vid, key, state)
    else:
        base = keep_name or voice["name"]
        ent = db.create_voice_entity(
            _auto_entity_name(provider_id, base), provider_id, vid, key, state)
    return ent


@router.post("/{vid}/register/{provider_id}")
def register_voice(vid: str, provider_id: str,
                   body: Optional[RegisterBody] = None) -> Dict:
    """注册新音色：素材 → 引擎（唯一注册入口，P2-0 起产音色实体）。

    body.voice_name：音色自有名称，同引擎内唯一（重名 409）；
    缺省沿用素材名（自动避让）。失败原因以 400 返回（配额不足/网络等）。
    """
    voice = db.get_voice(vid)
    if voice is None:
        raise HTTPException(404, "素材不存在")
    provider = get_provider(provider_id)
    try:
        meta = provider.register_clone_voice(
            voice["ref_path"], voice["ref_text"], voice["name"])
    except TTSProviderError as e:
        raise HTTPException(400, str(e))
    key = meta.voice_key
    if key.startswith("local_ref:"):   # 本地引用型引擎 → 回写本地 uuid
        key = f"clone:{vid}"
    try:
        ent = _upsert_registration_entity(
            vid, provider_id, key, meta.state,
            ((body.voice_name if body else "") or "").strip() or None)
    except db.VoiceNameTaken as e:
        raise HTTPException(409, str(e))
    logger.info("注册音色 %s@%s → %s（音色实体 %s）",
                ent["name"], provider_id, key, ent["id"])
    return {"voice_entity": ent, "registrations": db.get_voice(vid)["registrations"]}


# ---------------------------------------------------------------- 音色实体（P2-0）

@ent_router.get("")
def list_entities(engine: Optional[str] = None) -> Dict:
    """音色列表（素材库「音色管理」引擎页签用；?engine= 过滤）。"""
    return {"voice_entities": db.list_voice_entities(engine)}


@ent_router.delete("/{eid}")
def delete_entity(eid: str) -> Dict:
    """删除音色（引擎侧尽力删除 + 软删实体；**素材不受影响**）。"""
    ent = db.get_voice_entity(eid)
    if ent is None:
        raise HTTPException(404, "音色不存在")
    delete_errors = {}
    try:
        get_provider(ent["engine"]).delete_voice(ent["voice_key"])
    except Exception as e:  # noqa: BLE001 —— 引擎侧删除尽力而为
        delete_errors[ent["engine"]] = str(e)
    db.delete_voice_entity(eid)
    # registrations 双写同步：该引擎条目若指向同一 key 则移除（旧前端依赖）
    voice = db.get_voice(ent["material_id"])
    if voice:
        regs = dict(voice["registrations"])
        reg = regs.get(ent["engine"])
        if reg and reg.get("voice_key") == ent["voice_key"]:
            regs.pop(ent["engine"])
            db.update_voice_registrations(ent["material_id"], regs)
    logger.info("删除音色 %s(%s@%s)", ent["name"], eid, ent["engine"])
    return {"deleted": eid, "delete_errors": delete_errors}


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
    # 素材删除 → 其音色实体一并软删（引擎侧模型已按 registrations 尽力删除）
    for e in db.list_voice_entities():
        if e["material_id"] == vid:
            db.delete_voice_entity(e["id"])
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

    # 本地引擎暖机门禁：试听也是使用，未暖机一律拒绝（绝不静默加载模型）
    if provider_id == "cosyvoice_local":
        from app.providers.cosyvoice_local import warm_info
        if warm_info()["warm_state"] != "ready":
            raise HTTPException(
                409, "本地引擎未暖机：请到 设置 → 暖机引擎 后再试听")

    # 克隆音色先解析到引擎侧 key（预置音色原样透传）
    # voice_key 三种形态：preset:xxx（透传）/ clone:<素材id>（查 registrations）/
    #                     voiceent:<音色id>（P2-0 新模型：查音色实体表）
    engine_key = voice_key
    if voice_key.startswith("voiceent:"):
        ent = db.get_voice_entity(voice_key[len("voiceent:"):])
        if ent is None:
            raise HTTPException(404, "音色不存在（可能已被删除）")
        if ent["engine"] != provider_id:
            raise HTTPException(400, f"该音色归属引擎 {ent['engine']}，与试听引擎不符")
        engine_key = ent["voice_key"]
    elif voice_key.startswith("clone:"):
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
        # NotWarmedError 是 TTSProviderError 子类 → 409 引导暖机；其余 502
        from app.providers.cosyvoice_local import NotWarmedError
        raise HTTPException(409 if isinstance(e, NotWarmedError) else 502, str(e))
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
