"""批量评测：全部 fixture 跑一遍，输出混淆矩阵与核心指标。

指标对应简历口径：
- 自动审核覆盖率（auto_approve + auto_reject 占比，目标 ~80%+）
- 转人工率（低置信样本精准集中人工；人工裁决结果不影响该口径）
- 误杀数（expected=pass 但被判 auto_reject —— 误杀成本高于漏放）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from listing_guard.runner import load_fixture, run_audit, verdict_bucket
from listing_guard.settings import DATA_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="批量评测")
    parser.add_argument("--db", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    fixtures = [
        load_fixture(p.stem)
        for p in sorted((DATA_DIR / "fixtures").glob("*.json"))
    ]

    rows = []
    start = time.perf_counter()
    for fixture in fixtures:
        t0 = time.perf_counter()
        result = run_audit(fixture, checkpoint_path=args.db)
        elapsed = (time.perf_counter() - t0) * 1000
        verdict = result["state"].get("verdict", "unknown")
        bucket = verdict_bucket(verdict)
        # 指标沉淀（P95 / 降级 / 命中统计；Web 控制台消费同一份）
        from listing_guard.observability.metrics import record_audit  # noqa: PLC0415

        record_audit(result["audit_id"], result["state"], int(elapsed), result["history_steps"])
        rows.append(
            {
                "case": fixture["case_id"],
                "verdict": verdict,
                "bucket": bucket,
                "expected": fixture["expected"],
                "score": result["state"].get("score", "-"),
                "ok": bucket == fixture["expected"],
                "ms": int(elapsed),
            }
        )
        if args.verbose:
            print(f"{fixture['case_id']:<24}{verdict:<18}{bucket:<9}{fixture['expected']:<9}{'✓' if bucket == fixture['expected'] else '✗'}")
    total_ms = (time.perf_counter() - start) * 1000

    n = len(rows)
    matrix: dict[tuple[str, str], int] = {}
    for r in rows:
        matrix[(r["expected"], r["bucket"])] = matrix.get((r["expected"], r["bucket"]), 0) + 1

    auto = sum(1 for r in rows if r["verdict"] in ("auto_approve", "auto_reject"))
    human = sum(1 for r in rows if r["verdict"] in ("human_approved", "human_rejected"))
    mis_kill = sum(1 for r in rows if r["expected"] == "pass" and r["bucket"] == "reject")
    mis_pass = sum(1 for r in rows if r["expected"] == "reject" and r["bucket"] == "pass")

    print("━" * 64)
    print("批量评测结果（fixture 期望 vs 实际）")
    print("─" * 64)
    print(f"{'case':<24}{'verdict':<18}{'bucket':<9}{'expect':<9}{'score':<7}{'match'}")
    for r in rows:
        print(f"{r['case']:<24}{r['verdict']:<18}{r['bucket']:<9}{r['expected']:<9}{str(r['score']):<7}{'✓' if r['ok'] else '✗'}")
    print("─" * 64)
    print("混淆矩阵（行=期望，列=实际）")
    for exp in ("pass", "review", "reject", "material"):
        cells = [matrix.get((exp, b), 0) for b in ("pass", "review", "reject", "material", "unknown")]
        print(f"  {exp:<10}{cells}")
    print("─" * 64)
    print(f"自动审核覆盖率：{auto}/{n} = {auto / n:.0%}（auto_approve + auto_reject）")
    print(f"转人工率：    {human}/{n} = {human / n:.0%}")
    print(f"误杀优质房源：{mis_kill}（期望 pass 判 reject，核心红线）")
    print(f"漏放问题房源：{mis_pass}（期望 reject 判 pass）")
    print(f"总耗时：{total_ms:.0f}ms（均 {total_ms / n:.0f}ms/条，含挂起-恢复往返）")
    from listing_guard.observability.metrics import p95_elapsed_ms  # noqa: PLC0415

    print(f"端到端 P95：{p95_elapsed_ms():.0f}ms（指标已沉淀 data/metrics.jsonl）")
    print("━" * 64)

    if not all(r["ok"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
