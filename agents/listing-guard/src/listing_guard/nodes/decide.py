"""决策节点：按非对称阈值与否决规则落 verdict（路由判断在 routes.py 纯函数里）。

非对称阈值（误杀成本高于漏放）：
- 放行阈值高（默认 0.85）：优质房源才自动上架；
- 拦截阈值低（默认 0.35）：只有强烈可疑才自动驳回；
- 中间地带一律转人工，而非自动驳回。
"""

from __future__ import annotations

from listing_guard.llm.opinion import build_opinion
from listing_guard.settings import get_settings
from listing_guard.state import AuditState


def decide(state: AuditState) -> dict:
    s = get_settings()
    listing = state.get("listing") or {}
    th = s.thresholds_for(listing.get("city", ""))
    score = state.get("score", 0.0)
    factors = state.get("factors", {})

    def _style(factor: dict) -> str:
        return s.factors[factor["factor_id"]].veto_style

    veto_triggered = [f for f in factors.values() if f.get("veto_triggered")]
    reject_vetoes = [f["name"] for f in veto_triggered if _style(f) == "reject"]
    human_vetoes = [f["name"] for f in veto_triggered if _style(f) == "human"]

    if state.get("injection_hits"):
        verdict, reason = "human_review", "描述/OCR 命中提示词注入特征，强制转人工"
    elif reject_vetoes:
        verdict, reason = "auto_reject", f"命中否决项（直接驳回）：{'、'.join(reject_vetoes)}"
    elif human_vetoes:
        verdict, reason = "human_review", f"命中强因子（转人工）：{'、'.join(human_vetoes)}"
    elif score >= th.pass_:
        verdict, reason = "auto_approve", f"置信度 {score} ≥ 放行阈值 {th.pass_}"
    elif score <= th.reject:
        verdict, reason = "auto_reject", f"置信度 {score} ≤ 拦截阈值 {th.reject}"
    else:
        verdict, reason = "human_review", f"置信度 {score} 落中间地带（{th.reject} ~ {th.pass_}），转人工"

    opinion = build_opinion(state) if verdict == "human_review" else ""

    # 自动上架落「上架房源表」（人工放行在 human_review 节点落）
    if verdict == "auto_approve":
        from listing_guard.tools.published import publish  # noqa: PLC0415

        publish({**state, "verdict": verdict})

    return {
        "verdict": verdict,
        "opinion": opinion,
        "events": [f"[决策路由] {verdict} —— {reason}"],
    }
