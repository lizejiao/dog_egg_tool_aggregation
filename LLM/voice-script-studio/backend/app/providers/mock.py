"""Mock Provider：离线自测引擎。

不联网、不加载模型，用"正弦波音节串 + 静音"模拟语音，用于：
- 全流程冒烟（建音色 → 建任务 → 合成 → 拼接 → 下载）
- 断点续跑与重试逻辑的单测/集成测试
- WebUI 开发期的视觉验证

彩蛋：文本中包含 "FAIL" 时抛可重试错误，文本包含 "FATAL" 时抛不可重试错误，
方便测试重试与终态分类。
"""
import hashlib
import logging
import math
import struct
import wave
from typing import Any, Dict, List

from app.providers.base import (
    Caps, SynthParams, TTSProvider, TTSProviderError, VoiceMeta,
)

logger = logging.getLogger("vss.providers.mock")

_SAMPLE_RATE = 24000   # mock 输出采样率
_SILENCE = 0.05        # 每段末尾静音（秒）


class MockProvider(TTSProvider):
    """本地 mock 引擎。"""

    id = "mock"

    def capabilities(self) -> Caps:
        return Caps(
            kind="mock",
            display_name="Mock（离线自测）",
            max_concurrency=4,
            supports_emotion_tags=False,
            sample_rate_hz=_SAMPLE_RATE,
            has_preset_voices=True,
            needs_registration=False,
            notes="不联网、不调模型，仅用于全流程自测",
        )

    # ------------------------------------------------------------ 音色

    def list_preset_voices(self) -> List[VoiceMeta]:
        return [
            VoiceMeta(voice_key="preset:mock_soprano", name="Mock 女声（离线自测·嘟声非语音）"),
            VoiceMeta(voice_key="preset:mock_basso", name="Mock 男声（离线自测·嘟声非语音）"),
        ]

    def register_clone_voice(self, ref_path: str, ref_text: str,
                             name: str) -> VoiceMeta:
        # mock 无需注册：voice_key 直接用 name 生成稳定哈希，保证同名音色音高一致
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:4]
        return VoiceMeta(
            voice_key=f"clone:mock_{digest}", name=name,
            state="trained",
            extra={"hint": "mock 引擎不读取参考音频，仅按名称生成音高"},
        )

    def delete_voice(self, voice_key: str) -> None:
        logger.debug("mock delete_voice(%s) — 无需操作", voice_key)

    # ------------------------------------------------------------ 合成

    def synthesize(self, text: str, voice_key: str,
                   out_path: str, params: SynthParams) -> Dict[str, Any]:
        # 测试钩子：可控失败注入
        if "FATAL" in text:
            raise TTSProviderError("mock 注入的不可重试错误", retryable=False, status=400)
        if "FAIL" in text:
            raise TTSProviderError("mock 注入的可重试错误（模拟 429）",
                                   retryable=True, status=429)

        freq = self._voice_freq(voice_key)
        speed = max(0.5, min(params.speed, 2.0))
        frames = self._synthesize_frames(text, freq, speed)

        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(_SAMPLE_RATE)
            w.writeframes(frames)

        duration_ms = int(len(frames) / 2 / _SAMPLE_RATE * 1000)
        logger.info("mock 合成完成 voice=%s chars=%d duration=%dms",
                    voice_key, len(text), duration_ms)
        return {"audio_path": out_path, "duration_ms": duration_ms,
                "sample_rate_hz": _SAMPLE_RATE}

    # ------------------------------------------------------------ 内部

    def _voice_freq(self, voice_key: str) -> int:
        """音色 → 基频：预置两个示范音高，克隆音色按 key 哈希在 120~360Hz 分布。"""
        if voice_key == "preset:mock_soprano":
            return 320
        if voice_key == "preset:mock_basso":
            return 130
        digest = int(hashlib.sha256(voice_key.encode("utf-8")).hexdigest()[:6], 16)
        return 120 + digest % 240

    def _synthesize_frames(self, text: str, freq: int, speed: float) -> bytes:
        """生成"音节串"：每个非空白字符一个短音节，句读符号后加短停顿。"""
        frames = bytearray()
        pause_marks = set("，。！？；：、,.!?;: ")
        for ch in text:
            if ch in pause_marks:
                self._append_silence(frames, 0.10 / speed)
                continue
            self._append_tone(frames, freq, 0.07 / speed)
            self._append_silence(frames, 0.02)
        self._append_silence(frames, _SILENCE)
        return bytes(frames)

    def _append_tone(self, out: bytearray, freq: int, seconds: float) -> None:
        n = int(_SAMPLE_RATE * seconds)
        for i in range(n):
            # 淡入淡出防爆音
            env = min(1.0, i / 200.0, (n - i) / 200.0)
            val = int(12000 * env * math.sin(2 * math.pi * freq * i / _SAMPLE_RATE))
            out += struct.pack("<h", val)

    def _append_silence(self, out: bytearray, seconds: float) -> None:
        out += b"\x00\x00" * int(_SAMPLE_RATE * seconds)
