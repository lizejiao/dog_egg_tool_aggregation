"""成品音频转码：整稿 WAV → MP3 / M4A（下载时按需转码 + 缓存）。

设计要点（见 ARCHITECTURE.md §12 偏差表第 12 条）：
- 母带唯一：合成链路始终产出 final.wav；MP3/M4A 只是下载视图，不参与合成状态
- 按需转码：首次请求某格式时才转，产物缓存到 {task_dir}/exports/
- 缓存失效：以母带 mtime 为指纹——整稿重新生成（重试段/续跑）后自动重转
- MP3：lameenc（纯 LAME wheel，无系统依赖）；缺依赖时报中文提示而非崩溃
- M4A：afconvert（macOS 自带 AAC 编码器），零新依赖
"""
import os
import subprocess
import wave
from typing import Optional

# 支持的导出格式 → (扩展名, media_type)
FORMATS = {
    "wav": (".wav", "audio/wav"),
    "mp3": (".mp3", "audio/mpeg"),
    "m4a": (".m4a", "audio/mp4"),
}

MP3_BITRATE_KBPS = 128   # 语音透明音质（原始即 TTS 单声道输出）
AFCONVERT = "/usr/bin/afconvert"


# ---------------------------------------------------------------- 公共出口

def encode(wav_path: str, fmt: str = "wav") -> str:
    """把整稿 WAV 转成目标格式，返回产物路径（已是目标格式则原样返回）。

    产物缓存规则：exports/final.{ext} 存在且 mtime >= 母带 mtime 即命中，
    否则重新转码（整稿重新生成后旧缓存自动失效）。
    """
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise ValueError(f"不支持的导出格式：{fmt}（可选 {list(FORMATS)})")
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"母带不存在：{wav_path}")
    if fmt == "wav":
        return wav_path

    ext, _ = FORMATS[fmt]
    exports_dir = os.path.join(os.path.dirname(os.path.abspath(wav_path)), "exports")
    os.makedirs(exports_dir, exist_ok=True)
    out_path = os.path.join(exports_dir, f"final{ext}")
    src_mtime = os.path.getmtime(wav_path)
    if os.path.isfile(out_path) and os.path.getmtime(out_path) >= src_mtime:
        return out_path  # 缓存命中：母带未变

    if fmt == "mp3":
        _encode_mp3(wav_path, out_path)
    elif fmt == "m4a":
        _encode_m4a(wav_path, out_path)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"转码 {fmt} 失败：产物缺失或过小")
    return out_path


def media_type(fmt: str) -> str:
    """下载响应的 Content-Type。"""
    return FORMATS[fmt.lower()][1]


def export_filename(title: str, fmt: str) -> str:
    """下载文件名（任务标题 + 格式后缀；过滤路径非法字符）。"""
    safe = "".join(c for c in title if c not in r'/:*?"<>|\\').strip() or "output"
    return f"{safe}{FORMATS[fmt.lower()][0]}"


# ---------------------------------------------------------------- MP3（lameenc）

def _encode_mp3(wav_path: str, out_path: str) -> None:
    try:
        import lameenc
    except ImportError as e:
        raise RuntimeError(
            "MP3 导出需要 lameenc 依赖，请在 backend 目录执行："
            ".venv/bin/pip install lameenc"
        ) from e

    with wave.open(wav_path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()          # 期望 2（16-bit）
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())

    if width != 2:
        raise RuntimeError(f"MP3 转码期望 16-bit PCM，实际 {width * 8}-bit")
    # lameenc 需要多个通道交错的多字节缓冲（Python int 列表），单声道直接喂
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_BITRATE_KBPS)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)                # 2=高音质（0 最高但极慢，2 是常用平衡点）
    mp3_bytes = encoder.encode(pcm)
    mp3_bytes += encoder.flush()
    with open(out_path, "wb") as f:
        f.write(bytes(mp3_bytes))


# ---------------------------------------------------------------- M4A（afconvert）

def _encode_m4a(wav_path: str, out_path: str) -> None:
    """afconvert：WAV → AAC（m4a 容器）。macOS 自带，零依赖。

    注意：不要显式指定 `-b` 码率——TTS 母带多为 24kHz 单声道，AFConvert 对该
    组合拒绝设置码率属性（报 "Couldn't set audio converter property ('!dat')"），
    交给 AAC 编码器按采样率/声道自动选择码率即可（实测 24k mono ≈ 96kbps，
    语音场景足够）。
    """
    if not os.path.isfile(AFCONVERT):
        raise RuntimeError("M4A 导出依赖 macOS 自带的 afconvert，当前系统未找到")
    cmd = [AFCONVERT, "-f", "m4af", "-d", "aac", wav_path, out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("M4A 转码超时（120 秒）") from e
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:200]
        raise RuntimeError(f"afconvert 转码失败：{detail}")


# ---------------------------------------------------------------- 探测

def mp3_available() -> bool:
    """lameenc 是否已安装（前端按钮可用性提示用）。"""
    try:
        import lameenc  # noqa: F401
        return True
    except ImportError:
        return False


def m4a_available() -> bool:
    return os.path.isfile(AFCONVERT)


def availability() -> dict:
    """各格式可用性（引擎设置页/诊断用）。"""
    return {
        "wav": True,
        "mp3": mp3_available(),
        "m4a": m4a_available(),
    }
