"""P2-0 专项测试：两级实体（素材/音色）+ 能力矩阵 supported_params + voiceent 任务键。

覆盖：
- registrations JSON → voice_entities 迁移（幂等 + 备份产物）
- 注册新音色（可指定音色名；同引擎重名 409；跨引擎可重名）
- 缺省音色名的自动避让（旧前端兼容路径）
- 删音色不删素材（registrations 双写同步）
- /api/providers 输出 supported_params（Fish/本地/mock 三引擎能力矩阵）
- 任务以 voiceent:<id> 为 voice_key 走通全流程（mock 引擎）

运行前必须设置 VSS_DATA_DIR（与 test_api_e2e.py 同约定）。
"""
import os
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
from app.store import db  # noqa: E402

app_settings.ensure_dirs()
db.init_db()

client = TestClient(app)

_TS = str(int(time.time() * 1000) % 10**9)   # 本轮测试唯一名前缀


def _tiny_wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _upload_material(name: str) -> str:
    """上传纯素材（不注册），返回素材 id。"""
    r = client.post("/api/voices", data={
        "name": name,
        "ref_text": "这是逐字稿，与录音内容一致。",
        "register_provider": "none",
    }, files={"audio": ("ref.wav", _tiny_wav_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _register(vid: str, engine: str, voice_name=None):
    body = {"voice_name": voice_name} if voice_name is not None else None
    return client.post(f"/api/voices/{vid}/register/{engine}", json=body)


class TestMigration(unittest.TestCase):

    def test_01_registrations_migrate_idempotent(self):
        """旧数据（registrations JSON）迁移为音色实体；二次运行不重复。"""
        vid = db.create_voice(
            f"迁移素材{_TS}", "/tmp/x.wav", "旧逐字稿",
            registrations={"mock": {"voice_key": f"clone:mig_{_TS}",
                                     "state": "trained"}})
        db._migrate_registrations()   # init_db 已跑过一次，这里模拟历史数据后补跑
        ents = [e for e in db.list_voice_entities("mock")
                if e["material_id"] == vid]
        self.assertEqual(len(ents), 1)
        self.assertTrue(ents[0]["name"].startswith(f"迁移素材{_TS}"))
        self.assertEqual(ents[0]["voice_key"], f"clone:mig_{_TS}")
        # 幂等：再来一次不重复
        db._migrate_registrations()
        ents2 = [e for e in db.list_voice_entities("mock")
                 if e["material_id"] == vid]
        self.assertEqual(len(ents2), 1)

    def test_02_migration_backup_created(self):
        """发生实际迁移时先备份 vss.db（仅一次）。"""
        bak = db.settings.DB_PATH + ".pre-migration.bak"
        self.assertTrue(os.path.isfile(bak), "迁移备份文件应存在")

    def test_03_db_level_name_uniqueness(self):
        """存储层同引擎重名 → VoiceNameTaken；跨引擎重名放行。"""
        db.create_voice_entity(f"唯一名{_TS}", "mock", "m-units", "clone:k1")
        with self.assertRaises(db.VoiceNameTaken):
            db.create_voice_entity(f"唯一名{_TS}", "mock", "m-units", "clone:k2")
        # 跨引擎同名字可注册（素材库「-本地版/-云端版」场景）
        ent = db.create_voice_entity(f"唯一名{_TS}", "cosyvoice_local",
                                     "m-units", "clone:k3")
        self.assertEqual(ent["engine"], "cosyvoice_local")


class TestRegisterFlow(unittest.TestCase):

    def test_10_register_with_voice_name(self):
        """注册新音色：显式音色名 → 实体创建 + registrations 双写。"""
        vid = _upload_material(f"素材A{_TS}")
        r = _register(vid, "mock", voice_name=f"我的演讲声-mock版{_TS}")
        self.assertEqual(r.status_code, 200, r.text)
        ent = r.json()["voice_entity"]
        self.assertEqual(ent["name"], f"我的演讲声-mock版{_TS}")
        self.assertEqual(ent["engine"], "mock")
        self.assertEqual(ent["material_id"], vid)
        # 双写：旧字段 registrations 同步
        voice = db.get_voice(vid)
        self.assertIn("mock", voice["registrations"])

    def test_11_same_engine_name_conflict_409(self):
        """同引擎重名 → 409；换名或换引擎成功。"""
        vid1 = _upload_material(f"素材B{_TS}")
        r1 = _register(vid1, "mock", voice_name=f"撞名{_TS}")
        self.assertEqual(r1.status_code, 200)
        vid2 = _upload_material(f"素材C{_TS}")
        r2 = _register(vid2, "mock", voice_name=f"撞名{_TS}")
        self.assertEqual(r2.status_code, 409, r2.text)
        # 同一音色名注册到另一引擎（cosyvoice_local 注册=记录引用，不联网）
        r3 = _register(vid2, "cosyvoice_local", voice_name=f"撞名{_TS}")
        self.assertEqual(r3.status_code, 200, r3.text)

    def test_12_default_name_auto_avoidance(self):
        """缺省音色名：沿用素材名并自动避让（旧前端不传名的兼容路径）。"""
        n = f"同名素材{_TS}"
        vid1 = _upload_material(n)
        vid2 = _upload_material(n)
        r1 = _register(vid1, "mock")
        r2 = _register(vid2, "mock")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200, r2.text)
        names = {e["name"] for e in db.list_voice_entities("mock")
                 if e["material_id"] in (vid1, vid2)}
        self.assertEqual(len(names), 2, f"应自动避让：{names}")

    def test_13_delete_entity_keeps_material(self):
        """删音色：素材保留、实体消失、registrations 同步移除。"""
        vid = _upload_material(f"素材D{_TS}")
        r = _register(vid, "mock", voice_name=f"待删音色{_TS}")
        eid = r.json()["voice_entity"]["id"]
        d = client.delete(f"/api/voice-entities/{eid}")
        self.assertEqual(d.status_code, 200, d.text)
        self.assertIsNone(db.get_voice_entity(eid))
        # 素材还在
        self.assertIsNotNone(db.get_voice(vid))
        # registrations 双写同步移除
        self.assertNotIn("mock", db.get_voice(vid)["registrations"])

    def test_14_entities_endpoints(self):
        """/api/voice-entities 列举 + engine 过滤；总表接口含音色实体。"""
        r = client.get("/api/voice-entities")
        self.assertEqual(r.status_code, 200)
        all_ents = r.json()["voice_entities"]
        self.assertTrue(all_ents)
        r2 = client.get("/api/voice-entities?engine=mock")
        for e in r2.json()["voice_entities"]:
            self.assertEqual(e["engine"], "mock")
        # 总表 /api/voices 同样携带音色实体
        self.assertIn("voice_entities", client.get("/api/voices").json())


class TestCapsAndTask(unittest.TestCase):

    def test_20_supported_params_matrix(self):
        """能力矩阵经 /api/providers 下发（前端参数面板动态渲染依据）。"""
        providers = {p["id"]: p for p in client.get("/api/providers").json()["providers"]}
        self.assertEqual(providers["fish_cloud"]["supported_params"],
                         {"speed": True, "volume": True})
        self.assertEqual(providers["cosyvoice_local"]["supported_params"],
                         {"speed": True, "volume": False})
        self.assertEqual(providers["mock"]["supported_params"],
                         {"speed": True, "volume": False})

    def test_21_task_with_voiceent_key(self):
        """voiceent:<id> 作为任务 voice_key 走通全流程（新前端路径）。"""
        vid = _upload_material(f"素材E{_TS}")
        ent = _register(vid, "mock", voice_name=f"合成用音色{_TS}").json()["voice_entity"]
        r = client.post("/api/tasks", json={
            "title": "voiceent 键合成",
            "text": "这是一段用于验证音色实体键的短文本，句号结尾。",
            "voice_key": f"voiceent:{ent['id']}",
            "provider_id": "mock",
            "params": {"speed": 1.5},
        })
        self.assertEqual(r.status_code, 200, r.text)
        task_id = r.json()["task_id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            t = client.get(f"/api/tasks/{task_id}").json()
            if t["task"]["status"] in ("done", "partial_failed", "failed"):
                break
            time.sleep(0.2)
        self.assertEqual(t["task"]["status"], "done",
                         f"任务应完成：{t['task']}")

    def test_22_voiceent_engine_mismatch_rejected(self):
        """voiceent 键与任务引擎不符 → 解析层拒绝（勿静默合成）。

        注：键解析发生在任务后台线程，POST 创建本身返回 200，
        因此正确断言位置是 runner._resolve_voice_key。
        """
        vid = _upload_material(f"素材F{_TS}")
        ent = _register(vid, "mock", voice_name=f"错配音色{_TS}").json()["voice_entity"]
        from app.pipeline import runner
        with self.assertRaises(PermissionError):
            runner._resolve_voice_key("any", "cosyvoice_local",
                                      f"voiceent:{ent['id']}")
        # 匹配引擎则正常解析
        key = runner._resolve_voice_key("any", "mock", f"voiceent:{ent['id']}")
        self.assertTrue(key)


if __name__ == "__main__":
    unittest.main()
