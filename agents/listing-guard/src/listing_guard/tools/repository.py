"""主数据仓储（demo 用本地 seed；生产换 MySQL/ES + Redis 缓存，接口不变）。

三个库（基本面子图的数据源）：
- 楼盘库 communities：小区是否存在、价格带、坐标
- 历史房源库 listing_history：房源级查重（标题/户型/面积/pHash）
- 发房人信用库 publisher agents：违规率、下架率、信用等级
"""

from __future__ import annotations

import yaml

from listing_guard.settings import DATA_DIR, Settings, get_settings


def _load_history(settings: Settings | None = None) -> list[dict]:
    path = DATA_DIR / "seed" / "listing_history.yaml"
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("listings", [])


def get_community(name: str, settings: Settings | None = None) -> dict | None:
    s = settings or get_settings()
    return s.communities.get(name)


def get_publisher(agent_id: str, settings: Settings | None = None) -> dict | None:
    s = settings or get_settings()
    return s.publisher_agents.get(agent_id)


def find_duplicate_listing(
    listing: dict,
    *,
    phashes: list[str] | None = None,
    settings: Settings | None = None,
) -> list[dict]:
    """房源级查重：pHash 精确命中历史房源 = 强证据（盗图重复贴）；
    同小区+同户型+面积±5%+价格±10%（不同发房人）= 疑似重复贴。

    同一发房人（agent_id 相同）的改价重发不算重复（正常二次发房）。
    """
    history = _load_history(settings)
    community = get_community(listing.get("community", ""), settings)
    cid = (community or {}).get("id")
    hits = []
    for h in history:
        if h["community_id"] != cid:
            continue
        if h.get("agent_id") == listing.get("agent_id"):
            continue  # 本人历史房源：改价重发属正常行为
        phash_hit = bool(set(h.get("phashes", [])) & set(phashes or []))
        area_ok = abs(h["area"] - listing.get("area", 0)) / max(h["area"], 1) <= 0.05
        price_ok = abs(h["price"] - listing.get("price", 0)) / max(h["price"], 1) <= 0.10
        same_spec = h["rooms"] == listing.get("rooms") and area_ok and price_ok
        if phash_hit or same_spec:
            hits.append(
                {
                    "listing_code": h["listing_code"],
                    "phash_hit": phash_hit,
                    "same_spec": same_spec,
                }
            )
    return hits
