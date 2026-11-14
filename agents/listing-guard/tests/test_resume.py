"""断点续跑测试：挂起 → 新图实例恢复 → 已完成算子不重跑。"""

from langgraph.types import Command

from listing_guard.graph.builder import build_app
from listing_guard.runner import audit_id_of, initial_input, load_fixture, run_audit
from listing_guard.tools import gov_api


def test_submit_pauses_at_gov(tmp_path):
    db = tmp_path / "cp.sqlite"
    result = run_audit(load_fixture("normal_01"), checkpoint_path=db, auto=False)
    state = result["state"]

    assert result["pending"] == ("gov_verify",)
    assert "gov_verify" not in state["factors"]
    # 五个子图因子全部完成（表单/图像/视频/文本/基本面）
    assert set(state["factors"]) == {
        "form_rules",
        "image_authenticity",
        "image_text_consistency",
        "video_authenticity",
        "text_risk",
        "community_match",
        "price_reasonable",
        "listing_duplicate",
        "credit",
    }


def test_resume_in_new_app_no_rerun(tmp_path):
    """新图实例（等价新进程）恢复：每个节点只执行一次。"""
    db = tmp_path / "cp.sqlite"
    fixture = load_fixture("normal_01")
    audit_id = audit_id_of(fixture)
    cfg = {"configurable": {"thread_id": audit_id}}

    run_audit(fixture, checkpoint_path=db, auto=False)

    app2 = build_app(db)  # 新进程等价物
    snapshot = app2.get_state(cfg)
    assert snapshot.next == ("gov_verify",)

    app2.invoke(Command(resume=gov_api.mock_callback(audit_id)), cfg)
    final = app2.get_state(cfg)

    assert final.next == ()
    values = final.values
    assert values["verdict"] == "auto_approve"
    assert len(values["factors"]) == 10

    # 已完成算子不重跑：每个子图的轨迹事件只出现一次
    events = values["events"]
    assert sum(1 for e in events if e.startswith("[材料接入]")) == 1
    assert sum(1 for e in events if e.startswith("[注入防护]")) == 1
    assert sum(1 for e in events if e.startswith("[基本面子图]")) == 1
    assert sum(1 for e in events if e.startswith("[图片VLM子图]")) == 1
    assert sum(1 for e in events if e.startswith("[视频子图]")) == 1
    assert sum(1 for e in events if e.startswith("[政府核验]")) == 1


def test_gov_request_idempotent_on_reentry(tmp_path):
    """resume 时 gov_verify 节点重新进入：不得重复发起核验请求。"""
    db = tmp_path / "cp.sqlite"
    fixture = load_fixture("normal_01")
    audit_id = audit_id_of(fixture)
    cfg = {"configurable": {"thread_id": audit_id}}
    pending_file = gov_api.get_settings().gov_pending_path

    app = build_app(db)
    app.invoke(initial_input(fixture), cfg)  # 发起 + 挂起

    def _mine() -> list[dict]:
        return [r for r in gov_api._read_pending(pending_file).values() if r["audit_id"] == audit_id]

    request_id = _mine()[0]["request_id"]
    app.invoke(Command(resume=gov_api.mock_callback(audit_id)), cfg)  # 节点重新进入

    mine = _mine()
    assert len(mine) == 1, "不得重复发起请求"
    assert mine[0]["request_id"] == request_id


def test_material_supplement_resume(tmp_path):
    """缺材料挂起 → 补拍 resume → 全流程正常出终态。"""
    db = tmp_path / "cp.sqlite"
    fixture = load_fixture("missing_material_07")

    result = run_audit(fixture, checkpoint_path=db, auto=False)
    assert result["pending"] == ("wait_material",)
    assert result["state"]["missing_materials"]

    # 第二阶段：补材料 resume + 后续 gov/human 恢复
    from langgraph.types import Command as C  # noqa: PLC0415

    app2 = build_app(db)
    cfg = {"configurable": {"thread_id": audit_id_of(fixture)}}
    app2.invoke(C(resume=fixture["supplement"]), cfg)
    app2.invoke(C(resume=gov_api.mock_callback(audit_id_of(fixture))), cfg)
    final = app2.get_state(cfg).values
    assert final["verdict"] == "auto_approve"
    assert final["listing"]["images"], "补拍图片已合入"
