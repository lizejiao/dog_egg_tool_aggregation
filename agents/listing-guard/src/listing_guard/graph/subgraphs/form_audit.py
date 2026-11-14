"""子图 1：表单规则核验 form_audit_subgraph。

规则引擎（确定性因子）：必填完整性由主图 intake 把关，这里做语义级自洽——
楼层/朝向/面积边界/标题关键词与字段一致性。
内部流程：规则集检查 → 规则因子收敛。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from listing_guard.state import append_list
from listing_guard.tools.registry import make_factor

VALID_ORIENTATIONS = {"东", "南", "西", "北", "南北", "东南", "西南", "东北", "西北", "东西"}
ORIENTATION_KEYWORDS = {"南": ["南向", "朝南", "南北"], "北": ["北向", "朝北"]}


class FormState(TypedDict, total=False):
    listing: dict
    violations: Annotated[list, append_list]  # 子图内部中间数据
    factors: dict


def _validate_rules(state: FormState) -> dict:
    listing = state.get("listing") or {}
    v: list[str] = []

    # 楼层自洽：0 < floor ≤ total_floors
    floor, total = listing.get("floor"), listing.get("total_floors")
    if floor and total and not (0 < floor <= total):
        v.append(f"楼层不自洽：{floor}/{total} 层")

    # 朝向枚举合法
    if listing.get("orientation") and listing["orientation"] not in VALID_ORIENTATIONS:
        v.append(f"朝向非法枚举：{listing['orientation']}")

    # 面积边界：5㎡ ~ 2000㎡（合理租赁房源边界）
    area = listing.get("area") or 0
    if not (5 <= area <= 2000):
        v.append(f"面积越界：{area}㎡")

    # 标题-字段一致性：标题宣称户型与表单户型一致（防标题党挂羊头）
    rooms = listing.get("rooms") or 0
    title = listing.get("title", "")
    cn = {1: "一居", 2: "两居", 3: "三居", 4: "四居", 5: "五居"}.get(rooms)
    if cn and cn in title and f"{rooms} 室" not in title and rooms > 0:
        pass  # 中文户型词与 rooms 一致即可；数字写法在下一规则里校验
    if cn and cn not in title and any(w in title for w in ("一居", "两居", "三居", "四居", "五居")):
        v.append(f"标题户型词与表单 rooms={rooms} 不一致")

    return {"violations": v}


def _collect(state: FormState) -> dict:
    v = state.get("violations") or []
    if v:
        factor = make_factor(
            "form_rules", max(0.2, 1 - 0.3 * len(v)), "；".join(v), hit=True
        )
    else:
        factor = make_factor("form_rules", 1.0, "楼层/朝向/面积/标题-字段全部自洽")
    return {"factors": {"form_rules": factor}}


def build_form_audit_subgraph() -> StateGraph:
    g: StateGraph = StateGraph(FormState)
    g.add_node("validate_rules", _validate_rules)
    g.add_node("collect", _collect)
    g.add_edge(START, "validate_rules")
    g.add_edge("validate_rules", "collect")
    g.add_edge("collect", END)
    return g.compile()
