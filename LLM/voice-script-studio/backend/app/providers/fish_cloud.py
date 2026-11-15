"""Fish Audio 云端 Provider：REST 直调（不依赖官方 SDK，端点行为完全透明）。

端点均为官方公开 API（2026-11-14 查证，见 docs/ARCHITECTURE.md 与 Fish 官方 OpenAPI）：
  - TTS：    POST {base}/v1/tts   头: Authorization/model/Content-Type，体 JSON
  - 建音色：  POST {base}/model    multipart：type=tts, train_mode=fast, voices, texts
  - 列音色：  GET  {base}/model?self=true&page_size=&page_number=
  - 删音色：  DELETE {base}/model/{id}

voice_key 约定（本 provider 的解析规则）：
  - "preset:<model_id>" —— 云端平台音色（含网页端创建的）
  - "clone:<model_id>"  —— 经本应用注册的克隆音色（注册后与预置同源，都是平台模型）
  两种前缀在引擎侧无差别：后缀一律作为 reference_id 传入 TTS 请求。
  本地克隆 uuid → model_id 的解析由任务引擎经 DB registrations 完成，provider 不碰 DB。

重试策略：本 provider 不做重试，只负责把错误分类（retryable 与否）抛给
任务引擎统一退避重试，避免两层重试叠加放大请求量。
"""
import logging
import time
from typing import Any, Dict, List

import requests

from app.providers.base import (
    Caps, SynthParams, TTSProvider, TTSProviderError, VoiceMeta,
)
from app.store import config as config_store

logger = logging.getLogger("vss.providers.fish")

# 逐字稿随音色上传可提升克隆相似度（官方文档建议），enhance 开启降噪归一
_CREATE_VOICE_DEFAULTS = {
    "type": "tts",
    "train_mode": "fast",          # fast = 创建后立即可用（state 常直接为 trained）
    "visibility": "private",       # 私有，只对自己可见
}


