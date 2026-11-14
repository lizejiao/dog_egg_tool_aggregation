"""政府核验节点 —— 外部异步回调 + 检查点断点复跑的核心演示。

执行语义：
1. 首次进入：ensure_request 幂等发起核验请求 → interrupt() 挂起，
   进程可退出；此刻已完成节点（材料接入/注入防护/图像/轻核验/合规/信用）
   的结果都已在 SQLite 检查点里；
2. 回调到达：用同一 thread_id + Command(resume=回调数据) 恢复，
   本节点重新进入（所以发起必须幂等），interrupt() 这次直接返回回调数据；
3. 按回调结果落因子：passed → 强可信；failed → 强因子否决转人工；timeout → degraded 降级。
"""

from __future__ import annotations

from langgraph.types import interrupt

from listing_guard.state import AuditState
from listing_guard.tools import gov_api
from listing_guard.tools.registry import make_factor


def gov_verify(state: AuditState) -> dict:
    factors = state.get("factors", {})
    if "gov_verify" in factors:
        return {"events": ["[政府核验] 因子已存在，跳过（断点续跑不重算）"]}

    listing = state["listing"]
    request = gov_api.ensure_request(
        state["audit_id"], listing, hint=state.get("gov_hint", "passed")
    )

    # ---- 挂起点：等待监管平台异步回调（分钟级至小时级）----
    callback = interrupt(
        {
            "reason": "等待政府核验异步回调",
            "request": {
                "request_id": request["request_id"],
                "community": request["community"],
                "city": request["city"],
            },
        }
    )

    status = (callback or {}).get("status", "passed")
    message = (callback or {}).get("message", "")
    listing_code = (callback or {}).get("listing_code", "")

    if status == "timeout":
        factor = make_factor(
            "gov_verify",
            0.0,
            f"政府核验超时未返回（{message}），因子降级并从加权分母剔除",
            degraded=True,
        )
    elif status == "failed":
        factor = make_factor(
            "gov_verify",
            0.2,
            f"政府核验失败：{message}（备案码 {listing_code}）",
            hit=True,
            veto_triggered=True,  # 强因子：直接转人工而非自动驳回
        )
    else:
        factor = make_factor(
            "gov_verify", 1.0, f"政府核验通过：{message}（备案码 {listing_code}）"
        )

    return {
        "factors": {"gov_verify": factor},
        "events": [
            f"[政府核验] 回调到达：status={status}，request={request['request_id']}"
        ],
    }
