"""审核执行器：提交 → 断点续跑（自动 or 跨进程）→ 结果。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from langgraph.types import Command

from listing_guard.graph.builder import build_app
from listing_guard.settings import DATA_DIR, get_settings
from listing_guard.tools import gov_api


def load_fixture(case: str) -> dict:
    path = DATA_DIR / "fixtures" / f"{case}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in (DATA_DIR / "fixtures").glob("*.json")))
        raise FileNotFoundError(f"未找到 fixture：{case}（可用：{available}）")
    return json.loads(path.read_text(encoding="utf-8"))


def audit_id_of(fixture: dict) -> str:
    return f"AUD-{fixture['case_id'].upper()}"


def initial_input(fixture: dict) -> dict:
    return {
        "audit_id": audit_id_of(fixture),
        "listing": fixture["listing"],
        "gov_hint": fixture.get("gov_status", "passed"),
    }


def run_audit(
    fixture: dict,
    checkpoint_path: str | Path | None = None,
    auto: bool = True,
    on_step: Callable[[str], None] | None = None,
) -> dict:
    """跑完一条审核（含自动恢复全部挂起点）。

    auto=False 时在第一个 interrupt 处返回（提交态），供跨进程断点续跑演示。
    """
    log = on_step or (lambda msg: None)
    app = build_app(checkpoint_path)
    cfg = {"configurable": {"thread_id": audit_id_of(fixture)}}

    # 同 thread_id 重复提交时清掉旧检查点与待回调记录（demo 重跑友好；生产是幂等键控流程）
    existing = app.get_state(cfg)
    if existing.next or existing.values:
        app.checkpointer.delete_thread(audit_id_of(fixture))
        gov_api.reset(audit_id_of(fixture))

    app.invoke(initial_input(fixture), cfg)
    state = app.get_state(cfg)

    rounds = 0
    while state.next and auto and rounds < 10:
        rounds += 1
        node = state.next[0]
        if node == "gov_verify":
            callback = gov_api.mock_callback(audit_id_of(fixture))
            resume_value: Command = Command(resume=callback)
            log(f"⏸ 挂起于 [政府核验] → 模拟监管平台回调（status={callback['status']}）→ resume")
        elif node == "wait_material":
            supplement = fixture.get("supplement") or {}
            resume_value = Command(resume=supplement)
            log(f"⏸ 挂起于 [补材料] → 用 fixture 补拍材料 resume")
        elif node == "human_review":
            decision = fixture.get("human_decision") or {"action": "approve"}
            resume_value = Command(resume=decision)
            log(f"⏸ 挂起于 [人工复审] → 模拟人工裁决（{decision.get('action')}）→ resume")
        else:  # pragma: no cover —— 理论不可达
            raise RuntimeError(f"未预期的挂起节点：{node}")
        app.invoke(resume_value, cfg)
        state = app.get_state(cfg)

    history = list(app.get_state_history(cfg))
    return {
        "fixture": fixture,
        "audit_id": audit_id_of(fixture),
        "state": state.values,
        "pending": state.next,          # auto=False 或未恢复完时非空
        "history_steps": len(history),  # 执行轨迹步数（每个超步一个检查点）
        "app": app,
        "config": cfg,
    }


def verdict_bucket(verdict: str) -> str:
    """路由归桶：看的是"自动链路把这条房源送去了哪"，不是人工裁完后的终态。

    human_approved / human_rejected 都属于"转人工复核"bucket —— 人工裁决只是
    复核的结论，不影响"低置信样本被精准转人工"这一路由事实。
    """
    return {
        "auto_approve": "pass",
        "human_approved": "review",
        "human_rejected": "review",
        "auto_reject": "reject",
        "human_review": "review",
        "need_material": "material",
    }.get(verdict, "unknown")
