"""转人工评判意见生成（AI 给人工的辅助判断）。

mock：按命中因子模板生成确定性意见；
live：结构化输出（JSON Schema 约束，模型不允许自由文本影响路由，
意见仅展示给人工，verdict 由 routes.py 的纯函数决定）。
"""

from __future__ import annotations

from listing_guard.settings import CONFIG_DIR, get_settings
from listing_guard.state import AuditState


def build_opinion(state: AuditState) -> str:
    s = get_settings()
    if s.env.audit_mode == "live":
        return _build_opinion_live(state)
    return _build_opinion_mock(state)


def _build_opinion_mock(state: AuditState) -> str:
    score = state.get("score", 0.0)
    reasons = state.get("hit_reasons") or []
    if state.get("injection_hits"):
        return (
            "描述文本命中提示词注入特征（"
            + "、".join(state["injection_hits"])
            + "），已强制转人工，请核实是否存在诱导性内容。"
        )
    if not reasons:
        return f"置信度 {score}，未命中可疑因子，建议放行。"
    top = sorted(reasons, key=lambda f: f["weight"], reverse=True)[:3]
    lines = [f"置信度 {score}，命中 {len(reasons)} 个可疑因子，建议人工复核，重点关注："]
    for f in top:
        lines.append(f"- {f['name']}（权重 {f['weight']}，得分 {f['score']}）：{f['detail']}")
    if state.get("veto_hits"):
        lines.append(f"否决项命中：{'、'.join(state['veto_hits'])}，建议驳回。")
    return "\n".join(lines)


def _build_opinion_live(state: AuditState) -> str:  # pragma: no cover
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("live 模式需要安装 live 依赖组：uv sync --group live") from exc

    s = get_settings()
    prompt = (CONFIG_DIR / "prompts" / "opinion.md").read_text(encoding="utf-8")
    factors_text = "\n".join(
        f"- {f['name']}：score={f['score']}, weight={f['weight']}, detail={f['detail']}"
        for f in (state.get("hit_reasons") or [])
    )
    llm = ChatOpenAI(
        model=s.env.text_model,
        api_key=s.env.openai_api_key,
        base_url=s.env.openai_base_url,
        temperature=0,
    ).with_structured_output({"opinion": "string", "suggestion": "string"})
    result = llm.invoke(
        [
            ("system", prompt),
            ("user", f"置信度：{state.get('score')}\n命中因子：\n{factors_text}\n注入命中：{state.get('injection_hits')}"),
        ]
    )
    return f"{result['opinion']}（模型建议：{result['suggestion']}）"