class FishCloudProvider(TTSProvider):
    """Fish Audio 云端引擎（s2.1-pro-free / s2.1-pro）。"""

    id = "fish_cloud"

    def __init__(self, cfg: Dict[str, Any]):
        """cfg 即 config.providers.fish_cloud；apiKey 解析顺序：环境变量 > 配置文件。"""
        self.api_key = config_store.get_fish_api_key({"providers": {"fish_cloud": cfg}})
        self.model = cfg.get("model", "s2.1-pro-free")
        self.base_url = str(cfg.get("base_url", "https://api.fish.audio")).rstrip("/")
        self.timeout_s = int(cfg.get("timeout_s", 60))
        self.max_concurrency = max(1, int(cfg.get("max_concurrency", 2)))
        # 出网代理（如 http://127.0.0.1:7890）。留空时 requests 自动读环境变量；
        # 显式配置可避免环境变量指向不可用代理（沙箱/公司网关常见坑）。
        self.proxy = str(cfg.get("proxy") or "").strip()

    # ------------------------------------------------------------ 内部工具

    def _proxies(self):
        """requests proxies 参数：配置了代理则固定走它，否则交给环境变量。"""
        if self.proxy:
            return {"http": self.proxy, "https": self.proxy}
        return None

    def _require_key(self) -> None:
        if not self.api_key:
            raise TTSProviderError(
                "Fish apiKey 未配置，请在 WebUI「引擎设置」页填写", retryable=False, status=401)

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _classify_http_error(self, resp: requests.Response) -> TTSProviderError:
        """HTTP 错误 → 可重试性分类（429/5xx 可重试，4xx 不可重试）。"""
        safe_body = resp.text[:300] if resp.text else ""
        retryable = resp.status_code == 429 or resp.status_code >= 500
        return TTSProviderError(
            f"Fish HTTP {resp.status_code}: {safe_body}",
            retryable=retryable, status=resp.status_code)

    def _classify_network_error(self, exc: Exception) -> TTSProviderError:
        """网络层错误（超时/连接失败）一律可重试。"""
        msg = config_store.sanitize(f"Fish 网络错误: {exc}", self.api_key)
        return TTSProviderError(msg, retryable=True, status=0)

    # ------------------------------------------------------------ 能力与检查

    def capabilities(self) -> Caps:
        return Caps(
            kind="cloud",
            display_name=f"Fish 云端（{self.model}）",
            max_concurrency=self.max_concurrency,
            supports_emotion_tags=True,   # [pause]/[excited] 等 15000+ 官方标签
            sample_rate_hz=44100,         # Fish 输出 44.1kHz
            has_preset_voices=True,       # 云端音色库（self=true 列举）
            needs_registration=True,      # 克隆音色需先 POST /model 注册
            supported_params={"speed": True, "volume": True},
            # （官方 OpenAPI：prosody.speed 0.5~2.0 倍数；prosody.volume 为 dB 偏移 -20~20）
            notes="免费档无 SLA、请求可能用于改进模型；切付费仅改模型串",
        )

    def test_connection(self) -> Dict[str, Any]:
        """用最小请求（列 1 个音色）验证鉴权与连通。"""
        self._require_key()
        try:
            resp = requests.get(
                f"{self.base_url}/model",
                params={"self": "true", "page_size": 1},
                headers=self._auth_headers(),
                timeout=min(self.timeout_s, 15),
                proxies=self._proxies(),
            )
        except requests.RequestException as e:
            return {"ok": False, "message": config_store.sanitize(str(e), self.api_key)}
        if resp.status_code == 200:
            return {"ok": True, "message": "鉴权与连接正常"}
        return {"ok": False, "message": f"HTTP {resp.status_code}（检查 apiKey 与模型串）"}

    # ------------------------------------------------------------ 音色管理

    def list_preset_voices(self) -> List[VoiceMeta]:
        """分页拉取云端平台上属于本账号的音色（self=true）。"""
        self._require_key()
        out: List[VoiceMeta] = []
        page_number = 1
        while True:
            try:
                resp = requests.get(
                    f"{self.base_url}/model",
                    params={"self": "true", "page_size": 50, "page_number": page_number},
                    headers=self._auth_headers(),
                    timeout=min(self.timeout_s, 15),
                    proxies=self._proxies(),
                )
            except requests.RequestException as e:
                raise self._classify_network_error(e)
            if resp.status_code != 200:
                raise self._classify_http_error(resp)
            data = resp.json()
            for item in data.get("items", []):
                model_id = item.get("_id", "")
                if not model_id:
                    continue
                out.append(VoiceMeta(
                    voice_key=f"preset:{model_id}",
                    name=item.get("title") or "未命名音色",
                    state=item.get("state", "trained"),
                ))
            if not data.get("has_more") or len(out) >= 500:
                break
            page_number += 1
        logger.info("fish 列举云端音色 %d 个", len(out))
        return out

    def register_clone_voice(self, ref_path: str, ref_text: str,
                             name: str) -> VoiceMeta:
        """上传参考音频+逐字稿到云端，创建持久音色模型。"""
        self._require_key()
        with open(ref_path, "rb") as f:
            files = {"voices": (ref_path.rsplit("/", 1)[-1], f, "application/octet-stream")}
            data = dict(_CREATE_VOICE_DEFAULTS)
            data["title"] = name
            data["texts"] = ref_text              # 逐字稿成对上传，提升相似度
            data["enhance_audio_quality"] = "true"  # 降噪归一（录音不干净时友好）
            try:
                resp = requests.post(
                    f"{self.base_url}/model",
                    data=data, files=files,
                    headers=self._auth_headers(),
                    timeout=self.timeout_s,
                    proxies=self._proxies(),
                )
            except requests.RequestException as e:
                raise self._classify_network_error(e)
        if resp.status_code not in (200, 201):
            # 免费档特有错误：API 侧创建音色模型配额为 0（与网页版 3 个免费槽位是两套配额）
            if resp.status_code == 403 and "model creation limit" in resp.text.lower():
                raise TTSProviderError(
                    "免费 API 档没有创建音色模型的配额（0/0）。两条路任选："
                    "① 到 fish.audio 网页端「创建音色」上传同一段录音与逐字稿"
                    "（网页版免费含 3 个音色槽位，创建后本地会自动同步到引擎预置音色）；"
                    "② 到开发者平台充值 API 额度后，在音色卡上点「补注册」重试。",
                    retryable=False, status=403)
            raise self._classify_http_error(resp)
        payload = resp.json()
        model_id = payload.get("_id", "")
        state = payload.get("state", "training")
        if not model_id:
            raise TTSProviderError(
                f"Fish 返回体缺少 _id: {str(payload)[:200]}", retryable=False)
        logger.info("fish 注册克隆音色 %s -> model %s (state=%s)",
                    name, model_id, state)
        return VoiceMeta(voice_key=f"clone:{model_id}", name=name, state=state)

    def delete_voice(self, voice_key: str) -> None:
        """删除云端模型（音色从本地移除时联动调用）。"""
        self._require_key()
        model_id = voice_key.split(":", 1)[1] if ":" in voice_key else voice_key
        try:
            resp = requests.delete(
                f"{self.base_url}/model/{model_id}",
                headers=self._auth_headers(),
                timeout=min(self.timeout_s, 15),
                proxies=self._proxies(),
            )
        except requests.RequestException as e:
            raise self._classify_network_error(e)
        # 删除是尽力而为：404 视为已删，其余失败上抛（调用方提示用户手动清理）
        if resp.status_code not in (200, 204, 404):
            raise self._classify_http_error(resp)

    # ------------------------------------------------------------ 合成

    def synthesize(self, text: str, voice_key: str,
                   out_path: str, params: SynthParams) -> Dict[str, Any]:
        """单段合成。请求 WAV 格式，便于拼接层统一处理（无需 mp3 解码）。"""
        self._require_key()
        reference_id = voice_key.split(":", 1)[1] if ":" in voice_key else voice_key
        body: Dict[str, Any] = {
            "text": text,
            "reference_id": reference_id,
            "format": "wav",
            "normalize": True,            # 响度归一（官方字段）
        }
        # 韵律参数：只在用户改过默认值时下发（省 token & 避免服务端差异）
        prosody: Dict[str, Any] = {}
        if abs(params.speed - 1.0) > 0.01:
            prosody["speed"] = round(params.speed, 2)
        if abs(params.volume_db) > 0.05:
            prosody["volume"] = round(params.volume_db, 1)
        if prosody:
            body["prosody"] = prosody
        headers = self._auth_headers()
        headers.update({"Content-Type": "application/json", "model": self.model})
        try:
            resp = requests.post(
                f"{self.base_url}/v1/tts",
                json=body, headers=headers, timeout=self.timeout_s,
                proxies=self._proxies(),
            )
        except requests.RequestException as e:
            raise self._classify_network_error(e)
        if resp.status_code != 200:
            raise self._classify_http_error(resp)
        audio = resp.content
        if len(audio) < 1024:
            raise TTSProviderError(f"Fish 返回音频过小({len(audio)}B)，疑似异常", retryable=True)
        with open(out_path, "wb") as f:
            f.write(audio)
        # 从 WAV 头读真实采样率与时长（云端可能调整采样率）
        sample_rate, duration_ms = _wav_info(audio)
        logger.info("fish 合成完成 model=%s chars=%d duration=%dms",
                    self.model, len(text), duration_ms)
        return {"audio_path": out_path, "duration_ms": duration_ms,
                "sample_rate_hz": sample_rate}


