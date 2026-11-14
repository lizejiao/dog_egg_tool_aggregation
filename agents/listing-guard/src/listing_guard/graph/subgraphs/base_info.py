"""子图 5：房源基本面校验 base_info_subgraph。

对接三个库（生产为楼盘库 MySQL / 历史房源库 ES / 信用库 Redis）：
- 楼盘库：小区是否存在、GPS 与小区坐标一致性；
- 历史房源库：房源级查重（同小区+同户型+面积价格近似/pHash 命中，跨发房人）；
- 信用库：发房人历史违规率、下架率、账号等级。

产出四个确定性因子：community_match / price_reasonable / listing_duplicate / credit。
价格极端偏离（>40%）升级为强因子转人工（veto_style: human）。
"""

from __future__ import annotations

import math
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from listing_guard.state import append_list
from listing_guard.tools import repository
from listing_guard.tools.registry import make_factor

LEVEL_BASE = {"A": 1.0, "B": 0.85, "C": 0.6, "D": 0.3}


class BaseInfoState(TypedDict, total=False):
    listing: dict
    phashes: list                      # 图像子图产出的 pHash（用于房源级查重）
    community_factors: Annotated[list, append_list]  # 子图内部中间结果
    factors: dict


def _check_community(state: BaseInfoState) -> dict:
    listing = state.get("listing") or {}
    community = repository.get_community(listing.get("community", ""))

    results: list[dict] = []
    if community is None:
        results.append(
            make_factor("community_match", 0.1, f"楼盘库未收录小区『{listing.get('community')}』", hit=True)
        )
    else:
        results.append(make_factor("community_match", 1.0, f"楼盘库命中：{community['name']}"))

        # GPS 与小区坐标一致性（沿用原 gps_match 口径，并入 community_match）
        if community.get("coords") and listing.get("gps"):
            dist = math.dist(community["coords"], listing["gps"])
            if dist <= 0.008:
                results.append(make_factor("community_match", 1.0, f"GPS 距小区坐标约 {dist * 111000:.0f} 米，一致"))
            else:
                results.append(
                    make_factor(
                        "community_match",
                        max(0.1, 1 - 6 * dist),
                        f"GPS 偏离小区坐标约 {dist * 111000:.0f} 米",
                        hit=True,
                    )
                )
    return {"community_factors": results}


def _check_price(state: BaseInfoState) -> dict:
    listing = state.get("listing") or {}
    community = repository.get_community(listing.get("community", ""))

    if community and community.get("price_band"):
        lo, hi = community["price_band"]
        price = listing["price"]
        if lo <= price <= hi:
            factor = make_factor("price_reasonable", 1.0, f"价格 {price} 在 {community['name']} 价格带 [{lo}, {hi}] 内")
        elif price < lo:
            dev = (lo - price) / lo
            veto = dev > 0.4
            factor = make_factor(
                "price_reasonable",
                max(0.05, 1 - 2.5 * dev),
                f"价格 {price} 低于价格带下限 {lo}（偏离 {dev:.0%}）"
                + ("，疑似钓鱼价，强因子转人工" if veto else ""),
                hit=dev > 0.15,
                veto_triggered=veto,
            )
        else:
            dev = (price - hi) / hi
            veto = dev > 0.4
            factor = make_factor(
                "price_reasonable",
                max(0.05, 1 - 1.5 * dev),
                f"价格 {price} 高于价格带上限 {hi}（偏离 {dev:.0%}）"
                + ("，疑似虚高钓鱼，强因子转人工" if veto else ""),
                hit=dev > 0.15,
                veto_triggered=veto,
            )
    else:
        factor = make_factor("price_reasonable", 0.6, "小区价格带缺失，降权为中性", hit=True)
    return {"community_factors": [factor]}


def _check_duplicate(state: BaseInfoState) -> dict:
    listing = state.get("listing") or {}
    hits = repository.find_duplicate_listing(listing, phashes=state.get("phashes") or [])

    if hits:
        strong = any(h["phash_hit"] for h in hits)
        codes = "、".join(h["listing_code"] for h in hits)
        detail = (
            f"历史房源库命中 {len(hits)} 条：{codes}"
            + ("（pHash 强证据：盗图重复贴）" if strong else "（同小区同户型近似规格）")
        )
        factor = make_factor(
            "listing_duplicate", 0.3 if strong else 0.5, detail,
            hit=True, veto_triggered=True,
        )
    else:
        factor = make_factor("listing_duplicate", 1.0, "历史房源库无重复（同小区+户型+规格，跨发房人）")
    return {"community_factors": [factor]}


def _check_credit(state: BaseInfoState) -> dict:
    listing = state.get("listing") or {}
    publisher = repository.get_publisher(listing.get("agent_id", ""))

    if publisher is None:
        factor = make_factor("credit", 0.5, "发房人主数据缺失，按中性偏严处理", hit=True)
    else:
        base = LEVEL_BASE.get(publisher.get("level", "C"), 0.6)
        score = max(
            0.05,
            base - 1.5 * publisher.get("violation_rate", 0.3) - 0.5 * publisher.get("takedown_rate", 0.3),
        )
        detail = (
            f"发房人 {publisher['name']}（{publisher['level']} 级），"
            f"历史违规率 {publisher['violation_rate']:.0%}，下架率 {publisher['takedown_rate']:.0%}"
        )
        factor = make_factor("credit", score, detail, hit=score < 0.7)
    return {"community_factors": [factor]}


def _collect(state: BaseInfoState) -> dict:
    """community_match 多条结果取最小分；其余因子一一对应。"""
    items = state.get("community_factors") or []
    by_id: dict[str, list] = {}
    for f in items:
        by_id.setdefault(f["factor_id"], []).append(f)

    factors: dict = {}
    for fid, group in by_id.items():
        if len(group) == 1:
            factors[fid] = group[0]
        else:
            worst = min(group, key=lambda x: x["score"])
            merged_detail = "；".join(g["detail"] for g in group)
            merged = dict(worst)
            merged["detail"] = merged_detail
            merged["hit"] = any(g.get("hit") for g in group)
            factors[fid] = merged
    return {"factors": factors}


def build_base_info_subgraph() -> StateGraph:
    g: StateGraph = StateGraph(BaseInfoState)
    g.add_node("check_community", _check_community)
    g.add_node("check_price", _check_price)
    g.add_node("check_duplicate", _check_duplicate)
    g.add_node("check_credit", _check_credit)
    g.add_node("collect", _collect)
    # 四个库查询内部再并行（子图内二级并行）
    g.add_edge(START, "check_community")
    g.add_edge(START, "check_price")
    g.add_edge(START, "check_duplicate")
    g.add_edge(START, "check_credit")
    for n in ("check_community", "check_price", "check_duplicate", "check_credit"):
        g.add_edge(n, "collect")
    g.add_edge("collect", END)
    return g.compile()
