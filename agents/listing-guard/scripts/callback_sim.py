"""模拟政府监管平台异步回调（"两个终端"演示用）。

终端 1：uv run python scripts/run_case.py --case normal_01 --no-auto
终端 2：uv run python scripts/callback_sim.py --audit-id AUD-NORMAL_01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from listing_guard.graph.builder import build_app
from listing_guard.tools import gov_api


def main() -> None:
    parser = argparse.ArgumentParser(description="模拟政府核验异步回调")
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--status", default=None, help="覆盖回调结果：passed / failed / timeout")
    parser.add_argument("--decision", default=None, help="回调后若路由到人工复审，顺带模拟裁决：approve / reject")
    args = parser.parse_args()

    cfg = {"configurable": {"thread_id": args.audit_id}}
    app = build_app(args.db)
    state = app.get_state(cfg)
    print(f"回调前：待执行节点 {state.next}，已完成因子 {len(state.values.get('factors', {}))} 个")

    callback = gov_api.mock_callback(args.audit_id)
    if args.status:
        callback["status"] = args.status
    print(f"回调数据：{callback}")

    app.invoke(Command(resume=callback), cfg)
    state = app.get_state(cfg)
    print(f"回调后：待执行节点 {state.next}")

    if state.next and state.next[0] == "human_review" and args.decision:
        print(f"路由到人工复审 → 模拟裁决 {args.decision}")
        app.invoke(Command(resume={"action": args.decision, "comment": "callback_sim 模拟裁决"}), cfg)
        state = app.get_state(cfg)
        print(f"裁决后：待执行节点 {state.next}")

    print(f"当前因子：{', '.join(f'{k}={v['score']}' for k, v in state.values.get('factors', {}).items())}")
    print(f"verdict：{state.values.get('verdict', '（未终态，继续等待人工裁决等后续恢复）')}")


if __name__ == "__main__":
    main()
