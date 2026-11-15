"""CosyVoice3 本地引擎 Provider（M3）。

设计（见 docs/ARCHITECTURE.md §4.2 适配器矩阵与 §12 偏差表）：
- worker.py 常驻子进程：模型加载一次、多段复用（行 JSON 协议）
- 按需拉起：首次 synthesize/warmup 时启动；空闲 idle_kill_s 后回收（16G 内存保护）
- 并发恒 1：本地引擎物理单并发（子进程内串行），超时 kill 重启
- 音色：克隆音色 = 本地参考音频（ref_audio + ref_text），无需云端注册；
        registrations 里记录引用文件路径，删除音色 = 断开引用（文件归 voices 表所有）
- 输出 16-bit PCM WAV @24kHz，拼接层直接可用
"""
import json
import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

from app.providers.base import (Caps, SynthParams, TTSProvider,
                                TTSProviderError, VoiceMeta)
from app import settings

logger = logging.getLogger("vss.providers.cosyvoice")

# 引擎安装根目录（项目内自包含，卸载=删目录）
ENGINE_ROOT = os.path.normpath(os.path.join(
    settings.BACKEND_DIR, "..", "local_engines", "cosyvoice3"))
WORKER_PATH = os.path.join(ENGINE_ROOT, "worker.py")
ENGINE_PYTHON = os.path.join(ENGINE_ROOT, "cosyvoice_env", "bin", "python")

# 与 worker.py 的 RESP_PREFIX 约定一致（tqdm/日志混行过滤）
_RESP_PREFIX = "@@VSS@@"

# ---------------------------------------------------------------- worker 全局单例 + 暖机状态机
# 致命缺陷修复（2026-11-15 上午）：此前 worker 子进程挂在 provider 实例上，而
# get_provider() 每次调用都新建实例 → 每个任务/试听各自拉起独立 worker、
# 各自加载一份完整模型 → 多份模型叠加驻留曾把 16G 内存打爆、CPU 100% 全机卡死。
# 现改为模块级共享：全后端永远只有一个 worker、一份模型。
#
# 暖机门禁（2026-11-15 晚，用户定稿）：本地模型是 GB 级重资源，加载时机必须
# 由用户显式决定——warmup() 是**唯一**允许拉起 worker 的入口；合成/试听在
# 未暖机（cold）状态一律拒绝（NotWarmedError），绝不静默加载。
# 状态机：cold →(暖机)→ loading →(成功)→ ready →(30 分钟无使用/手动释放/配置变更)→ cold
# ready 采用滑动窗口续期：每次成功使用（合成/试听）重置 30 分钟窗口。
_WORKER_PROC: subprocess.Popen = None    # 当前 worker 子进程
_WORKER_FP16: bool = None                # 拉起时的 fp16 设置（用于检测配置漂移）
_WORKER_LOCK = threading.RLock()         # 全局串行（可重入，便于状态查询复用）
_WARM_STATE = "cold"                     # cold / loading / ready
_WARM_EXPIRES_AT = 0.0                   # ready 态自动释放时刻（绝对时间戳）
_WARM_BUSY = 0                           # 在途请求数（>0 时拒绝手动释放）
_WARM_TAKEN = threading.Event()          # 暖机会话互斥：防止并发暖机
_WARM_TIMER: threading.Timer = None      # 自动释放定时器
DEFAULT_IDLE_KILL_S = 1800               # 默认 30 分钟（用户定稿 2026-11-15）


class NotWarmedError(TTSProviderError):
    """本地引擎未暖机即被使用（前端应引导去设置页暖机）。"""

    def __init__(self, msg: str = ""):
        super().__init__(msg or
            "本地引擎未暖机：请到 设置 → 暖机引擎 加载模型后再合成", retryable=False)


def _engine_installed() -> bool:
    return os.path.isfile(WORKER_PATH) and os.path.isfile(ENGINE_PYTHON)


def _worker_start(fp16: bool) -> subprocess.Popen:
    """拉起全局唯一 worker（模型在其内部懒加载，全后端共享）。"""
    if not _engine_installed():
        raise TTSProviderError(
            "CosyVoice3 引擎未安装：local_engines/cosyvoice3 目录缺失",
            retryable=False)
    global _WORKER_PROC, _WORKER_FP16
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    _WORKER_PROC = subprocess.Popen(
        [ENGINE_PYTHON, WORKER_PATH],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,      # torch 警告极多，不进服务日志
        text=True, bufsize=1, env=env,
    )
    _WORKER_FP16 = bool(fp16)
    logger.info("CosyVoice3 worker 已拉起 (pid=%s, fp16=%s)",
                _WORKER_PROC.pid, _WORKER_FP16)
    return _WORKER_PROC


