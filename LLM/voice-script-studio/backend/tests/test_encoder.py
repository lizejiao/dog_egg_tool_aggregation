"""encoder 转码模块测试：MP3/M4A 产物合法性、缓存命中与失效、错误提示。

运行前必须设置 VSS_DATA_DIR（见 tests/__init__.py 说明），否则污染真实数据目录。
"""
import os
import sys
import tempfile
import time
import unittest
import wave

_TEST_DATA_DIR = os.environ.get("VSS_DATA_DIR")
if not _TEST_DATA_DIR or "vss" not in tempfile.gettempdir() + _TEST_DATA_DIR:
    _TEST_DATA_DIR = tempfile.mkdtemp(prefix="vss-test-")
    os.environ["VSS_DATA_DIR"] = _TEST_DATA_DIR

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline import encoder  # noqa: E402


def _make_wav(path: str, seconds: float = 1.0, rate: int = 44100) -> None:
    """生成正弦波测试 WAV（16-bit 单声道）。"""
    import math
    import struct
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(math.sin(2 * math.pi * 440 * i / rate) * 8000))
            for i in range(int(rate * seconds))
        )
        w.writeframes(frames)


class EncoderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="enc-unit-")
        self.wav = os.path.join(self.dir, "final.wav")
        _make_wav(self.wav, 0.5)

    def test_wav_passthrough(self):
        """wav 格式原样返回母带路径，不产生 exports 目录。"""
        self.assertEqual(encoder.encode(self.wav, "wav"), self.wav)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "exports")))

    def test_mp3_magic_and_cache(self):
        """MP3 产物帧头合法（ID3/帧同步）；二次调用命中缓存；母带更新后重转。"""
        mp3 = encoder.encode(self.wav, "mp3")
        head = open(mp3, "rb").read(3)
        self.assertTrue(head[:3] == b"ID3" or head[0] == 0xFF, "MP3 魔数不符")
        self.assertGreater(os.path.getsize(mp3), 1024)
        mtime = os.path.getmtime(mp3)
        time.sleep(0.02)
        again = encoder.encode(self.wav, "mp3")
        self.assertEqual(again, mp3)
        self.assertEqual(os.path.getmtime(mp3), mtime, "缓存未命中（不该重转）")
        # 母带更新 → 缓存失效
        time.sleep(0.02)
        _make_wav(self.wav, 0.3)
        new = encoder.encode(self.wav, "mp3")
        self.assertGreater(os.path.getmtime(new), mtime, "缓存未随母带失效")

    def test_m4a_magic_and_cache(self):
        """M4A 产物 ftyp 容器合法；缓存行为同 MP3。"""
        m4a = encoder.encode(self.wav, "m4a")
        self.assertEqual(open(m4a, "rb").read(8)[4:8], b"ftyp")
        mtime = os.path.getmtime(m4a)
        time.sleep(0.02)
        encoder.encode(self.wav, "m4a")
        self.assertEqual(os.path.getmtime(m4a), mtime, "缓存未命中")

    def test_invalid_format(self):
        with self.assertRaises(ValueError):
            encoder.encode(self.wav, "flac")

    def test_export_filename(self):
        """文件名过滤路径非法字符并带正确后缀。"""
        name = encoder.export_filename('我的/演讲:稿*?"<>|', "mp3")
        self.assertTrue(name.endswith(".mp3"))
        for bad in '/:*?"<>|\\':
            self.assertNotIn(bad, name)

    def test_availability(self):
        av = encoder.availability()
        self.assertTrue(av["wav"])
        self.assertIn(av["mp3"], (True, False))  # 取决于环境，只测结构
        self.assertIn("m4a", av)


class ExportMetaEndpointTest(unittest.TestCase):
    """GET /api/meta/exports：前端下载按钮置灰的前置探测数据源。"""

    def test_meta_exports(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.store import db
        from app import settings as app_settings
        app_settings.ensure_dirs()
        db.init_db()
        client = TestClient(app)
        r = client.get("/api/meta/exports")
        self.assertEqual(r.status_code, 200)
        av = r.json()
        self.assertTrue(av["wav"])
        self.assertIn(av["mp3"], (True, False))
        self.assertIn("m4a", av)

    def test_audio_download_head_is_405(self):
        """HEAD 不被该端点支持（FastAPI 仅注册 GET）——记录该行为，
        防止再有人把 HEAD 探测用于下载可用性判断（曾导致误报弹窗）。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.head("/api/tasks/whatever/audio?format=mp3")
        self.assertEqual(r.status_code, 405)


if __name__ == "__main__":
    unittest.main()
