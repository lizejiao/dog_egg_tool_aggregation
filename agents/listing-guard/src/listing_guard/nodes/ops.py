"""五个核验子图的主图入口节点。

每个入口：因子缓存检查（断点续跑不重算）→ invoke 对应编译子图 →
只把结构化因子结论与一行事件写回主图 state。
图像材料里的 pHash 会传给基本面子图做房源级查重（子图间的受控数据流）。
"""

from __future__ import annotations

from listing_guard.state import AuditState


def _subgraph_entry(state: AuditState, factor_keys: list[str], builder, payload: dict, label: str) -> dict:
    factors = state.get("factors", {})
    if all(k in factors for k in factor_keys):
        return {"events": [f"[{label}] 因子已存在，跳过（断点续跑不重算）"]}

    sub = builder()
    out = sub.invoke(payload)
    sub_factors = out.get("factors") or {}
    summary = "、".join(f"{k}={v['score']}" for k, v in sorted(sub_factors.items()))
    return {"factors": sub_factors, "events": [f"[{label}] 子图完成：{summary}"]}


# ---------------------------------------------------------------- 子图 1：表单规则
def form_audit_node(state: AuditState) -> dict:
    from listing_guard.graph.subgraphs.form_audit import build_form_audit_subgraph  # noqa: PLC0415

    return _subgraph_entry(
        state, ["form_rules"], build_form_audit_subgraph,
        {"listing": state["listing"]}, "表单规则子图",
    )


# ---------------------------------------------------------------- 子图 2：图片 VLM
def image_vlm_node(state: AuditState) -> dict:
    from listing_guard.graph.subgraphs.image_audit import build_image_subgraph  # noqa: PLC0415

    return _subgraph_entry(
        state, ["image_authenticity", "image_text_consistency"], build_image_subgraph,
        {"images": state["listing"].get("images", []), "rooms": state["listing"].get("rooms", 0)},
        "图片VLM子图",
    )


# ---------------------------------------------------------------- 子图 3：视频 ASR + 帧识别
def video_audit_node(state: AuditState) -> dict:
    from listing_guard.graph.subgraphs.video_audit import build_video_subgraph  # noqa: PLC0415

    return _subgraph_entry(
        state, ["video_authenticity"], build_video_subgraph,
        {"videos": state["listing"].get("videos", []), "rooms": state["listing"].get("rooms", 0)},
        "视频子图",
    )


# ---------------------------------------------------------------- 子图 4：文本风控
def text_risk_node(state: AuditState) -> dict:
    from listing_guard.graph.subgraphs.text_risk import build_text_risk_subgraph  # noqa: PLC0415

    return _subgraph_entry(
        state, ["text_risk"], build_text_risk_subgraph,
        {"listing": state["listing"]}, "文本风控子图",
    )


# ---------------------------------------------------------------- 子图 5：房源基本面
def base_info_node(state: AuditState) -> dict:
    from listing_guard.graph.subgraphs.base_info import build_base_info_subgraph  # noqa: PLC0415

    # 房源级查重的强证据：图片 pHash（demo 材料即含；live 由图像子图产出通道传递）
    phashes = [
        img.get("mock", {}).get("phash")
        for img in state["listing"].get("images", [])
        if img.get("mock", {}).get("phash")
    ]
    return _subgraph_entry(
        state,
        ["community_match", "price_reasonable", "listing_duplicate", "credit"],
        build_base_info_subgraph,
        {"listing": state["listing"], "phashes": phashes},
        "基本面子图",
    )
