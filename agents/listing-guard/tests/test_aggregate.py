"""汇聚节点单测：加权合成 / 降级重归一化 / 否决项隔离。

核心红线：
- 降级因子从分母剔除（不记 0 分，避免单算子故障误杀）；
- 否决项不进平均（否则正分能把黑名单房源"平均"回及格线）。
"""

from listing_guard.nodes.aggregate import aggregate


def _factor(fid: str, weight: float, score: float, **kw) -> dict:
    return {
        "factor_id": fid,
        "name": fid,
        "kind": "deterministic",
        "weight": weight,
        "score": score,
        "is_veto": False,
        "veto_triggered": False,
        "hit": False,
        "degraded": False,
        "elapsed_ms": 1,
        **kw,
    }


def test_weighted_average():
    state = aggregate({"factors": {
        "a": _factor("a", 0.8, 1.0),
        "b": _factor("b", 0.2, 0.0),
    }})
    assert state["score"] == 0.8


def test_degraded_factor_renormalized_not_zeroed():
    """政府核验超时（w=0.2, score=0）降级：从分母剔除，其余 1.0 因子合成仍为 1.0。"""
    state = aggregate({"factors": {
        "a": _factor("a", 0.3, 1.0),
        "b": _factor("b", 0.5, 1.0),
        "gov": _factor("gov", 0.2, 0.0, degraded=True),
    }})
    assert state["score"] == 1.0


def test_veto_not_averaged_but_listed():
    """盗图否决（w=0.3, score=0）不进平均，单独出现在 veto_hits。"""
    state = aggregate({"factors": {
        "image": _factor("image", 0.3, 0.0, veto_triggered=True),
        "b": _factor("b", 0.7, 1.0),
    }})
    assert state["score"] == 1.0  # 否决项不拉低平均分
    assert state["veto_hits"] == ["image"]


def test_hit_reasons_collect_hit_veto_degraded():
    state = aggregate({"factors": {
        "clean": _factor("clean", 0.5, 1.0),
        "hit": _factor("hit", 0.3, 0.4, hit=True),
        "veto": _factor("veto", 0.1, 0.1, veto_triggered=True),
        "degr": _factor("degr", 0.1, 0.0, degraded=True),
    }})
    assert {f["factor_id"] for f in state["hit_reasons"]} == {"hit", "veto", "degr"}


def test_all_degraded_keeps_zero():
    state = aggregate({"factors": {"a": _factor("a", 1.0, 0.5, degraded=True)}})
    assert state["score"] == 0.0
