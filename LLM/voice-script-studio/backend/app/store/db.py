"""SQLite 存储层：voices / tasks / segments 三张核心表。

- 连接策略：每次操作新建短连接 + 写操作持全局锁。单机单用户场景下足够，
  避免了多线程共享连接的隐患，也无需引入 ORM。
- 设计偏差说明：docs/ARCHITECTURE.md §6 原有 providers 表，实现时合并进
  config.json（§4.6 本就承载了 Provider 配置，两处存储反而易失同步）。
"""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from app import settings

# 全局写锁：SQLite 写操作串行化（读操作并发安全）
_WRITE_LOCK = threading.Lock()


@contextmanager
def _conn():
    """短连接上下文：退出时提交/回滚并**关闭**连接。

    注：sqlite3 连接自带的 with 只管事务不关连接，会泄漏文件描述符，
    故此处用 contextmanager 显式 close（消除 ResourceWarning）。
    """
    conn = sqlite3.connect(settings.DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """建表（幂等）。应用启动时调用。"""
    settings.ensure_dirs()
    with _WRITE_LOCK, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS voices (
                id          TEXT PRIMARY KEY,          -- 本地 uuid
                name        TEXT NOT NULL,
                ref_path    TEXT NOT NULL,            -- 参考音频归档路径
                ref_text    TEXT NOT NULL,            -- 逐字稿（必填）
                registrations TEXT NOT NULL DEFAULT '{}',
                            -- JSON: {provider_id: {"voice_key": ..., "state": ...}}
                status      TEXT NOT NULL DEFAULT 'active',  -- active / broken
                created_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,          -- 本地 uuid
                title       TEXT NOT NULL,
                voice_key   TEXT NOT NULL,            -- clone:<id> 或 preset:<provider>:<ref>
                provider_id TEXT NOT NULL,
                params_json TEXT NOT NULL DEFAULT '{}',
                status      TEXT NOT NULL,            -- created/splitting/synthesizing/
                                                       -- concatenating/done/partial_failed/failed
                total_segments   INTEGER NOT NULL DEFAULT 0,
                ok_segments      INTEGER NOT NULL DEFAULT 0,
                failed_segments  INTEGER NOT NULL DEFAULT 0,
                work_dir    TEXT NOT NULL,            -- 分段音频目录
                output_path TEXT,                     -- 整稿 wav 路径
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS segments (
                task_id     TEXT NOT NULL,
                seq         INTEGER NOT NULL,         -- 段序号，从 1 开始
                text        TEXT NOT NULL,
                para_break  INTEGER NOT NULL DEFAULT 0,  -- 该段之前是否为段落边界
                status      TEXT NOT NULL,            -- pending/running/ok/failed
                audio_path  TEXT,
                error       TEXT,
                attempts    INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER,
                PRIMARY KEY (task_id, seq)
            );
            """
        )


# ---------------------------------------------------------------- voices

def new_id() -> str:
    return uuid.uuid4().hex


def create_voice(name: str, ref_path: str, ref_text: str,
                 registrations: Optional[Dict[str, Any]] = None) -> str:
    """新建克隆音色记录，返回本地 id。"""
    vid = new_id()
    now = time.time()
    with _WRITE_LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO voices (id, name, ref_path, ref_text, registrations, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (vid, name, ref_path, ref_text,
             json.dumps(registrations or {}, ensure_ascii=False), "active", now),
        )
    return vid


def get_voice(vid: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM voices WHERE id=?", (vid,)).fetchone()
    if row is None:
        return None
    return _voice_row(row)


def list_voices() -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM voices WHERE status != 'deleted' ORDER BY created_at DESC"
        ).fetchall()
    return [_voice_row(r) for r in rows]


def update_voice_registrations(vid: str, registrations: Dict[str, Any]) -> None:
    """更新音色的引擎注册表（如 fish_cloud 返回的 model_id / state）。"""
    with _WRITE_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE voices SET registrations=? WHERE id=?",
            (json.dumps(registrations, ensure_ascii=False), vid),
        )


def delete_voice(vid: str) -> None:
    """软删除：保留记录便于追溯，状态标记 deleted。"""
    with _WRITE_LOCK, _conn() as conn:
        conn.execute("UPDATE voices SET status='deleted' WHERE id=?", (vid,))


def _voice_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["registrations"] = json.loads(d.get("registrations") or "{}")
    return d


# ---------------------------------------------------------------- tasks / segments

def create_task(title: str, voice_key: str, provider_id: str,
                params: Dict[str, Any], work_dir: str,
                segments: List[Dict[str, Any]]) -> str:
    """建任务 + 段落行（同一事务），返回 task_id。

    segments: [{seq, text, para_break}]
    """
    tid = new_id()
    now = time.time()
    with _WRITE_LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, voice_key, provider_id, params_json, status, "
            "total_segments, work_dir, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, title, voice_key, provider_id, json.dumps(params, ensure_ascii=False),
             "created", len(segments), work_dir, now, now),
        )
        conn.executemany(
            "INSERT INTO segments (task_id, seq, text, para_break, status) "
            "VALUES (?,?,?,?, 'pending')",
            [(tid, s["seq"], s["text"], int(s.get("para_break", False))) for s in segments],
        )
    return tid


def get_task(tid: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["params"] = json.loads(d.get("params_json") or "{}")
    return d


def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, voice_key, provider_id, status, total_segments, "
            "ok_segments, failed_segments, created_at, updated_at "
            "FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_task_status(tid: str, status: str,
                    output_path: Optional[str] = None) -> None:
    with _WRITE_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, output_path=COALESCE(?, output_path), updated_at=? "
            "WHERE id=?", (status, output_path, time.time(), tid),
        )


def get_segments(tid: str) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM segments WHERE task_id=? ORDER BY seq", (tid,)
        ).fetchall()
    return [dict(r) for r in rows]


def claim_segment(tid: str, seq: int) -> bool:
    """乐观领取一个 pending 段（防重复调度）：pending → running。"""
    with _WRITE_LOCK, _conn() as conn:
        cur = conn.execute(
            "UPDATE segments SET status='running' WHERE task_id=? AND seq=? AND status='pending'",
            (tid, seq),
        )
        return cur.rowcount > 0


def reset_running_segments(tid: str) -> int:
    """把 running 段复位为 pending（进程崩溃后恢复用）。"""
    with _WRITE_LOCK, _conn() as conn:
        cur = conn.execute(
            "UPDATE segments SET status='pending' WHERE task_id=? AND status='running'", (tid,)
        )
        return cur.rowcount


def set_segment_ok(tid: str, seq: int, audio_path: str, duration_ms: int) -> None:
    """段落盘成功：状态 ok + 计数器累加（计数器由任务状态统一派生，此处只更新段）。"""
    with _WRITE_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE segments SET status='ok', audio_path=?, duration_ms=?, error=NULL, "
            "attempts=attempts+1 WHERE task_id=? AND seq=?",
            (audio_path, duration_ms, tid, seq),
        )
        conn.execute(
            "UPDATE tasks SET ok_segments=ok_segments+1, updated_at=? WHERE id=?",
            (time.time(), tid),
        )


def set_segment_failed(tid: str, seq: int, error: str) -> None:
    with _WRITE_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE segments SET status='failed', error=?, attempts=attempts+1 "
            "WHERE task_id=? AND seq=?", (error, tid, seq),
        )
        conn.execute(
            "UPDATE tasks SET failed_segments=failed_segments+1, updated_at=? WHERE id=?",
            (time.time(), tid),
        )


def reset_segment_for_retry(tid: str, seq: int) -> bool:
    """手动重试单段：failed → pending（ok 段不可重置，防误触）。"""
    with _WRITE_LOCK, _conn() as conn:
        cur = conn.execute(
            "UPDATE segments SET status='pending', error=NULL "
            "WHERE task_id=? AND seq=? AND status='failed'",
            (tid, seq),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE tasks SET failed_segments=MAX(failed_segments-1, 0), "
                "updated_at=? WHERE id=?", (time.time(), tid),
            )
        return cur.rowcount > 0


def task_counts(tid: str) -> Dict[str, int]:
    """从段表实时统计（任务表的计数器仅作快速展示，此处用于终态判定）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT status, COUNT(*) AS n FROM segments WHERE task_id=? GROUP BY status", (tid,)
        ).fetchall()
    counts = {r["status"]: r["n"] for r in row}
    return {
        "total": sum(counts.values()),
        "ok": counts.get("ok", 0),
        "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0) + counts.get("running", 0),
    }
