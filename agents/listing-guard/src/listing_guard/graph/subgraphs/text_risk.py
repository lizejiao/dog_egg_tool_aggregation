"""子图 4：文本内容风控 text_risk_subgraph。

规则层（确定性）+ 语义层（概率性，LLM）双路扫描：
- 规则层：违禁词 / 联系方式飞单 / 注入特征（正则，可配置热更）；
- 语义层：违规承诺（"拎包入住保证升值"）、隐性引流话术等规则覆盖不到的软性风险，
  live 模式下由 LLM 结构化判定；mock 用确定性启发式（关键词组）等价演示。
- 房源描述是用户可控输入：仅作为待检测数据，永远不拼进任何 system 指令；
  命中注入特征在此只记风险项，路由短路由主图注入防护负责。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from listing_guard.state import append_list
from listing_guard.tools.compliance import scan_banned_words, scan_contact, scan_injection
from listing_guard.tools.registry import make_factor

# 语义层启发式（mock 等价物；live 换 LLM 结构化判定）
SEMANTIC_RISK_PATTERNS = [
    "稳赚", "保证升值", "包租", "返租金", "投资回报",
    "拎包入住保证", "百分百", "全城最低", "错过不再",
]


class TextRiskState(TypedDict, total=False):
    listing: dict
    rule_hits: Annotated[list, append_list]  # 子图内部：规则层命中（不外泄明细）
    factors: dict


def _rule_scan(state: TextRiskState) -> dict:
    listing = state.get("listing") or {}
    ocr_texts = [img.get("mock", {}).get("ocr_text", "") for img in listing.get("images", [])]
    asr_texts = [v.get("mock", {}).get("asr_text", "") for v in listing.get("videos", [])]
    corpus = "\n".join(
        [listing.get("description", ""), listing.get("title", "")] + ocr_texts + asr_texts
    )

    hits = []
    banned = scan_banned_words(corpus)
    contact = scan_contact(corpus)
    injection = scan_injection(corpus)
    if banned:
        hits.append(f"违禁词：{'、'.join(banned)}")
    if contact:
        hits.append(f"联系方式/飞单：命中 {len(contact)} 个特征")
    if injection:
        hits.append(f"注入特征：{'、'.join(injection)}")
    return {"rule_hits": hits}


def _semantic_scan(state: TextRiskState) -> dict:
    """语义层：违规承诺 / 诱导话术（live 走 LLM；mock 走启发式等价演示）。"""
    listing = state.get("listing") or {}
    corpus = f"{listing.get('title', '')}\n{listing.get('description', '')}"

    from listing_guard.settings import get_settings  # noqa: PLC0415

    if get_settings().env.audit_mode == "live":  # pragma: no cover
        semantic = _semantic_live(corpus)
    else:
        semantic = [p for p in SEMANTIC_RISK_PATTERNS if p in corpus]

    rule_hits = state.get("rule_hits") or []
    if semantic:
        rule_hits = rule_hits + [f"语义风险：{'、'.join(semantic)}"]
    return {"rule_hits": rule_hits}


def _semantic_live(corpus: str) -> list[str]:  # pragma: no cover
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return []
    from listing_guard.settings import CONFIG_DIR, get_settings  # noqa: PLC0415

    s = get_settings()
    prompt = (CONFIG_DIR / "prompts" / "opinion.md").read_text(encoding="utf-8")
    llm = ChatOpenAI(
        model=s.env.text_model, api_key=s.env.openai_api_key,
        base_url=s.env.openai_base_url, temperature=0,
    ).with_structured_output({"risks": "string", "level": "string"})
    r = llm.invoke([("system", prompt), ("user", f"扫描违规承诺与诱导话术：\n{corpus}")])
    return [f"{r.get('risks', '')}（{r.get('level', '')}）"] if r.get("risks") else []


def _collect(state: TextRiskState) -> dict:
    hits = state.get("rule_hits") or []
    veto = bool(hits)
    factor = make_factor(
        "text_risk",
        0.05 if veto else 1.0,
        "；".join(hits) if hits else "标题/描述/OCR/ASR 无违禁、无飞单、无注入、无违规承诺",
        hit=veto,
        veto_triggered=veto,
    )
    return {"factors": {"text_risk": factor}}


def build_text_risk_subgraph() -> StateGraph:
    g: StateGraph = StateGraph(TextRiskState)
    g.add_node("rule_scan", _rule_scan)
    g.add_node("semantic_scan", _semantic_scan)
    g.add_node("collect", _collect)
    g.add_edge(START, "rule_scan")
    g.add_edge("rule_scan", "semantic_scan")
    g.add_edge("semantic_scan", "collect")
    g.add_edge("collect", END)
    return g.compile()
