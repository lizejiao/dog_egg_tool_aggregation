"""因子结果构造 + 算子注册表。

统一超时/异常兜底：算子抛异常时降级为 degraded 因子（score 记 0 但从加权分母剔除），
单算子故障不阻断整体判定。
"""

from __future__ import annotations

import time
from functools import wraps

from listing_guard.settings import get_settings
from listing_guard.state import FactorResult


def make_factor(
    factor_id: str,
    score: float,
    detail: str,
    *,
    hit: bool = False,
    veto_triggered: bool = False,
    degraded: bool = False,
    elapsed_ms: int = 0,
) -> FactorResult:
    """按 factors.yaml 的注册信息补齐权重与否决属性。"""
    conf = get_settings().factors[factor_id]
    return FactorResult(
        factor_id=factor_id,
        name=conf.name,
        kind=conf.kind,
        score=round(max(0.0, min(1.0, score)), 4),
        weight=conf.weight,
        is_veto=conf.veto,
        veto_triggered=veto_triggered,
        hit=hit,
        detail=detail,
        degraded=degraded,
        elapsed_ms=elapsed_ms,
    )


def timed_operator(fn):
    """算子装饰器：计时 + 异常降级为 degraded 因子。

    被装饰函数签名 (…) -> (factor_id, score, detail, kwargs…)，
    返回 make_factor 的关键字参数（不含 factor_id）。
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            factor_id, score, detail, extra = fn(*args, **kwargs)
            elapsed = int((time.perf_counter() - start) * 1000)
            return make_factor(factor_id, score, detail, elapsed_ms=elapsed, **(extra or {}))
        except Exception as exc:  # noqa: BLE001 —— 任何算子故障都降级，不阻断图
            factor_id = getattr(fn, "factor_id", "unknown")
            elapsed = int((time.perf_counter() - start) * 1000)
            return make_factor(
                factor_id,
                0.0,
                f"算子异常降级：{exc}",
                degraded=True,
                elapsed_ms=elapsed,
            )

    wrapper.factor_id = getattr(fn, "factor_id", "unknown")
    return wrapper
