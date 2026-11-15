"""暖机门禁专项（2026-11-15 用户定稿方案的防回归）。

验证四条硬规则（全部用伪造 Popen，不加载真实模型）：
1. cold 态：合成/试听被 NotWarmedError 拒绝，且绝不拉起子进程
2. warmup 是唯一加载入口：成功 → ready + 30 分钟窗口；失败 → 回 cold
3. 滑动窗口续期：每次成功使用重置窗口；到期自动释放
4. 手动释放：空闲可释放；在途请求（busy）时拒绝
5. runner 层：本地引擎未暖机 start_or_resume 抛 NotWarmedError（创建放行）
"""
import io
import json
import time
import unittest
from unittest import mock

from app.providers import cosyvoice_local as cl
from app.providers.cosyvoice_local import NotWarmedError


class _FakeStdin:
    def __init__(self):
        self.buf = io.StringIO()

    def write(self, s):
        self.buf.write(s)

    def flush(self):
        pass


class _FakeStdout:
    def readline(self):
        return cl._RESP_PREFIX + json.dumps({"ok": True, "message": "fake"}) + "\n"


class _FakeProc:
    instances = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.killed = False
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()
        self.pid = 20000 + len(_FakeProc.instances)
        _FakeProc.instances.append(self)

    def poll(self):
        return "killed" if self.killed else None

    def kill(self):
        self.killed = True


def _provider(**cfg):
    base = {"fp16": False, "timeout_s": 5, "idle_kill_s": 1800}
    base.update(cfg)
    return cl.CosyVoiceLocalProvider(base)


