"""API 集成测试：异步链路 HTTP 化的完整闭环。

用 TestClient（不起真实端口）覆盖：
提交 → 挂起 → 政府回调 → 人工裁决 / 补材料 → 指标。
"""

from __future__ import annotations

import importlib

import pytest

fastapi_test = pytest.importorskip("fastapi.testclient")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立检查点库 + 上架表（环境变量），避免与本机运行中的服务互相污染。"""
    monkeypatch.setenv("LISTING_GUARD_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("LISTING_GUARD_PUBLISHED", str(tmp_path / "published.jsonl"))

    import listing_guard.api.main as api_mod

    api_mod._app = None  # 重置单例
    from fastapi.testclient import TestClient

    with TestClient(api_mod.app) as c:
        yield c
    api_mod._app = None


def test_submit_pauses_and_full_flow(client):
    # ① 提交：跑到政府核验挂起
    r = client.post("/audit", json={"case": "normal_01"})
    assert r.status_code == 200
    body = r.json()
    assert body["pending"] == ["gov_verify"]
    assert len(body["factors"]) == 9  # 五子图因子齐了，只差政府核验

    # ② 政府回调 → resume → 终态
    r = client.post(f"/audit/{body['audit_id']}/gov-callback", json={})
    assert r.status_code == 200
    assert r.json()["verdict"] == "auto_approve"


def test_human_review_flow(client):
    r = client.post("/audit", json={"case": "low_confidence_08"})
    audit_id = r.json()["audit_id"]

    r = client.post(f"/audit/{audit_id}/gov-callback", json={})
    body = r.json()
    assert "human_review" in body["pending"]
    assert body["opinion"]  # AI 评判意见必须带上

    r = client.post(f"/audit/{audit_id}/decision", json={"action": "approve", "comment": "实地核验通过"})
    assert r.json()["verdict"] == "human_approved"


def test_gov_callback_conflict(client):
    r = client.post("/audit", json={"case": "normal_01"})
    audit_id = r.json()["audit_id"]
    client.post(f"/audit/{audit_id}/gov-callback", json={})
    # 已不在挂起态，再回调应 409
    r = client.post(f"/audit/{audit_id}/gov-callback", json={})
    assert r.status_code == 409


def test_supplement_flow(client):
    r = client.post("/audit", json={"case": "missing_material_07"})
    body = r.json()
    assert "wait_material" in body["pending"]

    r = client.post(
        f"/audit/{body['audit_id']}/supplement",
        json={"fields": {"images": [
            {"path": "images/sup/1.jpg", "mock": {"authenticity": 0.96, "phash": "ab12cd34ef567890",
             "is_duplicate": False, "detected_rooms": 2, "ocr_text": ""}}
        ]}},
    )
    assert "wait_material" not in r.json()["pending"]


def test_stats_and_fixtures(client):
    client.post("/audit", json={"case": "normal_01"})
    stats = client.get("/stats").json()
    assert stats["total"] >= 1

    fixtures = client.get("/fixtures").json()
    assert any(f["case"] == "normal_01" for f in fixtures)


def test_404_unknown_audit(client):
    assert client.get("/audit/AUD-NOPE").status_code == 404


def test_published_listings_auto_and_human(client):
    """上架房源表：自动上架 + 人工放行都入表，/listings 可查且来源筛选正确。"""
    # 自动上架链路
    r = client.post("/audit", json={"case": "normal_01"})
    aid_auto = r.json()["audit_id"]
    client.post(f"/audit/{aid_auto}/gov-callback", json={})

    # 人工放行链路
    r = client.post("/audit", json={"case": "low_confidence_08"})
    aid_human = r.json()["audit_id"]
    client.post(f"/audit/{aid_human}/gov-callback", json={})
    client.post(f"/audit/{aid_human}/decision", json={"action": "approve", "comment": "测试"})

    all_rows = client.get("/listings").json()
    ids = {r["audit_id"] for r in all_rows}
    assert aid_auto in ids and aid_human in ids

    auto_rows = client.get("/listings?source=auto").json()
    human_rows = client.get("/listings?source=human").json()
    assert any(r["audit_id"] == aid_auto and r["source"] == "auto" for r in auto_rows)
    assert any(r["audit_id"] == aid_human and r["source"] == "human" for r in human_rows)
    assert all(r["source"] == "auto" for r in auto_rows)

    # 上架行带房源快照
    row = next(r for r in all_rows if r["audit_id"] == aid_auto)
    assert row["listing"]["title"] and row["listing"]["price"]


def test_rejected_not_published(client):
    """被驳回的房源不进上架表。"""
    r = client.post("/audit", json={"case": "stolen_image_03"})
    aid = r.json()["audit_id"]
    client.post(f"/audit/{aid}/gov-callback", json={})
    ids = {r["audit_id"] for r in client.get("/listings").json()}
    assert aid not in ids


def test_pending_queue_status_and_filter(client):
    """审核队列：挂起/驳回状态正确、上架的不出现、status 筛选正确。"""
    # 挂起在政府核验（不回调）
    d1 = client.post("/audit", json={"case": "normal_01"}).json()
    # 挂起在补材料
    d2 = client.post("/audit", json={"case": "missing_material_07"}).json()
    # 走到自动驳回终态
    d3 = client.post("/audit", json={"case": "stolen_image_03"}).json()
    client.post(f"/audit/{d3['audit_id']}/gov-callback", json={})
    # 走到人工复审挂起（AI 意见在）
    d4 = client.post("/audit", json={"case": "low_confidence_08"}).json()
    client.post(f"/audit/{d4['audit_id']}/gov-callback", json={})
    # 一条完成自动上架（不应出现在队列）
    d5 = client.post("/audit", json={"case": "gov_timeout_09"}).json()
    client.post(f"/audit/{d5['audit_id']}/gov-callback", json={})

    by_id = {r["audit_id"]: r for r in client.get("/pending").json()}
    assert by_id[d1["audit_id"]]["status"] == "waiting_gov"
    assert by_id[d2["audit_id"]]["status"] == "waiting_material"
    assert by_id[d3["audit_id"]]["status"] == "rejected_auto"
    assert by_id[d4["audit_id"]]["status"] == "waiting_human"
    assert by_id[d4["audit_id"]]["has_opinion"] is True
    assert d5["audit_id"] not in by_id  # 已上架不进队列

    pend = {r["audit_id"] for r in client.get("/pending?status=pending").json()}
    assert d1["audit_id"] in pend and d3["audit_id"] not in pend
    rej = {r["audit_id"] for r in client.get("/pending?status=rejected").json()}
    assert d3["audit_id"] in rej and d1["audit_id"] not in rej


def test_material_endpoint(client):
    """房源基本信息查询：提交材料的全量快照（表单 + 图片/视频清单）。"""
    d = client.post("/audit", json={"case": "normal_01"}).json()
    m = client.get(f"/audit/{d['audit_id']}/material").json()

    assert m["form"]["房源标题"] == "望京西园 南向两居 拎包入住 近地铁"
    assert m["form"]["月租（元）"] == 6800
    assert m["form"]["户型（室）"] == 2
    assert len(m["images"]) == 3
    assert len(m["videos"]) == 1  # normal_01 带实拍视频
    assert m["form"]["房源描述"]
    assert client.get("/audit/AUD-NOPE/material").status_code == 404
