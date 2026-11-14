"""可观测：指标沉淀（demo 用本地 JSONL；生产换 Prometheus + Grafana）。"""

from __future__ import annotations

import time
from pathlib import Path

from listing_guard.runner import verdict_bucket
from listing_guard.settings import DATA_DIR, get_settings

METRICS_FILE = DATA_DIR / "metrics.jsonl"


def record_audit(
    audit_id: str,
    state: dict,
    elapsed_ms: int,
    history_steps: int,
) -> None:
    """一条审核一条 JSONL：因子耗时/降级/命中 + 决策 + 端到端时长。

    batch_eval 与 API 共用；后续策略迭代（阈值回调）直接消费此文件。
    """
    factors = state.get("factors", {})
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "audit_id": audit_id,
        "verdict": state.get("verdict"),
        "bucket": verdict_bucket(state.get("verdict", "unknown")),
        "score": state.get("score"),
        "policy_version": state.get("policy_version") or get_settings().policy_version,
        "elapsed_ms": elapsed_ms,
        "history_steps": history_steps,
        "degraded_factors": [k for k, v in factors.items() if v.get("degraded")],
        "veto_hits": state.get("veto_hits", []),
        "hit_factors": [k for k, v in factors.items() if v.get("hit")],
        "node_elapsed_ms": {k: v.get("elapsed_ms", 0) for k, v in factors.items()},
    }
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(__import__("json").dumps(row, ensure_ascii=False) + "\n")


def p95_elapsed_ms() -> float:
    """从指标文件算端到端 P95（按 audit_id 去重取最新；无数据返回 0）。"""
    rows = load_rows()
    if not rows:
        return 0.0
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r.get("audit_id", "?")] = r
    times = sorted(r.get("elapsed_ms", 0) for r in latest.values())
    idx = max(0, int(len(times) * 0.95) - 1)
    return float(times[idx])


def load_rows(limit: int = 0) -> list[dict]:
    if not METRICS_FILE.exists():
        return []
    import json

    rows = [json.loads(line) for line in METRICS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-limit:] if limit else rows


def dashboard_stats() -> dict:
    """Web 控制台顶部指标卡数据（按 audit_id 去重取最新一行：终态覆盖提交态）。"""
    rows = load_rows()
    if not rows:
        return {"total": 0}
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r.get("audit_id", "?")] = r  # 后写覆盖
    rows = list(latest.values())
    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r.get("bucket", "unknown")] = buckets.get(r.get("bucket", "unknown"), 0) + 1
    total = len(rows)
    auto = buckets.get("pass", 0) + buckets.get("reject", 0)
    return {
        "total": total,
        "buckets": buckets,
        "auto_coverage": round(auto / total, 4) if total else 0,
        "human_rate": round(buckets.get("review", 0) / total, 4) if total else 0,
        "p95_ms": p95_elapsed_ms(),
        "avg_ms": round(sum(r.get("elapsed_ms", 0) for r in rows) / total, 1),
        "degraded": sum(len(r.get("degraded_factors", [])) for r in rows),
    }


def reset_metrics(path: Path | None = None) -> None:
    (path or METRICS_FILE).unlink(missing_ok=True)
