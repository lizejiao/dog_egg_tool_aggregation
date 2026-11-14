"""智能分句：清洗后的文本 → 合成段列表。

分句质量直接决定成品稳定性（自回归模型长句会漂移、短句会碎），规则：
1. 段落优先：先按空行切段落，段与段之间标记 para_break（拼接时停顿更长）
2. 段内按终结标点（。！？；…!?.）聚簇，目标区间 [min_chars, max_chars]
   - 聚簇不足 min_chars 时与下句合并（避免碎段）
   - 超过 max_chars 且无标点时硬切（罕见，兜底）
3. [emotion] 标签视为普通字符参与计数，不作为切分点
4. 输出段文本包含原句末标点（TTS 引擎按标点控制韵律）

返回格式：[{seq, text, para_break}]
"""
import re
from typing import Dict, List

# 终结标点：出现在这些符号之后才允许断段
_BREAK_CHARS = "。！？；…!?."
# 一个"句子"= 从上一切点到下一个终结标点（含其后跟随的引号/括号闭合）
_SENT_RE = re.compile(r"[^" + _BREAK_CHARS + r"]+[" + _BREAK_CHARS + r"]*[”』」\）)]*")


def split_into_segments(cleaned: str, max_chars: int = 80,
                        min_chars: int = 40) -> List[Dict]:
    """入口：干净文本 → 段列表。"""
    if max_chars < 20:
        raise ValueError("max_chars 过小（>=20）")
    segments: List[Dict] = []
    seq = 0
    paragraphs = [p.strip() for p in re.split(r"\n+", cleaned) if p.strip()]

    for para in paragraphs:
        sentences = [s for s in _SENT_RE.findall(para) if s.strip()]
        # 句子级兜底：无终结标点的超长段硬切
        sentences = _split_oversize(sentences, max_chars)
        # 聚簇：[min_chars, max_chars] 区间内合并相邻句
        buf = ""
        para_first = True
        for sent in sentences:
            candidate = (buf + sent).strip()
            if buf and len(candidate) > max_chars:
                seq += 1
                segments.append({"seq": seq, "text": buf,
                                 "para_break": para_first})
                para_first = False
                buf = sent
            else:
                buf = candidate
        if buf:
            # 段落收尾：不足 min_chars 且前一段非段落开头 → 并入前一段（避免碎尾）
            if (len(buf) < min_chars and segments
                    and not segments[-1]["para_break"]
                    and len(segments[-1]["text"]) + len(buf) <= max_chars + 20):
                segments[-1]["text"] += buf
            else:
                seq += 1
                segments.append({"seq": seq, "text": buf,
                                 "para_break": para_first})

    return segments


def _split_oversize(sentences: List[str], max_chars: int) -> List[str]:
    """无标点的超长"句子"按 max_chars 硬切（按顿号/逗号优先，其次按字数）。"""
    out: List[str] = []
    for sent in sentences:
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        cur = ""
        # 优先按次级标点（，、：）切
        for piece in re.split(r"(?<=[，、：:,])", sent):
            if len(cur) + len(piece) > max_chars and cur:
                out.append(cur)
                cur = piece
            else:
                cur += piece
        if cur:
            out.append(cur)
        # 仍超长（无任何标点）→ 纯字数硬切
        out = [p if len(p) <= max_chars else q
               for p in out
               for q in ([p[i:i + max_chars] for i in range(0, len(p), max_chars)])]
    return out


def estimate_segments(text: str, max_chars: int = 80) -> int:
    """轻量预估段数（上传稿时给前端展示，不落库）。"""
    return len(split_into_segments(text, max_chars=max_chars))
