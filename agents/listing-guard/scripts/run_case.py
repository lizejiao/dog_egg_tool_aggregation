"""单条审核全流程演示（含挂起 → 自动恢复的完整叙事）。

用法：
  uv run python scripts/run_case.py --case normal_01
  uv run python scripts/run_case.py --case gov_timeout_09
  uv run python scripts/run_case.py --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from listing_guard.report import print_report
from listing_guard.runner import load_fixture, run_audit
from listing_guard.settings import DATA_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="房源发房自动审核 —— 单条全流程")
    parser.add_argument("--case", default="normal_01", help="fixture 名（data/fixtures/<case>.json）")
    parser.add_argument("--db", default=None, help="检查点库路径（默认 data/checkpoints.sqlite）")
    parser.add_argument("--no-auto", action="store_true", help="不自动恢复挂起点（停在第一个 interrupt）")
    parser.add_argument("--list", action="store_true", help="列出全部 fixture")
    args = parser.parse_args()

    if args.list:
        for p in sorted((DATA_DIR / "fixtures").glob("*.json")):
            print(f"{p.stem}")
        return

    fixture = load_fixture(args.case)
    print(f"提交审核：{fixture['case_id']} —— {fixture.get('note', '')}\n")
    result = run_audit(fixture, checkpoint_path=args.db, auto=not args.no_auto, on_step=print)
    print_report(result)


if __name__ == "__main__":
    main()
