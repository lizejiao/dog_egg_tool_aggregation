"""文本合规与注入检测。

房源描述 / 图片 OCR 文本都是用户可控输入：
- 注入特征命中 → 强制转人工，模型不允许自由文本影响路由；
- 本模块只产出结构化命中清单，不做任何"理解式"放行。
"""

from __future__ import annotations

import re

from listing_guard.settings import Settings, get_settings


def _match_all(text: str, patterns: list[str]) -> list[str]:
    hits = []
    for p in patterns:
        if re.search(p, text):
            hits.append(p)
    return hits


def scan_injection(text: str, settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    return _match_all(text or "", s.compliance.injection_patterns)


def scan_banned_words(text: str, settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    return [w for w in s.compliance.banned_words if w in (text or "")]


def scan_contact(text: str, settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    return _match_all(text or "", s.compliance.contact_patterns)