def _worker_kill() -> None:
    """结束当前 worker（配置变化/超时/异常时调用）。"""
    global _WORKER_PROC, _WORKER_FP16
    proc, _WORKER_PROC, _WORKER_FP16 = _WORKER_PROC, None, None
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


def _worker_ensure(fp16: bool) -> subprocess.Popen:
    """ready 态下取当前 worker；意外死亡则自愈重生（不影响门禁状态）。

    fp16 漂移在这里不再自动重启——配置变更时 update 路径会显式释放并回 cold。
    """
    proc = _WORKER_PROC
    if proc is not None and proc.poll() is None:
        if _WORKER_FP16 == bool(fp16):
            return proc
        logger.warning("fp16 配置漂移未走 update 路径，拒绝复用（请重新暖机）")
        _worker_kill()
        _set_state("cold")
        raise NotWarmedError("引擎配置已变更，请重新暖机")
    logger.info("worker 意外退出，ready 态自愈重生")
    return _worker_start(fp16)


# ------------------------------------------------------------ 暖机状态机

def _set_state(s: str) -> None:
    global _WARM_STATE
    _WARM_STATE = s
    if s == "cold":
        global _WARM_EXPIRES_AT
        _WARM_EXPIRES_AT = 0.0


def _renew_window(idle_kill_s: int) -> None:
    """滑动窗口续期：重置到期时刻与自动释放定时器（调用方须持锁）。"""
    global _WARM_EXPIRES_AT, _WARM_TIMER
    _WARM_EXPIRES_AT = time.time() + idle_kill_s
    if _WARM_TIMER:
        _WARM_TIMER.cancel()
    _WARM_TIMER = threading.Timer(idle_kill_s, _on_window_expired)
    _WARM_TIMER.daemon = True
    _WARM_TIMER.start()


def _on_window_expired() -> None:
    with _WORKER_LOCK:
        if _WARM_STATE != "ready" or time.time() < _WARM_EXPIRES_AT:
            return                          # 已被续期或已释放，本次作废
        if _WARM_BUSY > 0:
            _renew_window(60)               # 在途请求兜底：1 分钟后再试
            return
        logger.info("CosyVoice3 worker 30 分钟未使用，自动释放")
        _set_state("cold")
        _worker_kill()


def warm_info() -> Dict[str, Any]:
    """暖机状态查询（前端倒计时数据源；后端为唯一权威）。"""
    with _WORKER_LOCK:
        remaining = 0
        if _WARM_STATE == "ready":
            remaining = max(0, int(_WARM_EXPIRES_AT - time.time()))
            if remaining == 0 and _WARM_BUSY == 0:
                # 已到期但定时器尚未触发（毫秒级窗口）——就地释放，别让前端看到假 ready
                _set_state("cold")
                _worker_kill()
        proc = _WORKER_PROC
        return {
            "warm_state": _WARM_STATE,
            "remaining_s": remaining,
            "fp16": _WORKER_FP16,
            "busy": _WARM_BUSY > 0,
            "alive": bool(proc and proc.poll() is None),
        }


def release_worker() -> Dict[str, Any]:
    """手动释放（设置页「释放引擎」按钮）。合成进行中拒绝。"""
    with _WORKER_LOCK:
        if _WARM_STATE == "cold":
            return {"ok": True, "message": "引擎本就未加载"}
        if _WARM_BUSY > 0:
            return {"ok": False,
                    "message": "有任务正在使用引擎，请等待完成后再释放"}
        pid = _WORKER_PROC.pid if _WORKER_PROC else None
        _set_state("cold")
        _worker_kill()
        logger.info("CosyVoice3 worker 手动释放 (pid=%s)", pid)
        return {"ok": True, "message": "引擎已释放，模型已移出内存"}


def acquire_for_warmup() -> bool:
    """暖机会话入场券：True=可以开始暖机；False=已有暖机在进行。"""
    with _WORKER_LOCK:
        if _WARM_TAKEN.is_set():
            return False
        _WARM_TAKEN.set()
        _set_state("loading")
        return True


def finish_warmup(ok: bool, idle_kill_s: int) -> None:
    """暖机收尾：成功→ready+开窗口；失败→cold。"""
    with _WORKER_LOCK:
        try:
            if ok:
                _set_state("ready")
                _renew_window(idle_kill_s)
            else:
                _set_state("cold")
                _worker_kill()
        finally:
            _WARM_TAKEN.clear()


