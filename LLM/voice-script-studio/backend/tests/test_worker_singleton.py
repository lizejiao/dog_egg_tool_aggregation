"""CosyVoice worker 全局单例回归（2026-11-15 卡死缺陷的防回归）。

背景：worker 子进程曾挂在 provider 实例上，而 get_provider() 每次调用
都新建实例 → 多任务/试听并发时各自拉起独立 worker、各自加载一份完整
模型，16G 内存机器直接卡死。修复后 worker 为模块级全局单例，
且加载入口收敛到显式暖机（见 test_warmup_gate.py 门禁专项）。

本文件用伪造 Popen 验证单例语义（在已暖机的前提下）：
1. 多个 provider 实例串行请求 → 全程只有一个子进程
2. worker 意外退出 → 下一个请求自愈重生（仍单例）
3. 并发请求串行排队，绝不拉起第二个 worker
"""
import io
import json
import threading
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
    """模拟 worker 子进程：记录 kill 状态。"""
    instances = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.killed = False
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()
        self.pid = 10000 + len(_FakeProc.instances)
        _FakeProc.instances.append(self)

    def poll(self):
        return "killed" if self.killed else None

    def kill(self):
        self.killed = True


def _provider(**cfg):
    base = {"fp16": False, "timeout_s": 5, "idle_kill_s": 1800}
    base.update(cfg)
    return cl.CosyVoiceLocalProvider(base)


class WorkerSingletonTest(unittest.TestCase):
    def setUp(self):
        _FakeProc.instances.clear()
        self._popen = mock.patch.object(
            cl.subprocess, "Popen", side_effect=lambda *a, **k: _FakeProc(*a, **k))
        self._popen.start()
        self._installed = mock.patch.object(cl, "_engine_installed",
                                            return_value=True)
        self._installed.start()
        self._reset()

    def tearDown(self):
        self._popen.stop()
        self._installed.stop()
        self._reset()

    def _reset(self):
        cl._WORKER_PROC, cl._WORKER_FP16 = None, None
        cl._WARM_STATE, cl._WARM_EXPIRES_AT, cl._WARM_BUSY = "cold", 0.0, 0
        cl._WARM_TAKEN.clear()
        if cl._WARM_TIMER:
            cl._WARM_TIMER.cancel()
        cl._WARM_TIMER = None

    def _warm(self, **cfg):
        """暖机到 ready（新语义下的测试前置）。"""
        r = _provider(**cfg).warmup()
        assert r["ok"], r
        _FakeProc.instances.clear()      # 暖机拉起的进程不计入后续断言

    def test_multiple_instances_share_one_worker(self):
        """核心防回归：暖机后，三个实例各发请求 → 不再拉新进程。"""
        self._warm()
        for _ in range(3):
            p = _provider()        # 模拟 get_provider() 每次新建实例
            resp = p._request({"cmd": "synth", "fp16": False})
            self.assertTrue(resp["ok"])
        self.assertEqual(len(_FakeProc.instances), 0,
                         "ready 态多实例必须复用同一 worker（0 个新进程）")

    def test_worker_down_then_respawn_single(self):
        """worker 意外退出后 → ready 态自愈重生一个（仍然单例）。"""
        self._warm()
        first = _FakeProc.instances  # 空；先发一次请求确认存活
        _provider()._request({"cmd": "synth", "fp16": False})
        self.assertEqual(len(first), 0)
        # 直接杀掉真实 worker 进程对象（绕过门禁模拟崩溃）
        cl._WORKER_PROC.killed = True
        _provider()._request({"cmd": "synth", "fp16": False})
        self.assertEqual(len(_FakeProc.instances), 1, "自愈恰好重生一个")
        self.assertEqual(cl.warm_info()["warm_state"], "ready")

    def test_concurrent_requests_still_one_worker(self):
        """并发请求串行排队，绝不拉起第二个 worker。"""
        self._warm()
        results = []
        errs = []

        def worker():
            try:
                p = self and _provider()
                results.append(p._request({"cmd": "synth", "fp16": False}))
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        ts = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertFalse(errs)
        self.assertEqual(len(results), 8)
        self.assertEqual(len(_FakeProc.instances), 0,
                         "并发场景不允许新拉 worker")

    def test_fp16_drift_after_warmup_rejected(self):
        """暖机后 fp16 漂移（未经 update 路径）→ 拒绝复用并回 cold。"""
        self._warm(fp16=False)
        with self.assertRaises(NotWarmedError):
            _provider(fp16=True)._request({"cmd": "synth", "fp16": True})
        self.assertEqual(cl.warm_info()["warm_state"], "cold")


if __name__ == "__main__":
    unittest.main()
