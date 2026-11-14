"""上架房源库（demo 用 JSONL；生产对应「上架房源表」MySQL）。

写入点：
- decide 节点 verdict=auto_approve（自动审核上架）
- human_review 节点 verdict=human_approved（人工放行上架）

同时支持从检查点回填（GET /listings 时幂等合并）：
检查点库里已有终态为上架的审核单（历史跑过的），首次查询时补录，
时间戳以回填时刻记录并标 backfilled=true。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from listing_guard.settings import DATA_DIR, get_settings

SNAPSHOT_FIELDS = ["title", "city", "community", "price", "rooms", "area", "floor", "orientation", "agent_id"]
PUBLISHED_VERDICTS = ("auto_approve", "human_approved")


def _file() -> Path:
    return Path(os.environ.get("LISTING_GUARD_PUBLISHED") or DATA_DIR / "published.jsonl")


def publish(state: dict) -> None:
    """终态上架时写一行（同一单幂等：重复写会去重，后写覆盖）。"""
    verdict = state.get("verdict")
    if verdict not in PUBLISHED_VERDICTS:
        return
    listing = state.get("listing") or {}
    row = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "audit_id": state.get("audit_id"),
        "verdict": verdict,
        "source": "auto" if verdict == "auto_approve" else "human",
        "score": state.get("score"),
        "policy_version": state.get("policy_version") or get_settings().policy_version,
        "listing": {k: listing.get(k) for k in SNAPSHOT_FIELDS},
        "human_decision": state.get("human_decision"),
    }
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows()
    rows = [r for r in rows if r.get("audit_id") != row["audit_id"]] + [row]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _rows() -> list[dict]:
    path = _file()
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def list_published(source: str = "all") -> list[dict]:
    """上架列表（时间倒序）；source=auto|human 筛选决策来源。"""
    rows = _rows()
    if source in ("auto", "human"):
        rows = [r for r in rows if r.get("source") == source]
    return sorted(rows, key=lambda r: r.get("ts", ""), reverse=True)


def sync_from_checkpoints(graph_app) -> int:
    """从检查点库回填历史已上架审核单（幂等），返回补录条数。"""
    import sqlite3  # noqa: PLC0415

    db_path = Path(os.environ.get("LISTING_GUARD_DB") or get_settings().checkpoint_path)
    if not db_path.exists():
        return 0

    known = {r.get("audit_id") for r in _rows()}
    added = 0
    conn = sqlite3.connect(db_path)
    try:
        thread_ids = {
            r[0]
            for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
        }
    finally:
        conn.close()

    cfg = {"configurable": {}}
    for tid in thread_ids:
        if tid in known:
            continue
        cfg["configurable"]["thread_id"] = tid
        snap = graph_app.get_state(cfg)
        values = snap.values
        if values.get("verdict") in PUBLISHED_VERDICTS:
            values = {**values, "audit_id": tid, "backfilled": True}
            publish(values)
            added += 1
    return added
