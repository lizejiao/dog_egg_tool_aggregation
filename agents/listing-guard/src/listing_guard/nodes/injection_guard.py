"""注入防护：用户可控输入的指令剥离与注入特征检测。

命中注入特征 → injection_hits 非空 → route_after_injection 强制转人工。
分隔符隔离在这里体现：描述文本仅作为"待检测数据"，永远不拼进任何 system 指令。
"""

from __future__ import annotations

from listing_guard.tools.compliance import scan_injection
from listing_guard.state import AuditState


def injection_guard(state: AuditState) -> dict:
    listing = state.get("listing") or {}
    texts = {
        "标题": listing.get("title", ""),
        "描述": listing.get("description", ""),
    }
    hits: list[str] = []
    for where, text in texts.items():
        for pattern in scan_injection(text):
            hits.append(f"{where}命中『{pattern}』")

    events = [f"[注入防护] 扫描 {len(texts)} 段用户文本，命中 {len(hits)} 个注入特征"]
    if hits:
        events.append("[注入防护] 命中注入特征，强制转人工（自由文本不允许影响路由）")
    return {"injection_hits": hits, "events": events}
