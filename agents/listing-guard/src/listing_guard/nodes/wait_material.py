"""补材料挂起节点：房东补拍材料后从断点续跑。

已完成算子不重算（各算子节点有因子缓存检查），
只有缺失部分（如新补拍的图片）会进入后续验真。
"""

from __future__ import annotations

from langgraph.types import interrupt

from listing_guard.state import AuditState


def wait_material(state: AuditState) -> dict:
    missing = state.get("missing_materials", [])
    payload = {
        "missing": missing,
        "resume_hint": "以补齐字段字典恢复流程，如 {'images': [...]}",
    }

    # ---- 挂起点：等待房东补拍/补传 ----
    supplement = interrupt(payload) or {}

    listing = dict(state.get("listing") or {})
    for key, value in supplement.items():
        listing[key] = value

    return {
        "listing": listing,
        "missing_materials": [],
        "events": [f"[补材料] 房东补传 {len(supplement)} 类材料（{', '.join(supplement.keys())}），从断点续跑"],
    }
