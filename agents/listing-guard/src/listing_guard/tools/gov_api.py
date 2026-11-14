"""政府核验（对接住建/租赁监管平台）—— demo 用本地 JSON 模拟"异步消息队列"。

关键机制：
- ensure_request 幂等发起（resume 时节点重新进入，不能重复发起）；
- 发起后图在 gov_verify 节点 interrupt() 挂起，回调到达后用同一 thread_id
  以 Command(resume=回调数据) 恢复 —— 进程可以退出重开（检查点在 SQLite）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from listing_guard.settings import Settings, get_settings


def _read_pending(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_pending(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_request(audit_id: str, listing: dict, hint: str = "passed", settings: Settings | None = None) -> dict:
    """幂等发起政府核验请求：已发起则直接返回已有请求（不重复发起）。"""
    s = settings or get_settings()
    pending = _read_pending(s.gov_pending_path)
    if audit_id in pending:
        return pending[audit_id]
    request = {
        "request_id": f"GOV-{int(time.time() * 1000)}",
        "audit_id": audit_id,
        "community": listing.get("community"),
        "city": listing.get("city"),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "_hint": hint,  # mock：回调结果提示（passed/failed/timeout）
    }
    pending[audit_id] = request
    _write_pending(s.gov_pending_path, pending)
    return request


def mock_callback(audit_id: str, settings: Settings | None = None) -> dict:
    """模拟监管平台异步回调：从待回调队列取请求，生成回调数据（分钟级延迟的瞬时等价）。

    幂等：已投递过的请求重复回调返回同一份数据（gov_verify 节点 resume 时
    会重新进入并再次 ensure_request，投递记录必须保留，否则会重复发起）。
    """
    s = settings or get_settings()
    pending = _read_pending(s.gov_pending_path)
    request = pending.get(audit_id)
    if request is None:
        raise KeyError(f"未找到待回调的政府核验请求：{audit_id}")
    if request.get("callback"):
        return request["callback"]

    hint = request.get("_hint", "passed")
    callback = {
        "request_id": request["request_id"],
        "listing_code": f"BJ{audit_id[-4:]}{request['request_id'][-4:]}",
        "status": hint,
        "message": {
            "passed": "核验通过：房源已备案",
            "failed": "核验失败：未找到备案信息",
            "timeout": "超时未返回：平台无响应",
        }.get(hint, "核验通过"),
    }
    request["callback"] = callback
    request["delivered"] = True
    pending[audit_id] = request
    _write_pending(s.gov_pending_path, pending)
    return callback


def reset(audit_id: str, settings: Settings | None = None) -> None:
    """清掉某审核单的待回调记录（runner 重置 thread 时配套调用）。"""
    s = settings or get_settings()
    pending = _read_pending(s.gov_pending_path)
    if pending.pop(audit_id, None) is not None:
        _write_pending(s.gov_pending_path, pending)


def pending_requests(settings: Settings | None = None) -> list[dict]:
    """未投递的待回调请求（delivered 标记的保留供幂等查询）。"""
    s = settings or get_settings()
    return [r for r in _read_pending(s.gov_pending_path).values() if not r.get("delivered")]