class WarmedGuard:
    """使用门禁：进入时校验 ready（cold/loading 拒绝），退出时递减在途数。"""

    def __enter__(self):
        global _WARM_BUSY
        with _WORKER_LOCK:
            if _WARM_STATE != "ready":
                if _WARM_STATE == "loading":
                    raise NotWarmedError("引擎正在暖机中，请稍候再试")
                raise NotWarmedError()
            _WARM_BUSY += 1
        return self

    def __exit__(self, *exc):
        global _WARM_BUSY
        with _WORKER_LOCK:
            _WARM_BUSY = max(0, _WARM_BUSY - 1)
        return False


class CosyVoiceLocalProvider(TTSProvider):
    """CosyVoice3-0.5B 本地引擎适配器。"""

    id = "cosyvoice_local"

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self.fp16 = bool(cfg.get("fp16", False))
        self.timeout_s = int(cfg.get("timeout_s", 600))
        self.idle_kill_s = int(cfg.get("idle_kill_s", DEFAULT_IDLE_KILL_S))
        # worker 子进程是模块级全局单例（见文件头部说明），实例不再持有

    # ------------------------------------------------------------ 能力

    def capabilities(self) -> Caps:
        installed = _engine_installed()
        return Caps(
            kind="local",
            display_name="CosyVoice3 本地（离线免费）",
            max_concurrency=1,
            supports_emotion_tags=True,   # [laughter]/[breath] 等事件标签原生支持
            sample_rate_hz=24000,
            has_preset_voices=False,      # 克隆型引擎，无预置音色
            needs_registration=False,     # 本地引用文件即可，无需"注册"动作
            supported_params={"speed": True, "volume": False},
            # （inference_zero_shot(speed=) 原生支持，经 worker 协议透传；
            #  音量无原生接口，拼接层也不做增益——前端不显示音量滑杆）
            notes=("Apache-2.0 可商用；完全离线；合成慢于实时（RTF≈2~5），"
                   "适合后台批量" + ("" if installed else "；⚠️ 引擎未安装"
                                     "（local_engines/cosyvoice3 缺失）")),
        )

    # ------------------------------------------------------------ 请求（全局单例 worker + 暖机门禁）

    def _request(self, req: Dict, *, guarded: bool = True) -> Dict:
        """向全局 worker 发一行请求并等一行带标记的响应。

        - guarded=True（默认，合成/试听路径）：必须已暖机（ready），否则
          NotWarmedError——绝不静默拉起 worker（暖机门禁核心）。
        - guarded=False：仅暖机流程自身使用（此时持暖机会话券）。
        全局锁串行：并发请求在锁上排队，绝不拉起第二个 worker。
        """
        global _WARM_BUSY
        with _WORKER_LOCK:
            if guarded:
                if _WARM_STATE != "ready":
                    if _WARM_STATE == "loading":
                        raise NotWarmedError("引擎正在暖机中，请稍候再试")
                    raise NotWarmedError()
                _WARM_BUSY += 1
            proc = _worker_ensure(bool(req.get("fp16", self.fp16)))
            deadline = time.time() + self.timeout_s
            try:
                proc.stdin.write(json_dumps(req) + "\n")
                proc.stdin.flush()
                # 只认 @@VSS@@ 前缀行（worker 的 tqdm/日志混在 stdout）
                line = ""
                while time.time() < deadline:
                    line = _readline_with_timeout(
                        proc, max(1, int(deadline - time.time())))
                    if line.startswith(_RESP_PREFIX):
                        break
                else:
                    raise subprocess.TimeoutExpired(
                        cmd="worker", timeout=self.timeout_s)
                if not line.startswith(_RESP_PREFIX):
                    raise subprocess.TimeoutExpired(
                        cmd="worker", timeout=self.timeout_s)
                resp = json.loads(line[len(_RESP_PREFIX):])
                if not resp.get("ok"):
                    raise TTSProviderError(
                        resp.get("error", "未知错误"),
                        retryable=bool(resp.get("retryable")))
                # 成功使用 → 滑动窗口续期（30 分钟从现在重新计）
                if guarded:
                    _renew_window(self.idle_kill_s)
                return resp
            except subprocess.TimeoutExpired:
                _worker_kill()
                _set_state("cold")   # worker 异常 → 不可信，回未暖机
                raise TTSProviderError(
                    f"本地合成超时（>{self.timeout_s}s），请重新暖机",
                    retryable=True)
            except (BrokenPipeError, OSError, json.JSONDecodeError) as e:
                _worker_kill()
                _set_state("cold")
                raise TTSProviderError(
                    f"worker 通信异常（{type(e).__name__}），请重新暖机",
                    retryable=True)
            finally:
                if guarded:
                    _WARM_BUSY = max(0, _WARM_BUSY - 1)

    # ------------------------------------------------------------ 契约实现

    def test_connection(self) -> Dict[str, Any]:
        """轻量检查（永不加载模型）：引擎文件是否完整 + 当前暖机状态。"""
        if not _engine_installed():
            return {"ok": False,
                    "message": "引擎未安装（local_engines/cosyvoice3 缺失）"}
        info = warm_info()
        if info["warm_state"] == "ready":
            mm, ss = divmod(info["remaining_s"], 60)
            return {"ok": True,
                    "message": f"引擎已就绪（剩余 {mm:02d}:{ss:02d} 自动释放）"}
        if info["warm_state"] == "loading":
            return {"ok": True, "message": "引擎正在暖机中……"}
        return {"ok": True, "message": "引擎已安装，未暖机（点「暖机引擎」加载模型）"}

    def warmup(self) -> Dict[str, Any]:
        """暖机（唯一加载入口）：拉起 worker 并加载权重（首次约 80 秒）。

        并发暖机互斥；成功 → ready + 30 分钟滑动窗口；失败 → 回 cold。
        """
        if not _engine_installed():
            return {"ok": False,
                    "message": "引擎未安装（local_engines/cosyvoice3 缺失）"}
        if not acquire_for_warmup():
            return {"ok": False, "message": "已有暖机在进行中，请稍候"}
        t0 = time.time()
        try:
            with _WORKER_LOCK:
                if _WORKER_PROC is None or _WORKER_PROC.poll() is not None:
                    _worker_start(self.fp16)
            r = self._request({"cmd": "warmup", "fp16": self.fp16},
                              guarded=False)
        except TTSProviderError as e:
            finish_warmup(False, self.idle_kill_s)
            return {"ok": False, "message": str(e)}
        finish_warmup(True, self.idle_kill_s)
        return {"ok": True,
                "message": f"{r['message']}，耗时 {time.time() - t0:.1f}s，"
                           f"{self.idle_kill_s // 60} 分钟内免重新加载"}

    def synthesize(self, text: str, voice_key: str,
                   out_path: str, params: SynthParams) -> Dict[str, Any]:
        """单段合成。voice_key 必须是 clone:<本地uuid>（本地引擎无预置音色）。"""
        if not voice_key.startswith("clone:"):
            raise TTSProviderError(
                "本地引擎仅支持克隆音色（clone:*），请先在音色库上传/录音",
                retryable=False)
        from app.store import db
        voice = db.get_voice(voice_key[len("clone:"):])
        if voice is None or not os.path.isfile(voice["ref_path"]):
            raise TTSProviderError(
                f"参考音频缺失（{voice['name'] if voice else voice_key}），"
                "请在音色库重新上传", retryable=False)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        self._request({
            "cmd": "synth",
            "text": text,
            "ref_path": voice["ref_path"],
            "ref_text": voice["ref_text"],
            "out_path": os.path.abspath(out_path),
            "fp16": self.fp16,
            "speed": max(0.5, min(float(params.speed), 2.0)),  # worker 协议 P2-0 新增
        })
        return {"audio_path": out_path, "sample_rate_hz": 24000}

    def register_clone_voice(self, ref_path: str, ref_text: str,
                             name: str) -> VoiceMeta:
        """本地引擎"注册"= 记录引用文件（无需任何网络动作）。

        注意 voice_key 必须回传本地 uuid（voices 表主键），因为注册时
        上传方尚未可知 uuid → 由 API 层创建后再落 registrations；
        这里返回占位 key，真实 key 由调用方覆写（见 api/voices.py）。
        """
        return VoiceMeta(voice_key=f"local_ref:{name}", name=name,
                         extra={"engine": self.id})

    def delete_voice(self, voice_key: str) -> None:
        pass  # 引用文件归 voices 表所有，由 voices API 统一清理

    def strip_emotion_tags(self, text: str) -> str:
        """CosyVoice3 支持 [laughter] 等事件标签，但语义与 Fish 不同；
        为避免误读，统一剥离 Fish 系标签（音频停顿已在拼接层处理）。"""
        return TTSProvider.strip_emotion_tags(self, text)


# ---------------------------------------------------------------- 小工具

def json_dumps(obj: Dict) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _readline_with_timeout(proc: subprocess.Popen, timeout_s: int) -> str:
    """worker 响应行读取（超时守护线程 + 队列，避免阻塞读卡死）。"""
    import queue
    q: "queue.Queue[str]" = queue.Queue(maxsize=1)

    def _reader():
        try:
            q.put(proc.stdout.readline())
        except Exception:  # noqa: BLE001
            q.put("")

    threading.Thread(target=_reader, daemon=True).start()
    try:
        return q.get(timeout=timeout_s)
    except queue.Empty:
        raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout_s)
