"""材料接入：归一化、必填校验、灰度城市检查。"""

from __future__ import annotations

from listing_guard.settings import get_settings
from listing_guard.state import AuditState

REQUIRED_FIELDS = ["title", "city", "community", "price", "rooms", "area", "gps", "agent_id", "description"]


def intake(state: AuditState) -> dict:
    s = get_settings()
    listing = state.get("listing") or {}

    # 归一化（真实链路里这步还做单位换算、字典枚举映射等）
    listing["price"] = int(listing.get("price") or 0)
    listing["rooms"] = int(listing.get("rooms") or 0)
    listing["area"] = int(listing.get("area") or 0)

    missing = [f for f in REQUIRED_FIELDS if not listing.get(f)]
    if not listing.get("images"):
        missing.append("images（实拍图，至少 1 张）")

    events = [f"[材料接入] 字段校验完成，缺失 {len(missing)} 项，策略版本 {s.policy_version}"]

    # 灰度放量：未命中灰度城市不进自动链路，直接走人工
    if listing.get("city") and listing["city"] not in s.gray.cities:
        return {
            "listing": listing,
            "policy_version": s.policy_version,
            "missing_materials": [],
            "verdict": "human_review",
            "opinion": f"城市 {listing['city']} 未命中自动审核灰度（当前灰度：{'、'.join(s.gray.cities)}），直接转人工。",
            "finished": True,
            "events": events + [f"[灰度] {listing['city']} 未在灰度名单，转人工"],
        }

    return {
        "listing": listing,
        "policy_version": s.policy_version,
        "missing_materials": missing,
        "events": events,
    }
