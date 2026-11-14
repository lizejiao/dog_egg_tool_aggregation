"""置信度汇聚节点 —— 加权合成 + 可解释。

关键规则：
- 规则引擎产出确定性因子 + 模型产出概率性因子，统一加权合成；
- degraded 因子从分母剔除后重归一化（降级 ≠ 记 0 分，避免单算子故障误杀）；
- 否决项不进平均，单独输出 veto_hits（否则正分能把黑名单房源"平均"回及格线）。
"""

from __future__ import annotations

from listing_guard.state import AuditState


def aggregate(state: AuditState) -> dict:
    factors = state.get("factors", {})
    all_factors = list(factors.values())

    # degraded：从分母剔除重归一化（降级 ≠ 记 0 分，避免单算子故障误杀）；
    # veto_triggered：不进平均（否决由路由层直接短路，否则正分能把黑名单"平均"回及格线）
    active = [f for f in all_factors if not f.get("degraded") and not f.get("veto_triggered")]
    degraded = [f for f in all_factors if f.get("degraded")]

    denom = sum(f["weight"] for f in active)
    score = (sum(f["weight"] * f["score"] for f in active) / denom) if denom else 0.0

    veto_hits = [f["name"] for f in all_factors if f.get("veto_triggered")]
    hit_reasons = [
        f
        for f in all_factors
        if f.get("hit") or f.get("veto_triggered") or f.get("degraded")
    ]

    events = [
        f"[汇聚] 加权置信度 {round(score, 4)}"
        + (f"（{len(degraded)} 个降级因子已从分母 {round(denom, 2)} 剔除重归一化）" if degraded else "")
        + (f"；否决项命中：{'、'.join(veto_hits)}" if veto_hits else "")
    ]

    return {
        "score": round(score, 4),
        "veto_hits": veto_hits,
        "hit_reasons": hit_reasons,
        "events": events,
    }
