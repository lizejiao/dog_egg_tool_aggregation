"""音频拼接层：分段 WAV → 整稿 WAV（纯标准库实现，零外部依赖）。

职责（docs/ARCHITECTURE.md §4.4）：
1. 读取各分段 WAV（引擎输出采样率可能不同：fish 44.1k / mock 24k）
2. 统一重采样到目标采样率（取首个分段的采样率，线性插值即可——
   语音场景下线性重采样听感可接受；后续如需高保真可换 ffmpeg）
3. 段间插入静音：段内句间 pause_between、段间 pause_between_paragraphs
4. 峰值归一化到 0.95，避免整稿忽大忽小
5. 输出 16-bit 单声道 WAV

WAV 读写只支持 PCM（非压缩）：fish 请求 format=wav、mock 输出均满足；
若未来引入 mp3 输出的引擎，需在该层前增加解码步骤（ffmpeg/afconvert）。
"""
import struct
import wave
from typing import Dict, List

# 归一化目标峰值（0~1）
_PEAK_TARGET = 0.95


def concat_segments(segment_paths: List[str], out_path: str,
                    pause_between_s: float = 0.35,
                    pause_between_paragraphs_s: float = 0.8,
                    para_break_map: Dict[int, bool] = None) -> Dict:
    """拼接入口。

    segment_paths: 按段序排列的 WAV 路径列表
    para_break_map: {seq: bool} 段序 → 是否段落边界（决定停顿时长）
    Returns: {"duration_ms", "sample_rate", "segments"}
    """
    if not segment_paths:
        raise ValueError("无分段可拼接")
    para_break_map = para_break_map or {}

    # 1) 读取所有分段（frames 为 16-bit 有符号采样数组）
    pieces = []          # [(rate, samples)]
    for p in segment_paths:
        pieces.append(_read_wav_mono(p))

    # 2) 目标采样率 = 首段采样率（通常整稿统一）
    target_rate = pieces[0][0]

    # 3) 逐段重采样 + 插静音
    out_samples: List[int] = []
    for idx, (rate, samples) in enumerate(pieces):
        if rate != target_rate:
            samples = _resample(samples, rate, target_rate)
        if idx > 0:
            seq = idx + 1  # 段序从 1 开始
            pause = (pause_between_paragraphs_s
                     if para_break_map.get(seq) else pause_between_s)
            out_samples.extend(_silence(target_rate, pause))
        out_samples.extend(samples)

    # 4) 峰值归一化（全部段完成后统一做，保证整稿响度一致）
    peak = max((abs(s) for s in out_samples), default=0)
    if peak > 0:
        gain = min(32767.0 * _PEAK_TARGET / peak, 2.0)  # 只放大 2 倍封顶，防底噪放大
        out_samples = [max(-32768, min(32767, int(s * gain))) for s in out_samples]

    # 5) 写出
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(target_rate)
        w.writeframes(b"".join(struct.pack("<h", s) for s in out_samples))

    return {
        "duration_ms": int(len(out_samples) / target_rate * 1000),
        "sample_rate": target_rate,
        "segments": len(pieces),
    }


# ---------------------------------------------------------------- 内部工具

def _read_wav_mono(path: str):
    """读 WAV → (sample_rate, samples)。多声道时混为单声道。"""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        if width != 2:
            raise ValueError(f"仅支持 16-bit PCM，{path} 为 {width * 8}-bit")
        raw = w.readframes(w.getnframes())
    samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
    if channels > 1:
        samples = [samples[i] for i in range(0, len(samples), channels)]
    return rate, list(samples)


def _resample(samples: List[int], from_rate: int, to_rate: int) -> List[int]:
    """线性插值重采样。"""
    if not samples:
        return []
    n_out = int(len(samples) * to_rate / from_rate)
    step = (len(samples) - 1) / max(n_out - 1, 1)
    return [samples[min(int(i * step), len(samples) - 1)] for i in range(n_out)]


def _silence(rate: int, seconds: float) -> List[int]:
    return [0] * int(rate * seconds)
