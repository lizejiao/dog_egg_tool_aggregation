"""路由纯函数：无 IO、可被单测 100% 覆盖。

阈值分档、注入命中、缺材料、否决项路由全部在这里判断；
运营调 thresholds.yaml 即可改变分档，不需要跑整图回归。
"""

from __future__ import annotations

from listing_guard.state import AuditState


def route_after_intake(state: AuditState) -> str:
    """灰度未命中（intake 已置终态）→ 结束；材料不齐 → 挂起等补拍；否则进注入防护。"""
    if state.get("finished"):
        return "end"
    if state.get("missing_materials"):
        return "need_material"
    return "continue"


def route_after_injection(state: AuditState) -> list[str] | str:
    """命中注入特征 → 直接强制转人工（不允许自由文本影响路由）；
    否则并行扇出五个核验子图 + 政府核验算子（同一超步并发）。"""
    if state.get("injection_hits"):
        return "human_review"
    return [
        "form_audit",
        "image_vlm",
        "video_audit",
        "text_risk",
        "base_info",
        "gov_verify",
    ]


def route_after_decide(state: AuditState) -> str:
    """决策路由：人工复审 or 终态。"""
    if state.get("verdict") == "human_review":
        return "human_review"
    return "end"
