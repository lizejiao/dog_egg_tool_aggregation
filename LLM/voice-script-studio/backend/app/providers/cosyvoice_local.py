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


def _engine_installed() -> bool:
    return os.path.isfile(WORKER_PATH) and os.path.isfile(ENGINE_PYTHON)


class CosyVoiceLocalProvider(TTSProvider):
    """CosyVoice3-0.5B 本地引擎适配器。"""

    id = "cosyvoice_local"

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self.fp16 = bool(cfg.get("fp16", False))
        self.timeout_s = int(cfg.get("timeout_s", 600))
        self.idle_kill_s = int(cfg.get("idle_kill_s", 300))
        self._proc: subprocess.Popen = None
        self._lock = threading.Lock()        # 并发恒 1（子进程级互斥）
        self._last_used = 0.0
        self._idle_timer: threading.Timer = None

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
            notes=("Apache-2.0 可商用；完全离线；合成慢于实时（RTF≈2~5），"
                   "适合后台批量" + ("" if installed else "；⚠️ 引擎未安装"
                                     "（local_engines/cosyvoice3 缺失）")),
        )

    # ------------------------------------------------------------ 生命周期

    def _start_worker(self) -> subprocess.Popen:
        """拉起 worker 子进程（模型在其内部懒加载）。"""
        if not _engine_installed():
            raise TTSProviderError(
                "CosyVoice3 引擎未安装：local_engines/cosyvoice3 目录缺失",
                retryable=False)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self._proc = subprocess.Popen(
            [ENGINE_PYTHON, WORKER_PATH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,      # torch 警告极多，不进服务日志
            text=True, bufsize=1, env=env,
        )
        self._watch_idle()
        logger.info("CosyVoice3 worker 已拉起 (pid=%s)", self._proc.pid)
        return self._proc

    def _ensure_worker(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            self._start_worker()
        return self._proc

    def _watch_idle(self) -> None:
        """空闲回收定时器：idle_kill_s 无任务则结束子进程释放内存。"""
        if self._idle_timer:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self.idle_kill_s, self._kill_if_idle)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _kill_if_idle(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None \
                    and time.time() - self._last_used >= self.idle_kill_s:
                logger.info("CosyVoice3 worker 空闲回收 (pid=%s)", self._proc.pid)
                try:
                    self._proc.kill()
                finally:
                    self._proc = None

    def _request(self, req: Dict) -> Dict:
        """向 worker 发一行请求并等一行带标记的响应（tqdm 输出会被跳过）。"""
        deadline = time.time() + self.timeout_s
        with self._lock:
            self._last_used = time.time()
            proc = self._ensure_worker()
            try:
                proc.stdin.write(json_dumps(req) + "\n")
                proc.stdin.flush()
                # 只认 @@VSS@@ 前缀行（worker 的 tqdm/日志混在 stdout）
                line = ""
                while time.time() < deadline:
                    line = _readline_with_timeout(proc, int(deadline - time.time()) + 1)
                    if line.startswith(_RESP_PREFIX):
                        break
                else:
                    raise subprocess.TimeoutExpired(cmd="worker", timeout=self.timeout_s)
                if not line.startswith(_RESP_PREFIX):
                    raise subprocess.TimeoutExpired(cmd="worker", timeout=self.timeout_s)
                resp = json.loads(line[len(_RESP_PREFIX):])
                self._watch_idle()
                if not resp.get("ok"):
                    raise TTSProviderError(
                        resp.get("error", "未知错误"),
                        retryable=bool(resp.get("retryable")))
                return resp
            except subprocess.TimeoutExpired:
                self._kill_worker()
                raise TTSProviderError(
                    f"本地合成超时（>{self.timeout_s}s），worker 已重启",
                    retryable=True)
            except (BrokenPipeError, OSError, json.JSONDecodeError) as e:
                self._kill_worker()
                raise TTSProviderError(
                    f"worker 通信异常（{type(e).__name__}），已重启",
                    retryable=True)

    def _kill_worker(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None

    # ------------------------------------------------------------ 契约实现

    def test_connection(self) -> Dict[str, Any]:
        if not _engine_installed():
            return {"ok": False,
                    "message": "引擎未安装（local_engines/cosyvoice3 缺失）"}
        try:
            r = self._request({"cmd": "warmup", "fp16": self.fp16})
            return {"ok": True, "message": r["message"] + "，本地引擎可用"}
        except TTSProviderError as e:
            return {"ok": False, "message": str(e)}

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
