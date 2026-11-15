"""P2c 设置页配套 API 测试：全局合成参数读写、暖机端点、provider 扩展字段。"""
import os
import sys
import tempfile
import unittest

_TEST_DATA_DIR = os.environ.get("VSS_DATA_DIR")
if not _TEST_DATA_DIR or "vss" not in tempfile.gettempdir() + _TEST_DATA_DIR:
    _TEST_DATA_DIR = tempfile.mkdtemp(prefix="vss-test-")
    os.environ["VSS_DATA_DIR"] = _TEST_DATA_DIR

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import settings as app_settings  # noqa: E402
from app.store import config as config_store  # noqa: E402
from app.store import db  # noqa: E402

app_settings.ensure_dirs()
db.init_db()
client = TestClient(app)


class SynthesisConfigTest(unittest.TestCase):
    def test_get_defaults(self):
        r = client.get("/api/config/synthesis")
        self.assertEqual(r.status_code, 200)
        s = r.json()["synthesis"]
        for k in ("chars_per_segment", "max_retries",
                  "pause_between", "pause_between_paragraphs"):
            self.assertIn(k, s)

    def test_put_roundtrip_and_validation(self):
        r = client.put("/api/config/synthesis", json={
            "chars_per_segment": 120, "pause_between": 0.5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["synthesis"]["chars_per_segment"], 120)
        self.assertEqual(r.json()["synthesis"]["pause_between"], 0.5)
        # 越界 → 400
        r = client.put("/api/config/synthesis", json={"chars_per_segment": 999})
        self.assertEqual(r.status_code, 400)
        # 空更新 → 400
        r = client.put("/api/config/synthesis", json={})
        self.assertEqual(r.status_code, 400)
        # 还原默认（防污染其他测试）
        client.put("/api/config/synthesis", json={
            "chars_per_segment": 80, "pause_between": 0.35})

    def test_put_takes_effect_on_new_task(self):
        """改全局默认 → 新任务分句上限随之变化（参数未覆盖时）。"""
        client.put("/api/config/synthesis", json={"chars_per_segment": 40})
        r = client.post("/api/tasks", json={
            "title": "全局参数联动", "text": "，".join(["十个汉字十个汉字"] * 12) + "。",
            "voice_key": "clone:none", "provider_id": "mock",
        })
        tid = r.json()["task_id"]
        segs = client.get(f"/api/tasks/{tid}").json()["segments"]
        self.assertGreater(len(segs), 1, "40 字上限应把长文切成多段")
        client.put("/api/config/synthesis", json={"chars_per_segment": 80})


class WarmupEndpointTest(unittest.TestCase):
    def test_mock_warmup_noop(self):
        """mock 无需暖机 → ok=True 且文案可读（非本地引擎不拉权重）。

        注意：mock 的启用状态是后续测试（音色实体）的前置条件，测试后必须还原原值。
        """
        before = [p for p in client.get("/api/providers").json()["providers"]
                  if p["id"] == "mock"][0]["enabled"]
        client.put("/api/providers/mock", json={"enabled": True})
        try:
            r = client.post("/api/providers/mock/warmup")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])
        finally:
            client.put("/api/providers/mock", json={"enabled": before})

    def test_unknown_provider_400(self):
        r = client.post("/api/providers/nope/warmup")
        self.assertEqual(r.status_code, 400)


class ProviderFieldsTest(unittest.TestCase):
    def test_local_fields_exposed(self):
        """引擎列表应暴露本地引擎 fp16 / idle_kill_s（设置页表单回显）。"""
        r = client.get("/api/providers")
        local = [p for p in r.json()["providers"] if p["id"] == "cosyvoice_local"][0]
        self.assertIn("fp16", local)
        self.assertIn("idle_kill_s", local)
        self.assertIn(local["fp16"], (True, False))

    def test_put_local_fields(self):
        r = client.put("/api/providers/cosyvoice_local",
                       json={"fp16": True, "idle_kill_s": 120})
        self.assertEqual(r.status_code, 200)
        local = [p for p in r.json()["providers"] if p["id"] == "cosyvoice_local"][0]
        self.assertTrue(local["fp16"])
        self.assertEqual(local["idle_kill_s"], 120)
        # 还原
        client.put("/api/providers/cosyvoice_local", json={"fp16": False, "idle_kill_s": 300})


if __name__ == "__main__":
    unittest.main()
