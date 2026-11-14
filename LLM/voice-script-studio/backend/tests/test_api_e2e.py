"""API 端到端集成测试：mock provider 全流程冒烟。

覆盖：音色上传→注册→列举、稿件上传校验、建任务→合成→拼接→下载、
失败段重试、断点续跑（模拟进程内重启：直接再调 start_or_resume）。

运行前必须设置 VSS_DATA_DIR（见 tests/__init__.py 的说明），否则
会污染真实 data 目录。
"""
import os
import struct
import sys
import tempfile
import time
import unittest
import wave

# 环境隔离检查：数据目录必须是测试专用
_TEST_DATA_DIR = os.environ.get("VSS_DATA_DIR")
if not _TEST_DATA_DIR or "vss" not in tempfile.gettempdir() + _TEST_DATA_DIR:
    _TEST_DATA_DIR = tempfile.mkdtemp(prefix="vss-test-")
    os.environ["VSS_DATA_DIR"] = _TEST_DATA_DIR

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import settings as app_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline.runner import TaskRunner  # noqa: E402
from app.store import db  # noqa: E402

# TestClient 不触发 startup 事件（with 上下文才会），这里显式完成启动职责
app_settings.ensure_dirs()
db.init_db()

client = TestClient(app)


def _tiny_wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """生成最小合法 WAV（上传音色用）。"""
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _wait_task_done(task_id: str, timeout_s: float = 60) -> dict:
    """轮询任务直到终态。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}").json()
        if r["task"]["status"] in ("done", "partial_failed", "failed"):
            return r
        time.sleep(0.2)
    raise TimeoutError(f"任务 {task_id} 超时未完成")


class TestEndToEnd(unittest.TestCase):

    # ------------------------------------------------------------ 音色

    def test_01_create_and_list_voice(self):
        """上传克隆音色 → mock 自动注册 → 出现在列表。"""
        r = client.post("/api/voices", data={
            "name": "测试音色",
            "ref_text": "这是逐字稿测试内容，与录音一致。",
        }, files={"audio": ("ref.wav", _tiny_wav_bytes(), "audio/wav")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("mock", body["registrations"])

        listing = client.get("/api/voices").json()
        self.assertTrue(any(v["name"] == "测试音色" for v in listing["clones"]))
        # mock 预置音色可见
        presets = listing["presets"].get("mock", [])
        self.assertTrue(any("mock_soprano" in p["voice_key"] for p in presets))

    def test_02_voice_upload_validation(self):
        """非白名单扩展名与空白逐字稿必须 400。

        注：用 " " 而非 ""——TestClient 的 httpx 会丢弃 multipart 中的
        空字符串字段（已知行为），空串会变成 422 而非到达端点逻辑。
        真实浏览器无此问题。
        """
        r = client.post("/api/voices", data={"name": "x", "ref_text": " "},
                        files={"audio": ("a.exe", b"bad", "application/x-exe")})
        self.assertEqual(r.status_code, 400)
        r = client.post("/api/voices", data={"name": "x", "ref_text": " "},
                        files={"audio": ("a.wav", _tiny_wav_bytes(0.2), "audio/wav")})
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------ 稿件上传

    def test_03_upload_script_md(self):
        """md 上传 → 清洗 + 段数预估；非法扩展名拒绝。"""
        md = ("---\ntitle: x\n---\n\n# 演讲标题\n\n"
              "这是正文第一段，足够长可以分成句子。这是第二句话！\n\n"
              "```js\nconsole.log(1)\n```\n\n第二段正文。")
        r = client.post("/api/upload/script",
                        files={"file": ("讲稿.md", md.encode(), "text/markdown")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertNotIn("console.log", body["cleaned_text"])
        self.assertNotIn("#", body["cleaned_text"])
        self.assertGreaterEqual(body["segment_count"], 1)

        r = client.post("/api/upload/script",
                        files={"file": ("virus.exe", b"MZ", "application/x-exe")})
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------ 合成主流程

    def test_04_full_synthesis_with_mock(self):
        """预置音色 + mock 引擎：建任务 → done → 整稿可下载且是合法 WAV。"""
        r = client.post("/api/tasks", json={
            "title": "冒烟测试",
            "text": ("你好，这是第一段。\n\n这是第二段，稍微长一些，"
                     "包含多个句号。测试拼接！[pause] 标签透传不影响。"),
            "voice_key": "preset:mock_soprano",
            "provider_id": "mock",
        })
        self.assertEqual(r.status_code, 200, r.text)
        task_id = r.json()["task_id"]

        result = _wait_task_done(task_id)
        self.assertEqual(result["task"]["status"], "done", result)
        # 全段落 ok
        self.assertTrue(all(s["status"] == "ok" for s in result["segments"]))

        audio = client.get(f"/api/tasks/{task_id}/audio")
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content[:4], b"RIFF")  # 合法 WAV

        # 单段试听
        seg = client.get(f"/api/tasks/{task_id}/segments/1/audio")
        self.assertEqual(seg.status_code, 200)

    def test_05_failure_retry_and_resume(self):
        """失败段（FAIL 注入）→ partial_failed → 单段重试与断点续跑生效。"""
        r = client.post("/api/tasks", json={
            "title": "失败测试",
            "text": "正常段落一。FAIL 注入可重试失败。正常段落三。",
            "voice_key": "preset:mock_basso",
            "provider_id": "mock",
        })
        task_id = r.json()["task_id"]
        result = _wait_task_done(task_id, timeout_s=90)  # 重试退避 ~3.5s
        self.assertEqual(result["task"]["status"], "partial_failed")
        failed_seqs = [s["seq"] for s in result["segments"] if s["status"] == "failed"]
        self.assertTrue(failed_seqs)

        # 断点续跑：重置失败段再跑（文本仍含 FAIL 会再失败，验证流程而非结果）
        seq = failed_seqs[0]
        r = client.post(f"/api/tasks/{task_id}/segments/{seq}/retry")
        self.assertIn(r.status_code, (200, 400))  # 已在运行时 400 亦可接受
        result = _wait_task_done(task_id, timeout_s=90)
        self.assertIn(result["task"]["status"], ("partial_failed", "done"))

    def test_06_resume_after_restart(self):
        """模拟重启续跑：新进程视角下对未完成任务直接 start_or_resume。

        稿件分三个段落：正常段/FAIL 段/正常段（FAIL 独占一段，确保
        存在可跳过的 ok 段可断言）。"""
        r = client.post("/api/tasks", json={
            "title": "续跑测试",
            "text": ("段落甲，这是一段比较长一些的占位内容，保证独立成段。"
                     "段落乙，同样是比较长的占位内容。\n\n"
                     "FAIL 再失败一次。\n\n"
                     "段落丙，继续占位的内容，长度足够独立成段。"),
            "voice_key": "preset:mock_soprano",
            "provider_id": "mock",
        })
        task_id = r.json()["task_id"]
        result = _wait_task_done(task_id, timeout_s=90)
        self.assertEqual(result["task"]["status"], "partial_failed")

        # 模拟重启：直接调用 TaskRunner（绕过运行注册表），ok 段应被跳过
        attempts_before = {s["seq"]: s["attempts"] for s in result["segments"]
                           if s["status"] == "ok"}
        self.assertTrue(attempts_before, "应存在成功段")

        TaskRunner.start_or_resume(task_id)
        result2 = _wait_task_done(task_id, timeout_s=90)
        attempts_after = {s["seq"]: s["attempts"] for s in result2["segments"]
                          if s["status"] == "ok"}
        # 续跑未重复合成已成功段（attempts 不变）
        self.assertEqual(attempts_before, attempts_after)

    # ------------------------------------------------------------ 引擎设置

    def test_07_provider_settings_flow(self):
        """设置页：列举 → 更新模型串 → 测试连接（mock 恒 ok）。"""
        listing = client.get("/api/providers").json()["providers"]
        ids = [p["id"] for p in listing]
        self.assertIn("fish_cloud", ids)
        self.assertIn("mock", ids)

        r = client.put("/api/providers/fish_cloud", json={
            "enabled": True, "model": "s2.1-pro-pro",  # 故意写错再改回
        })
        self.assertEqual(r.status_code, 200)
        models = [p.get("model") for p in r.json()["providers"]
                  if p["id"] == "fish_cloud"]
        self.assertEqual(models[0], "s2.1-pro-pro")

        r = client.put("/api/providers/fish_cloud", json={"model": "s2.1-pro-free"})
        self.assertEqual(r.status_code, 200)

        r = client.post("/api/providers/mock/test")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_08_preview_cache(self):
        """试听：首次生成缓存，二次命中 cached=True。"""
        args = "?provider_id=mock&voice_key=preset:mock_soprano"
        r1 = client.post(f"/api/voices/preview{args}")
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertFalse(r1.json()["cached"])
        r2 = client.post(f"/api/voices/preview{args}")
        self.assertTrue(r2.json()["cached"])
        # 缓存回放
        url = r2.json()["preview_url"]
        audio = client.get(url)
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content[:4], b"RIFF")


class PresetRefreshTest(unittest.TestCase):
    """?refresh=1 必须清空 60s TTL 缓存强制回源（网页端新建音色即时可同步）。"""

    def setUp(self):
        from app.api import voices as voices_api
        self.voices_api = voices_api
        voices_api._PRESET_CACHE.clear()

    def tearDown(self):
        self.voices_api._PRESET_CACHE.clear()

    def test_refresh_bypasses_ttl_cache(self):
        # 1) 注入一份“新鲜”的伪缓存条目 → 普通请求应命中缓存
        self.voices_api._PRESET_CACHE["mock"] = (
            time.time(),
            [{"voice_key": "preset:fake_cached", "name": "伪缓存音色", "state": "trained"}],
        )
        r = client.get("/api/voices")
        keys = [m["voice_key"] for m in r.json()["presets"]["mock"]]
        self.assertEqual(keys, ["preset:fake_cached"])

        # 2) refresh=1 → 缓存被清、真实回源（拿到 mock 的真实预置音色）
        r = client.get("/api/voices?refresh=1")
        keys = [m["voice_key"] for m in r.json()["presets"]["mock"]]
        self.assertIn("preset:mock_soprano", keys)
        self.assertNotIn("preset:fake_cached", keys)

    def test_refresh_failure_keeps_no_blank(self):
        """刷新路径上引擎异常时返回错误信息而非 500/空白（降级保护）。"""
        from app.api import voices as voices_api
        original = voices_api.get_provider

        class _BrokenProvider:
            def capabilities(self):
                from app.providers.base import Caps
                return Caps(kind="cloud", display_name="broken",
                            has_preset_voices=True)

            def list_preset_voices(self):
                from app.providers.base import TTSProviderError
                raise TTSProviderError("mock 的连接失败")

        def _fake_get_provider(pid):
            if pid == "mock":
                return _BrokenProvider()
            return original(pid)

        voices_api.get_provider = _fake_get_provider
        try:
            r = client.get("/api/voices?refresh=1")
            self.assertEqual(r.status_code, 200)
            self.assertIn("error", r.json()["presets"]["mock"])
        finally:
            voices_api.get_provider = original


if __name__ == "__main__":
    unittest.main()