class WarmupGateTest(unittest.TestCase):
    def setUp(self):
        _FakeProc.instances.clear()
        self._popen = mock.patch.object(
            cl.subprocess, "Popen", side_effect=lambda *a, **k: _FakeProc(*a, **k))
        self._popen.start()
        self._installed = mock.patch.object(cl, "_engine_installed",
                                            return_value=True)
        self._installed.start()
        self._reset_globals()

    def tearDown(self):
        self._restore_and_reset()

    def _reset_globals(self):
        # 保存并清零全局状态（每个用例独立冷启动）
        self._saved = (cl._WORKER_PROC, cl._WORKER_FP16, cl._WARM_STATE,
                       cl._WARM_EXPIRES_AT, cl._WARM_BUSY, cl._WARM_TIMER)
        cl._WORKER_PROC, cl._WORKER_FP16 = None, None
        cl._WARM_STATE, cl._WARM_EXPIRES_AT, cl._WARM_BUSY = "cold", 0.0, 0
        cl._WARM_TAKEN.clear()
        if cl._WARM_TIMER:
            cl._WARM_TIMER.cancel()
        cl._WARM_TIMER = None

    def _restore_and_reset(self):
        self._popen.stop()
        self._installed.stop()
        cl._WORKER_PROC, cl._WORKER_FP16 = None, None
        cl._WARM_STATE, cl._WARM_EXPIRES_AT, cl._WARM_BUSY = "cold", 0.0, 0
        cl._WARM_TAKEN.clear()
        if cl._WARM_TIMER:
            cl._WARM_TIMER.cancel()
        cl._WARM_TIMER = None

    # ------------------------------------------------------------ 规则 1

    def test_cold_rejects_synth_and_no_spawn(self):
        """cold 态合成被拒 + 全程零子进程。"""
        p = _provider()
        with self.assertRaises(NotWarmedError):
            p._request({"cmd": "synth", "fp16": False})
        self.assertEqual(len(_FakeProc.instances), 0,
                         "未暖机绝不允许拉起 worker")

    def test_loading_rejects_with_clear_message(self):
        """loading 态（暖机中）拒绝并给出明确文案。"""
        self.assertTrue(cl.acquire_for_warmup())
        p = _provider()
        with self.assertRaises(NotWarmedError) as ctx:
            p._request({"cmd": "synth", "fp16": False})
        self.assertIn("暖机中", str(ctx.exception))
        cl.finish_warmup(False, 1800)

    # ------------------------------------------------------------ 规则 2

    def test_warmup_is_only_load_entry(self):
        """暖机成功 → ready，窗口 1800s。"""
        r = _provider().warmup()
        self.assertTrue(r["ok"])
        info = cl.warm_info()
        self.assertEqual(info["warm_state"], "ready")
        self.assertTrue(1700 < info["remaining_s"] <= 1800)
        self.assertEqual(len(_FakeProc.instances), 1)

    def test_warmup_failure_back_to_cold(self):
        """暖机失败（worker 报错）→ 回 cold。"""
        bad = cl._RESP_PREFIX + json.dumps({"ok": False, "error": "权重缺失"})
        with mock.patch.object(_FakeStdout, "readline", return_value=bad):
            r = _provider().warmup()
        self.assertFalse(r["ok"])
        self.assertEqual(cl.warm_info()["warm_state"], "cold")

    def test_double_warmup_rejected(self):
        """暖机会话互斥：第二次直接拒绝。"""
        good = cl._RESP_PREFIX + json.dumps({"ok": True, "message": "loaded"})
        with mock.patch.object(_FakeStdout, "readline", return_value=good):
            self.assertTrue(cl.acquire_for_warmup())
            r = _provider().warmup()      # 应拿到"已有暖机在进行"
            self.assertFalse(r["ok"])
            self.assertIn("暖机", r["message"])
            cl.finish_warmup(True, 1800)

    # ------------------------------------------------------------ 规则 3

    def test_use_renews_window(self):
        """每次成功使用 → 窗口重置 30 分钟。"""
        with mock.patch.object(_FakeStdout, "readline",
                               return_value=cl._RESP_PREFIX +
                               json.dumps({"ok": True, "message": "ok"})):
            _provider().warmup()
            # 把窗口压到只剩 10s
            cl._WARM_EXPIRES_AT = time.time() + 10
            p = _provider()
            p._request({"cmd": "synth", "fp16": False})     # 使用 → 续期
            info = cl.warm_info()
            self.assertEqual(info["warm_state"], "ready")
            self.assertTrue(info["remaining_s"] > 1700, "使用后续期回 30 分钟")

    def test_window_expiry_releases(self):
        """到期未使用 → 自动释放回 cold。"""
        with mock.patch.object(_FakeStdout, "readline",
                               return_value=cl._RESP_PREFIX +
                               json.dumps({"ok": True, "message": "ok"})):
            _provider().warmup()
        cl._WARM_EXPIRES_AT = time.time() - 1      # 已过期
        cl._on_window_expired()
        info = cl.warm_info()
        self.assertEqual(info["warm_state"], "cold")
        self.assertTrue(_FakeProc.instances[0].killed)

    # ------------------------------------------------------------ 规则 4

    def test_release_idle_ok_busy_rejected(self):
        """手动释放：空闲成功；busy 拒绝。"""
        with mock.patch.object(_FakeStdout, "readline",
                               return_value=cl._RESP_PREFIX +
                               json.dumps({"ok": True, "message": "ok"})):
            _provider().warmup()
        r = cl.release_worker()
        self.assertTrue(r["ok"])
        self.assertEqual(cl.warm_info()["warm_state"], "cold")

        with mock.patch.object(_FakeStdout, "readline",
                               return_value=cl._RESP_PREFIX +
                               json.dumps({"ok": True, "message": "ok"})):
            _provider().warmup()
            with cl.WarmedGuard():                    # 模拟在途使用
                r = cl.release_worker()
                self.assertFalse(r["ok"])
                self.assertIn("正在使用", r["message"])

    # ------------------------------------------------------------ 规则 5

    def test_runner_blocks_local_start_when_cold(self):
        """runner：本地引擎未暖机，start_or_resume 抛 NotWarmedError。"""
        from app.pipeline import runner
        with mock.patch.object(
                runner.db, "get_task",
                return_value={"id": "t1", "status": "pending",
                              "provider_id": "cosyvoice_local"}):
            with self.assertRaises(NotWarmedError):
                runner.TaskRunner.start_or_resume("t1")


if __name__ == "__main__":
    unittest.main()