# ---------------------------------------------------------------- 模块内工具

def _wav_info(audio: bytes):
    """解析 WAV 头，返回 (sample_rate, duration_ms)。

    只读 fmt 块（采样率/声道/位深）与 data 块大小，算出播放时长。
    注意：Fish 云端返回的是流式 WAV，data 块声明的 size 可能是
    占位值（实测 0xFFFFFF00），必须用「文件实际剩余字节」兜底，
    否则时长会算出十几小时的天文数字。
    任何解析异常都退回保守估值，不影响主流程。
    """
    import struct
    try:
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("非 WAV")
        pos = 12
        rate, channels, bits = 44100, 1, 16
        data_size, data_offset = 0, None
        while pos + 8 <= len(audio):
            chunk_id, size = struct.unpack("<4sI", audio[pos:pos + 8])
            if chunk_id == b"fmt " and size >= 16:
                channels = struct.unpack("<H", audio[pos + 10:pos + 12])[0]
                rate = struct.unpack("<I", audio[pos + 12:pos + 16])[0]
                bits = struct.unpack("<H", audio[pos + 22:pos + 24])[0]
            elif chunk_id == b"data":
                data_size = size
                data_offset = pos + 8
            pos += 8 + size + (size % 2)   # RIFF 块按 2 字节对齐
        # 流式 WAV 的 data size 占位不可信：以实际剩余字节为准
        if data_offset is not None:
            remaining = len(audio) - data_offset
            if not 0 < data_size <= remaining:
                data_size = max(remaining, 0)
        bytes_per_frame = max(1, channels * bits // 8)
        return rate, int(data_size / bytes_per_frame / max(rate, 1) * 1000)
    except Exception:
        return 44100, int(len(audio) / 2 / 44100 * 1000)
