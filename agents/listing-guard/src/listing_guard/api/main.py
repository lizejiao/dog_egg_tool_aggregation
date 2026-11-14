"""FastAPI 服务：提交审核 / 查状态 / 政府回调 / 人工裁决 / 补材料 / 指标。

异步链路的 HTTP 化：
- POST /audit            提交（跑到第一个挂起点或终态）
- GET  /audit/{id}       查状态（因子、评分、挂起位置、AI 意见）
- POST /audit/{id}/gov-callback   政府核验回调 → resume
- POST /audit/{id}/decision       人工裁决 → resume
- POST /audit/{id}/supplement    房东补材料 → resume
- GET  /stats            指标卡数据
- GET  /                 Web 审核控制台
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from langgraph.types import Command
from pydantic import BaseModel

from listing_guard.graph.builder import build_app
from listing_guard.observability.metrics import dashboard_stats, record_audit
from listing_guard.runner import audit_id_of, initial_input, load_fixture
from listing_guard.settings import PROJECT_ROOT, get_settings
from listing_guard.tools import gov_api

app = FastAPI(title="listing-guard", version="0.1.0")

# 单例图应用（进程内复用，SqliteSaver 连接不重复建）
_app = None


def graph_app():
    global _app
    if _app is None:
        _app = build_app()
    return _app


class AuditRequest(BaseModel):
    case: str | None = None        # 用 fixture 提交（演示）
    listing: dict | None = None    # 或直接给房源材料
    gov_hint: str = "passed"


class GovCallback(BaseModel):
    status: str | None = None      # 覆盖回调结果（演示 timeout/failed）


class HumanDecision(BaseModel):
    action: str  # approve | reject
    comment: str = ""


class Supplement(BaseModel):
    fields: dict  # 如 {"images": [...]}


def _cfg(audit_id: str) -> dict:
    return {"configurable": {"thread_id": audit_id}}


def _snapshot(audit_id: str) -> dict:
    snap = graph_app().get_state(_cfg(audit_id))
    if not snap.values and not snap.next:
        raise HTTPException(404, f"审核单不存在：{audit_id}")
    values = snap.values
    factors = values.get("factors", {})
    return {
        "audit_id": audit_id,
        "pending": list(snap.next),
        "verdict": values.get("verdict"),
        "score": values.get("score"),
        "policy_version": values.get("policy_version"),
        "listing_title": (values.get("listing") or {}).get("title"),
        "veto_hits": values.get("veto_hits", []),
        "injection_hits": values.get("injection_hits", []),
        "missing_materials": values.get("missing_materials", []),
        "opinion": values.get("opinion", ""),
        "hit_reasons": [
            {"factor": f["name"], "weight": f["weight"], "score": f["score"], "detail": f["detail"]}
            for f in (values.get("hit_reasons") or [])
        ],
        "factors": [
            {
                "id": k,
                "name": v["name"],
                "kind": v["kind"],
                "weight": v["weight"],
                "score": v["score"],
                "status": "veto" if v.get("veto_triggered") else ("degraded" if v.get("degraded") else ("hit" if v.get("hit") else "pass")),
                "detail": v["detail"],
            }
            for k, v in sorted(factors.items(), key=lambda kv: -kv[1]["weight"])
        ],
        "events": values.get("events", []),
        "human_decision": values.get("human_decision"),
    }


@app.post("/audit")
def submit_audit(req: AuditRequest):
    """提交审核：跑到第一个挂起点（政府核验/人工/补材料）或终态。"""
    if req.case:
        try:
            fixture = load_fixture(req.case)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        listing, gov_hint = fixture["listing"], fixture.get("gov_status", "passed")
        audit_id = audit_id_of(fixture)
    elif req.listing:
        listing, gov_hint = req.listing, req.gov_hint
        audit_id = f"AUD-{int(time.time() * 1000)}"
    else:
        raise HTTPException(400, "case 或 listing 必填其一")

    cfg = _cfg(audit_id)
    g = graph_app()
    existing = g.get_state(cfg)
    if existing.next or existing.values:  # 重跑清理（demo 语义）
        g.checkpointer.delete_thread(audit_id)
        gov_api.reset(audit_id)

    t0 = time.perf_counter()
    g.invoke({"audit_id": audit_id, "listing": listing, "gov_hint": gov_hint}, cfg)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    snap = g.get_state(cfg)
    record_audit(audit_id, snap.values, elapsed_ms, len(list(g.get_state_history(cfg))))
    return _snapshot(audit_id)


@app.get("/audit/{audit_id}")
def get_audit(audit_id: str):
    return _snapshot(audit_id)


@app.get("/audit/{audit_id}/material")
def audit_material(audit_id: str):
    """房源基本信息查询：提交审核时的完整房源材料（表单字段 + 图片/视频清单）。"""
    snap = graph_app().get_state(_cfg(audit_id))
    if not snap.values and not snap.next:
        raise HTTPException(404, f"审核单不存在：{audit_id}")
    values = snap.values
    listing = values.get("listing") or {}

    form_fields = [
        "title", "city", "community", "community_id", "price", "rooms", "area",
        "floor", "total_floors", "orientation", "gps", "agent_id", "description",
    ]
    form = {
        label: listing.get(key)
        for key, label in [
            ("title", "房源标题"), ("city", "城市"), ("community", "小区"),
            ("price", "月租（元）"), ("rooms", "户型（室）"), ("area", "面积（㎡）"),
            ("floor", "所在楼层"), ("total_floors", "总楼层"), ("orientation", "朝向"),
            ("gps", "GPS 坐标"), ("agent_id", "发房人 ID"), ("description", "房源描述"),
        ]
    }
    images = [
        {"path": img.get("path"), "note": "mock 材料无预览"}
        for img in listing.get("images", [])
    ]
    videos = [
        {"path": v.get("path"), "note": "mock 材料无预览"}
        for v in listing.get("videos", [])
    ]
    return {
        "audit_id": audit_id,
        "verdict": values.get("verdict"),
        "score": values.get("score"),
        "form": form,
        "images": images,
        "videos": videos,
        "missing_materials": values.get("missing_materials", []),
        "human_decision": values.get("human_decision"),
        "policy_version": values.get("policy_version"),
    }


def _record_now(audit_id: str, t0: float) -> dict:
    """resume 后落一次指标：终态 verdict/因子/耗时（终态行会覆盖提交行的语义，
    stats 聚合按 audit_id 去重取最新）。"""
    g = graph_app()
    snap = g.get_state(_cfg(audit_id))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    record_audit(audit_id, snap.values, elapsed_ms, len(list(g.get_state_history(_cfg(audit_id)))))
    return snap.values


@app.post("/audit/{audit_id}/gov-callback")
def gov_callback(audit_id: str, body: GovCallback | None = None):
    """政府核验异步回调 → resume；可覆盖 status 演示 failed/timeout 分支。"""
    _snapshot(audit_id)  # 404 校验
    snap = graph_app().get_state(_cfg(audit_id))
    if snap.next != ("gov_verify",):
        raise HTTPException(409, f"当前不在政府核验挂起态（pending={list(snap.next)}）")

    callback = gov_api.mock_callback(audit_id)
    if body and body.status:
        callback["status"] = body.status
    t0 = time.perf_counter()
    graph_app().invoke(Command(resume=callback), _cfg(audit_id))
    _record_now(audit_id, t0)
    return _snapshot(audit_id)


@app.post("/audit/{audit_id}/decision")
def human_decision(audit_id: str, body: HumanDecision):
    """人工裁决 → resume，结果回流 data/feedback/。"""
    _snapshot(audit_id)
    t0 = time.perf_counter()
    graph_app().invoke(
        Command(resume={"action": body.action, "comment": body.comment}), _cfg(audit_id)
    )
    _record_now(audit_id, t0)
    return _snapshot(audit_id)


@app.post("/audit/{audit_id}/supplement")
def supplement_material(audit_id: str, body: Supplement):
    """房东补材料 → resume，从断点续跑（已完成算子不重算）。"""
    _snapshot(audit_id)
    t0 = time.perf_counter()
    graph_app().invoke(Command(resume=body.fields), _cfg(audit_id))
    _record_now(audit_id, t0)
    return _snapshot(audit_id)


@app.get("/stats")
def stats():
    return dashboard_stats()


@app.get("/listings")
def published_listings(source: str = "all"):
    """上架房源列表（auto=自动核验上架 / human=人工放行上架 / all）。

    首次查询自动从检查点库回填历史已上架审核单（幂等）。
    """
    from listing_guard.tools.published import list_published, sync_from_checkpoints  # noqa: PLC0415

    sync_from_checkpoints(graph_app())
    return list_published(source)


STATUS_TEXT = {
    "waiting_gov": "等待政府核验回调",
    "waiting_material": "等待房东补材料",
    "waiting_human": "等待人工复审",
    "rejected_auto": "自动驳回",
    "rejected_human": "人工驳回",
    "other": "其他终态",
}
PENDING_NODE_MAP = {"gov_verify": "waiting_gov", "wait_material": "waiting_material", "human_review": "waiting_human"}


@app.get("/pending")
def pending_audits(status: str = "all"):
    """审核队列：所有未上架审核单及其当前状态。

    status 筛选：pending=挂起中（等回调/等材料/等人工） / rejected=已驳回 / all=全部。
    数据源为检查点库（每单一个 thread），排除已上架（published.jsonl）。
    """
    import os
    import sqlite3

    from listing_guard.tools.published import list_published  # noqa: PLC0415

    published_ids = {r["audit_id"] for r in list_published()}
    db_path = Path(os.environ.get("LISTING_GUARD_DB") or get_settings().checkpoint_path)
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    try:
        thread_ids = {r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()}
    finally:
        conn.close()

    g = graph_app()
    rows = []
    for tid in thread_ids:
        if tid in published_ids:
            continue
        snap = g.get_state({"configurable": {"thread_id": tid}})
        values = snap.values
        if not values and not snap.next:
            continue
        listing = values.get("listing") or {}
        pending_nodes = list(snap.next)

        if pending_nodes:
            st = PENDING_NODE_MAP.get(pending_nodes[0], "running")
        else:
            verdict = values.get("verdict")
            st = {
                "auto_reject": "rejected_auto",
                "human_rejected": "rejected_human",
            }.get(verdict, "other")

        if st == "running":  # 理论不可达（同步 invoke），防御性跳过
            continue
        if status == "pending" and not pending_nodes:
            continue
        if status == "rejected" and not st.startswith("rejected"):
            continue

        rows.append(
            {
                "audit_id": tid,
                "status": st,
                "status_text": STATUS_TEXT.get(st, st),
                "pending_nodes": pending_nodes,
                "score": values.get("score"),
                "veto_hits": values.get("veto_hits", []),
                "has_opinion": bool(values.get("opinion")),
                "listing_title": listing.get("title"),
                "community": listing.get("community"),
                "city": listing.get("city"),
                "price": listing.get("price"),
                "rooms": listing.get("rooms"),
                "area": listing.get("area"),
            }
        )

    order = {"waiting_gov": 0, "waiting_material": 1, "waiting_human": 2, "rejected_auto": 3, "rejected_human": 4, "other": 5}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["audit_id"]), reverse=False)
    return rows


@app.get("/fixtures")
def fixtures_list():
    return [
        {"case": p.stem, "note": (_load_fixture_note(p))}
        for p in sorted((PROJECT_ROOT / "data" / "fixtures").glob("*.json"))
    ]


def _load_fixture_note(path: Path) -> str:
    import json

    return json.loads(path.read_text(encoding="utf-8")).get("note", "")


@app.get("/")
def index():
    web = PROJECT_ROOT / "web" / "index.html"
    if web.exists():
        return FileResponse(web)
    return JSONResponse({"msg": "web/index.html 未找到，请先创建"})


@app.get("/docs/flow")
def business_flow_doc():
    """业务流程详解文档页。"""
    doc = PROJECT_ROOT / "docs" / "business-flow.html"
    if doc.exists():
        return FileResponse(doc)
    raise HTTPException(404, "docs/business-flow.html 未找到")
