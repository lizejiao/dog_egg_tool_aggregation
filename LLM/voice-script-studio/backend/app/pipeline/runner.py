"""任务引擎：状态机、段级调度、断点续跑（docs/ARCHITECTURE.md §4.4）。

状态机：created → splitting → synthesizing → concatenating → done
                                                    └→ partial_failed（有失败段）
设计要点：
- 每个 task 一个后台线程，线程内用 ThreadPoolExecutor 做段级并发
  （并发上限 = provider Caps.max_concurrency，云端≤2 / mock=4）
- 断点续跑：每段成功立即落盘并更新 DB；线程退出后重启进程，任何时刻对
  task 调 start_or_resume() 都只处理 pending/failed 段（ok 段自动跳过）
- 可重试错误（429/5xx/超时/OOM）在段内做指数退避重试（0.5/1/2s），
  不可重试错误（鉴权/超限）直接落 failed，等人工处理
- 同一 task 同时只允许一个线程运行（_RUNNING 锁注册表）
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app import settings
from app.pipeline import assembler, splitter, text_clean
from app.providers import get_provider, strip_or_keep_tags
from app.providers.base import SynthParams, TTSProviderError
from app.store import config as config_store
from app.store import db

logger = logging.getLogger("vss.runner")

# task_id → Thread：运行中任务注册表（防止同一任务并发起跑）
_RUNNING: Dict[str, threading.Thread] = {}
_RUNNING_LOCK = threading.Lock()

# 段合成线程池的统一退出等待时间
_JOIN_TIMEOUT = 120


class TaskRunner:
    """任务编排器（无状态方法 + 模块级运行注册表，便于测试替换）。"""

    # ------------------------------------------------------------ 创建任务

    @staticmethod
    def create_task(title: str, raw_text: str, voice_key: str,
                    provider_id: str, params: Optional[Dict] = None) -> str:
        """清洗→分句→落库，返回 task_id。不自动启动，由 API 层调 start。"""
        cfg = config_store.load_config()["synthesis"]
        max_chars = int(params.get("chars_per_segment", cfg["chars_per_segment"])) \
            if params else int(cfg["chars_per_segment"])

        cleaned = text_clean.clean_script(raw_text)
        segments = splitter.split_into_segments(cleaned, max_chars=max_chars)
        if not segments:
            raise ValueError("清洗后无有效文本，请检查稿件内容")

        provider = get_provider(provider_id)  # 校验 provider 可用（未启用会抛错）
        work_dir = os.path.join(settings.TASKS_DIR, db.new_id())
        os.makedirs(work_dir, exist_ok=True)
        task_id = db.create_task(
            title=title or "未命名稿件", voice_key=voice_key,
            provider_id=provider_id, params=params or {}, work_dir=work_dir,
            segments=segments,
        )
        logger.info("任务创建 %s：%d 段，provider=%s voice=%s",
                    task_id, len(segments), provider_id, voice_key)
        return task_id

    # ------------------------------------------------------------ 启动/续跑

    @staticmethod
    def start_or_resume(task_id: str) -> Dict[str, Any]:
        """启动或续跑任务。已在运行 → 返回 running；已完成 → 幂等返回状态。

        本地引擎暖机门禁（用户定稿 2026-11-15）：开始合成前校验 ready，
        未暖机/暖机中 → 409 语义错误（创建任务放行，仅开始时拦截）。
        """
        task = db.get_task(task_id)
        if task is None:
            raise KeyError(f"任务不存在: {task_id}")
        if task["status"] in ("done",):
            return {"task_id": task_id, "status": "done", "message": "任务已完成"}

        if task["provider_id"] == "cosyvoice_local":
            from app.providers.cosyvoice_local import NotWarmedError, warm_info
            wi = warm_info()
            if wi["warm_state"] != "ready":
                raise NotWarmedError()   # API 层转 409 + not_warmed

        with _RUNNING_LOCK:
            thread = _RUNNING.get(task_id)
            if thread is not None and thread.is_alive():
                return {"task_id": task_id, "status": "running",
                        "message": "任务已在运行中"}
            t = threading.Thread(target=_run_task, args=(task_id,),
                                  daemon=True, name=f"task-{task_id[:8]}")
            _RUNNING[task_id] = t
            t.start()
        return {"task_id": task_id, "status": "started"}

    @staticmethod
    def is_running(task_id: str) -> bool:
        with _RUNNING_LOCK:
            t = _RUNNING.get(task_id)
            return t is not None and t.is_alive()

    @staticmethod
    def retry_segment(task_id: str, seq: int) -> Dict[str, Any]:
        """失败段重试：failed → pending，若任务空闲则立即续跑。"""
        if not db.reset_segment_for_retry(task_id, seq):
            raise ValueError(f"段 {seq} 不存在或非 failed 状态")
        db.set_task_status(task_id, "synthesizing")
        return TaskRunner.start_or_resume(task_id)


# ---------------------------------------------------------------- 工作线程

def _run_task(task_id: str) -> None:
    """任务主循环（后台线程体）。崩溃安全：任何异常都收敛到任务状态。"""
    try:
        task = db.get_task(task_id)
        provider_id = task["provider_id"]
        provider = get_provider(provider_id)
        caps = provider.capabilities()
        synth_cfg = config_store.load_config()["synthesis"]

        # 崩溃恢复：上次运行中断的 running 段复位
        db.reset_running_segments(task_id)
        db.set_task_status(task_id, "synthesizing")

        remaining = [s for s in db.get_segments(task_id)
                     if s["status"] in ("pending",)]
        if remaining:
            _synthesize_pending(task_id, provider, caps, synth_cfg)

        # 终态判定：以段表实时统计为准
        counts = db.task_counts(task_id)
        if counts["pending"] > 0:
            # 仍有 pending（可能被外部重置）→ 保持 synthesizing 等下次触发
            db.set_task_status(task_id, "synthesizing")
            return
        if counts["failed"] > 0:
            db.set_task_status(task_id, "partial_failed")
            logger.info("任务 %s 结束：partial_failed（失败 %d 段）",
                        task_id, counts["failed"])
            return

        # 全部成功 → 拼接
        db.set_task_status(task_id, "concatenating")
        out_path = _concatenate(task_id, synth_cfg)
        db.set_task_status(task_id, "done", output_path=out_path)
        logger.info("任务 %s 完成：%s", task_id, out_path)
    except Exception as e:  # noqa: BLE001 —— 后台线程兜底，任何异常都不能静默吞掉
        logger.exception("任务 %s 异常终止", task_id)
        db.set_task_status(task_id, "failed")
        # 失败原因写入任务标题旁不合适，落在日志 + 段表 error 即可
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(task_id, None)


def _synthesize_pending(task_id: str, provider, caps, synth_cfg: Dict) -> None:
    """段级调度：线程池并发合成 pending 段，成功即落盘（断点续跑的写侧）。"""
    task = db.get_task(task_id)
    voice_key = task["voice_key"]
    engine_voice_key = _resolve_voice_key(task_id, provider.id, voice_key)
    # 参数按引擎能力守门（P2-0）：Caps 未声明的参数不传，引擎端拿到的恒为默认值
    tp = task.get("params") or {}
    sp = caps.supported_params or {}
    params = SynthParams(
        emotion_tags_allowed=caps.supports_emotion_tags,
        speed=max(0.5, min(float(tp.get("speed", 1.0)), 2.0))
        if sp.get("speed") else 1.0,
        volume_db=max(-20.0, min(float(tp.get("volume_db", 0.0)), 20.0))
        if sp.get("volume") else 0.0,
    )

    def work(seg: Dict) -> None:
        _synthesize_one(task_id, provider, caps, engine_voice_key,
                        params, seg, synth_cfg)

    with ThreadPoolExecutor(max_workers=caps.max_concurrency) as pool:
        # 逐段提交；claim_segment 的乐观锁保证不重复消费
        for seg in db.get_segments(task_id):
            if seg["status"] == "pending" and db.claim_segment(task_id, seg["seq"]):
                pool.submit(work, seg)


def _synthesize_one(task_id: str, provider, caps, engine_voice_key: str,
                    params: SynthParams, seg: Dict, synth_cfg: Dict) -> None:
    """单段合成 + 指数退避重试。任何结果都落 DB（ok/failed）。"""
    seq = seg["seq"]
    out_path = os.path.join(db.get_task(task_id)["work_dir"], f"seg_{seq:04d}.wav")
    text = strip_or_keep_tags(seg["text"], caps, params)

    max_retries = int(synth_cfg.get("max_retries", 3))
    for attempt in range(max_retries):
        try:
            result = provider.synthesize(text, engine_voice_key, out_path, params)
            db.set_segment_ok(task_id, seq, result["audio_path"],
                              int(result.get("duration_ms", 0)))
            return
        except TTSProviderError as e:
            if not e.retryable or attempt == max_retries - 1:
                db.set_segment_failed(task_id, seq, str(e))
                logger.warning("任务 %s 段 %d 失败：%s", task_id, seq, e)
                return
            time.sleep(0.5 * (2 ** attempt))  # 0.5s / 1s / 2s
        except Exception as e:  # noqa: BLE001 —— 引擎实现缺陷也不能拖垮线程池
            db.set_segment_failed(task_id, seq, f"引擎异常: {e}")
            logger.exception("任务 %s 段 %d 引擎异常", task_id, seq)
            return
    # 理论不可达：循环内必然 return
    db.set_segment_failed(task_id, seq, "重试次数耗尽")


def _resolve_voice_key(task_id: str, provider_id: str, voice_key: str) -> str:
    """任务 voice_key → 引擎级 key。三种形态（与 API 试听解析规则一致）：

    - "voiceent:<音色id>"   P2-0 新模型：查 voice_entities（新前端用）
    - "clone:<素材id>"      旧模型：查 DB registrations（旧前端/历史任务兼容）
    - "preset:<id>"        原样透传
    未注册/已删除给出明确报错。
    """
    if voice_key.startswith("voiceent:"):
        ent = db.get_voice_entity(voice_key[len("voiceent:"):])
        if ent is None:
            raise KeyError(f"音色不存在或已删除: {voice_key}")
        if ent["engine"] != provider_id:
            raise PermissionError(
                f"音色「{ent['name']}」归属引擎 {ent['engine']}，与任务引擎 {provider_id} 不符")
        return ent["voice_key"]
    if not voice_key.startswith("clone:"):
        return voice_key
    local_id = voice_key[len("clone:"):]
    voice = db.get_voice(local_id)
    if voice is None:
        raise KeyError(f"克隆音色不存在: {local_id}")
    reg = voice["registrations"].get(provider_id)
    if not reg or not reg.get("voice_key"):
        raise PermissionError(
            f"音色「{voice['name']}」尚未注册到引擎 {provider_id}，"
            f"请在音色库页补注册后重试")
    return reg["voice_key"]


def _concatenate(task_id: str, synth_cfg: Dict) -> str:
    """拼接整稿，返回输出路径。"""
    task = db.get_task(task_id)
    segs = db.get_segments(task_id)
    ok_segs = [s for s in segs if s["status"] == "ok"]
    if not ok_segs:
        raise RuntimeError("无成功段可拼接")
    para_break_map = {s["seq"]: bool(s["para_break"]) for s in ok_segs}
    params = task.get("params") or {}
    out_path = os.path.join(task["work_dir"], "final.wav")
    assembler.concat_segments(
        [s["audio_path"] for s in ok_segs],
        out_path,
        pause_between_s=float(params.get("pause_between",
                                         synth_cfg["pause_between"])),
        pause_between_paragraphs_s=float(params.get(
            "pause_between_paragraphs", synth_cfg["pause_between_paragraphs"])),
        para_break_map=para_break_map,
    )
    return out_path
