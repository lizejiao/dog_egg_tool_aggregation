"""fish_cloud 模块内工具的回归测试（不联网）。"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.providers.fish_cloud import _wav_info  # noqa: E402


def _make_wav(rate: int, channels: int, bits: int, frames: int,
              data_size_placeholder: int) -> bytes:
    """构造 WAV 字节流；data_size_placeholder 可故意填错误值（模拟流式占位）。"""
    data = b"\x00\x00" * frames * channels          # 16-bit 样本
    fmt = struct.pack("<HHIIHH", 1, channels, rate,
                      rate * channels * bits // 8, channels * bits // 8, bits)
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", data_size_placeholder) + data
    )


class TestWavInfo(unittest.TestCase):
    """Fish 云端返回流式 WAV（data size 为占位值）时，时长必须按实际剩余字节计算。"""

    def test_normal_wav(self):
        """正常 WAV：声明的 data size 正确，直接采用。"""
        audio = _make_wav(44100, 1, 16, frames=44100, data_size_placeholder=88200)
        rate, dur = _wav_info(audio)
        self.assertEqual(rate, 44100)
        self.assertEqual(dur, 1000)                  # 1 秒

    def test_streaming_placeholder_size(self):
        """流式占位 size（实测 Fish 返回 0xFFFFFF00）：回退实际字节数。"""
        audio = _make_wav(44100, 1, 16, frames=44100,
                          data_size_placeholder=0xFFFFFF00)
        rate, dur = _wav_info(audio)
        self.assertEqual(rate, 44100)
        self.assertEqual(dur, 1000)                  # 不再是天文数字

    def test_not_wav_fallback(self):
        """非 WAV 输入：保守估值兜底，不抛异常。"""
        rate, dur = _wav_info(b"ID3 some garbage bytes")
        self.assertEqual(rate, 44100)
        self.assertGreaterEqual(dur, 0)


if __name__ == "__main__":
    unittest.main()
