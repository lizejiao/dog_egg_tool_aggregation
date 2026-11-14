"""图状态定义。

设计要点：
- state 里只放结构化结论（factor 字典、命中清单、轨迹事件），原始图片 base64 / OCR 全文
  只在子图内部流转，返回主图前收敛为结构化字段 —— 子图上下文隔离。
- 并行扇出的多个算子节点同时写 factors / events 等字段，必须挂 reducer 合并。
- 全部用 TypedDict + 普通 dict（可被 LangGraph 的 msgpack 序列化器直接落检查点）。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


def merge_factors(current: dict | None, update: dict | None) -> dict:
    """并行算子各自写入自己的 factor 结果，dict 合并（后者覆盖同名 key）。"""
    merged = dict(current or {})
    merged.update(update or {})
    return merged


def append_list(current: list | None, update: list | None) -> list:
    """并行写入安全版 operator.add（处理初始 None）。"""
    return (current or []) + (update or [])


class FactorResult(TypedDict, total=False):
    factor_id: str        # 因子 id（factors.yaml 注册）
    name: str             # 因子名
    kind: str             # deterministic | probabilistic
    score: float          # 0~1，越高越可信
    weight: float         # 因子权重（factors.yaml）
    is_veto: bool         # 是否否决项
    veto_triggered: bool  # 否决项是否命中
    hit: bool             # 是否命中可疑项（用于命中清单）
    detail: str           # 算子结论明细（可解释性载体）
    degraded: bool        # 算子超时/异常降级标记
    elapsed_ms: int       # 算子耗时


class AuditState(TypedDict, total=False):
    # ---- 输入 ----
    audit_id: str               # 审核单号 = thread_id
    listing: dict               # 归一化后的房源材料（图片仅存路径引用）
    policy_version: str         # 策略版本（因子+阈值哈希），落检查点可回溯

    # ---- 接入与注入防护 ----
    missing_materials: list     # 缺失材料清单（非空 → 挂起等房东补拍）
    injection_hits: Annotated[list, append_list]  # 命中的注入特征

    # ---- 并行验真因子（扇出 reducer 合并）----
    factors: Annotated[dict, merge_factors]
    events: Annotated[list, append_list]          # 执行轨迹（演示/排查用）
    gov_requested: dict         # 已发起的政府核验请求（幂等发起的依据）
    gov_hint: str               # mock 专用：政府核验回调结果提示（passed/failed/timeout）

    # ---- 汇聚与决策 ----
    score: float                # 加权置信度（degraded 因子重归一化）
    veto_hits: list             # 命中的否决项清单
    hit_reasons: list           # 命中因子清单（含权重与明细，可解释、可申诉）
    verdict: str                # auto_approve | human_review | auto_reject | human_approved |
                                # human_rejected | need_material
    opinion: str                # 转人工时的 AI 评判意见

    # ---- 人工/回调 ----
    human_decision: dict        # 人工裁决结果
    finished: bool
