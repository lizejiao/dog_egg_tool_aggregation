"""主图装配：状态机拓扑、并行扇出、条件边、检查点。

拓扑（见 docs/architecture.md）：
START → 材料接入 →(条件边)→ 注入防护 →(条件边扇出)→
  [图像子图 | 轻核验 | 合规 | 信用 | 政府核验(interrupt)] → 汇聚 → 决策路由
  →(条件边)→ 自动上架/自动驳回(END) | 人工复审(interrupt) → END
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from listing_guard.graph.routes import route_after_decide, route_after_injection, route_after_intake
from listing_guard.nodes.aggregate import aggregate
from listing_guard.nodes.decide import decide
from listing_guard.nodes.gov_verify import gov_verify
from listing_guard.nodes.human_review import human_review
from listing_guard.nodes.injection_guard import injection_guard
from listing_guard.nodes.intake import intake
from listing_guard.nodes.ops import base_info_node, form_audit_node, image_vlm_node, text_risk_node, video_audit_node
from listing_guard.nodes.wait_material import wait_material
from listing_guard.settings import get_settings
from listing_guard.state import AuditState

PARALLEL_OPS = ["form_audit", "image_vlm", "video_audit", "text_risk", "base_info", "gov_verify"]


def _setup_langsmith() -> None:
    """LangSmith 环境变量门控：有 key 才开启，没 key 零开销跳过。"""
    import os

    s = get_settings()
    if not s.env.langsmith_api_key:
        return
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", s.env.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", s.env.langsmith_project)
    os.environ.setdefault("LANGSMITH_ENDPOINT", s.env.langsmith_endpoint)


def build_app(checkpoint_path: str | Path | None = None, checkpointer=None):
    """编译主图。默认挂 SqliteSaver（data/checkpoints.sqlite）。

    LangSmith：配置了 LANGSMITH_API_KEY 时自动开启 tracing（环境变量门控，
    未配 key 时完全跳过、零开销）；按审核单号区分 trace。
    """
    _setup_langsmith()
    g: StateGraph = StateGraph(AuditState)

    g.add_node("intake", intake)
    g.add_node("wait_material", wait_material)
    g.add_node("injection_guard", injection_guard)
    g.add_node("form_audit", form_audit_node)
    g.add_node("image_vlm", image_vlm_node)
    g.add_node("video_audit", video_audit_node)
    g.add_node("text_risk", text_risk_node)
    g.add_node("base_info", base_info_node)
    g.add_node("gov_verify", gov_verify)
    g.add_node("aggregate", aggregate)
    g.add_node("decide", decide)
    g.add_node("human_review", human_review)

    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake",
        route_after_intake,
        {"end": END, "need_material": "wait_material", "continue": "injection_guard"},
    )
    g.add_edge("wait_material", "injection_guard")
    g.add_conditional_edges(
        "injection_guard",
        route_after_injection,
        {
            "human_review": "human_review",
            "form_audit": "form_audit",
            "image_vlm": "image_vlm",
            "video_audit": "video_audit",
            "text_risk": "text_risk",
            "base_info": "base_info",
            "gov_verify": "gov_verify",
        },
    )
    # 并行扇出后汇聚：aggregate 在全部算子（含 resume 后的 gov_verify）完成后执行
    for op in PARALLEL_OPS:
        g.add_edge(op, "aggregate")
    g.add_edge("aggregate", "decide")
    g.add_conditional_edges("decide", route_after_decide, {"human_review": "human_review", "end": END})
    g.add_edge("human_review", END)

    if checkpointer is None:
        import os

        path = (
            Path(checkpoint_path)
            if checkpoint_path
            else Path(os.environ.get("LISTING_GUARD_DB") or get_settings().checkpoint_path)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)
