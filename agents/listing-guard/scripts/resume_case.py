"""跨进程断点续跑演示：验证"进度在 SQLite 检查点里，与进程无关"。

用法（分两次执行，模拟真实异步时序）：
  uv run python scripts/resume_case.py --case normal_01            # 第 1 步：提交，挂起后退出
  uv run python scripts/resume_case.py --case normal_01 --resume   # 第 2 步：新进程从检查点续跑
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from listing_guard.graph.builder import build_app
from listing_guard.report import print_report
from listing_guard.runner import audit_id_of, initial_input, load_fixture, run_audit
from listing_guard.tools import gov_api


def main() -> None:
    parser = argparse.ArgumentParser(description="跨进程断点续跑演示")
    parser.add_argument("--case", default="normal_01")
    parser.add_argument("--db", default=None)
    parser.add_argument("--resume", action="store_true", help="第二阶段：从检查点恢复")
    args = parser.parse_args()

    fixture = load_fixture(args.case)
    audit_id = audit_id_of(fixture)
    cfg = {"configurable": {"thread_id": audit_id}}

    if not args.resume:
        # ---- 第 1 步：提交（新进程、新图实例）----
        print(f"[进程 1] 提交 {audit_id}，跑到政府核验挂起后退出进程……\n")
        result = run_audit(fixture, checkpoint_path=args.db, auto=False, on_step=print)
        st = result["state"]
        print(f"[进程 1] 挂起于：{result['pending']}")
        print(f"[进程 1] 已完成因子：{', '.join(sorted(st.get('factors', {}))) or '（无）'}")
        print(f"[进程 1] 检查点已落盘（{result['history_steps']} 步）。进程退出，回调稍后到达。\n")
        print(f"[进程 2] 请执行：uv run python scripts/resume_case.py --case {args.case} --resume")
        return

    # ---- 第 2 步：新进程从检查点恢复 ----
    print(f"[进程 2] 新进程打开检查点库，恢复 {audit_id} ……\n")
    app = build_app(args.db)
    state = app.get_state(cfg)
    print(f"[进程 2] 恢复时状态：待执行节点 {state.next}，已完成因子 {len(state.values.get('factors', {}))} 个")

    rounds = 0
    while state.next and rounds < 10:
        rounds += 1
        node = state.next[0]
        if node == "gov_verify":
            callback = gov_api.mock_callback(audit_id)
            print(f"[进程 2] 政府核验回调到达：{callback['status']} → resume（注意：已完成算子不重跑）")
            app.invoke(Command(resume=callback), cfg)
        elif node == "wait_material":
            supp = fixture.get("supplement") or {}
            print(f"[进程 2] 房东补传材料 → resume")
            app.invoke(Command(resume=supp), cfg)
        elif node == "human_review":
            decision = fixture.get("human_decision") or {"action": "approve"}
            print(f"[进程 2] 人工裁决 {decision.get('action')} → resume")
            app.invoke(Command(resume=decision), cfg)
        else:  # pragma: no cover
            raise RuntimeError(f"未预期的挂起节点：{node}")
        state = app.get_state(cfg)

    print_report(
        {
            "fixture": fixture,
            "audit_id": audit_id,
            "state": state.values,
            "pending": state.next,
            "history_steps": len(list(app.get_state_history(cfg))),
        }
    )


if __name__ == "__main__":
    main()
