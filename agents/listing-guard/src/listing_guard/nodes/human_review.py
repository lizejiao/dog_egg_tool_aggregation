"""人工审批节点（human-in-the-loop）。

低置信样本挂起在本节点（interrupt），带 AI 评判意见；
人工裁决后继续流转，裁决结果回流 feedback/ 作为策略迭代样本
（后续可用这批样本回调因子权重与阈值，形成闭环）。
"""

from __future__ import annotations

import json
import time

from langgraph.types import interrupt

from listing_guard.settings import get_settings
from listing_guard.state import AuditState


def human_review(state: AuditState) -> dict:
    hit_factors = [
        {"factor": f["name"], "weight": f["weight"], "score": f["score"], "detail": f["detail"]}
        for f in (state.get("hit_reasons") or [])
    ]
    payload = {
        "audit_id": state.get("audit_id"),
        "score": state.get("score"),
        "veto_hits": state.get("veto_hits", []),
        "opinion": state.get("opinion", ""),
        "hit_factors": hit_factors,
        "resume_hint": '以 {"action": "approve"|"reject", "comment": "..."} 恢复流程',
    }

    # ---- 挂起点：等待人工裁决 ----
    decision = interrupt(payload) or {}

    action = decision.get("action", "approve")
    verdict = "human_approved" if action == "approve" else "human_rejected"

    # 裁决回流：策略迭代样本
    _save_feedback(state, decision, verdict)

    # 人工放行落「上架房源表」（自动上架在 decide 节点落）
    if verdict == "human_approved":
        from listing_guard.tools.published import publish  # noqa: PLC0415

        publish({**state, "verdict": verdict, "human_decision": decision})

    return {
        "verdict": verdict,
        "human_decision": decision,
        "finished": True,
        "events": [f"[人工复审] 裁决 {action}（{decision.get('comment', '')}）→ {verdict}"],
    }


def _save_feedback(state: AuditState, decision: dict, verdict: str) -> None:
    s = get_settings()
    s.feedback_dir.mkdir(parents=True, exist_ok=True)
    sample = {
        "audit_id": state.get("audit_id"),
        "verdict": verdict,
        "score": state.get("score"),
        "policy_version": state.get("policy_version"),
        "factors": {k: {"score": v["score"], "hit": v.get("hit"), "degraded": v.get("degraded")} for k, v in state.get("factors", {}).items()},
        "decision": decision,
        "sampled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = s.feedback_dir / f"{state.get('audit_id')}.json"
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
