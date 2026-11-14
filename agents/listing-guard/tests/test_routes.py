"""路由纯函数单测：阈值分档 / 注入命中 / 缺材料的路由判断（无 IO，100% 覆盖）。"""

from listing_guard.graph.routes import route_after_decide, route_after_injection, route_after_intake


def test_route_after_intake_missing_material():
    assert route_after_intake({"missing_materials": ["images"]}) == "need_material"


def test_route_after_intake_gray_excluded():
    assert route_after_intake({"finished": True}) == "end"


def test_route_after_intake_continue():
    assert route_after_intake({"missing_materials": []}) == "continue"


def test_route_after_injection_hit_forces_human():
    assert route_after_injection({"injection_hits": ["描述命中『忽略规则』"]}) == "human_review"


def test_route_after_injection_fanout_six_ops():
    ops = route_after_injection({})
    assert set(ops) == {"form_audit", "image_vlm", "video_audit", "text_risk", "base_info", "gov_verify"}


def test_route_after_decide():
    assert route_after_decide({"verdict": "human_review"}) == "human_review"
    assert route_after_decide({"verdict": "auto_approve"}) == "end"
    assert route_after_decide({"verdict": "auto_reject"}) == "end"
