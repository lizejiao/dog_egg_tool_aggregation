"""文本清洗：把 Markdown / 纯文本演讲稿处理成适合 TTS 的干净文本。

规则（docs/ARCHITECTURE.md §4.3）：
1. 剥离 YAML front-matter、代码围栏、HTML 注释——这些内容绝不能被念出来
2. Markdown 语法降级为纯文本：标题去 #、链接保文字去 URL、图片整行删、
   粗斜体去标记、表格保留单元格文字、引用去 >、列表去序号符
3. 保留段落与换行语义（分句器依赖段落边界）
4. 保留 [emotion] 形式的英文情绪标签（Fish 官方语法，合成时透传）

实现说明：正则按"先特殊后一般"的顺序应用；不追求覆盖所有 Markdown 扩展
语法（目标是演讲稿，不是任意文档转换器）。
"""
import re

# 1) YAML front-matter（文件开头 --- ... ---）
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)

# 2) 代码围栏（``` 或 ~~~ 整块删除，含围栏行本身）
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*(```|~~~)[ \t]*$", re.MULTILINE | re.DOTALL)

# 3) HTML 注释
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# 4) Markdown 元素（应用顺序有依赖：链接必须在图片/引用之后处理前文检查）
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")          # ![alt](url) → 整体删
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")             # [text](url) → text
_AUTO_LINK_RE = re.compile(r"<https?://[^>]+>")             # <http://...> → 删
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")             # 表格行 → 保留单元格文字
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")         # 表格分隔行 → 删
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")                   # **bold** → bold
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")      # *it* → it
_CODE_TICK_RE = re.compile(r"`([^`\n]+)`")                  # `code` → code（行内代码念出内容
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}\s+", re.MULTILINE)  # 标题 → 去 # 号
_QUOTE_RE = re.compile(r"^[ \t]*>\s?", re.MULTILINE)         # 引用 → 去 >
# 列表标记（- / * / 1.）→ 去；注意 * 与斜体冲突，仅在行首处理
_LIST_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_STRIKE_RE = re.compile(r"~~([^~]+)~~")                      # ~~del~~ → del

# 残留的裸 URL 单独成行 → 删（避免念出一串域名）
_BARE_URL_RE = re.compile(r"^\s*https?://\S+\s*$", re.MULTILINE)


def clean_script(raw: str, strip_stage_cues: bool = True) -> str:
    """入口：原始稿件 → 干净文本（保留段落结构）。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _FRONT_MATTER_RE.sub("", text, count=1)
    text = _FENCE_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)

    # 表格：先删分隔行，再把单元格文字用空格拼接（按行处理保持段落语义）
    lines = text.split("\n")
    out_lines = []
    for line in lines:
        if _TABLE_SEP_RE.match(line):
            continue
        m = _TABLE_ROW_RE.match(line)
        if m:
            cells = [c.strip() for c in m.group(1).split("|") if c.strip()]
            out_lines.append("，".join(cells))
        else:
            out_lines.append(line)
    text = "\n".join(out_lines)

    # 行内元素（图片最先行内处理，避免链接正则误吞）
    text = _BARE_URL_RE.sub("", text)
    text = _IMAGE_RE.sub("", text)
    text = _AUTO_LINK_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _CODE_TICK_RE.sub(r"\1", text)

    # 行首元素
    text = _HEADING_RE.sub("", text)
    text = _QUOTE_RE.sub("", text)
    text = _LIST_RE.sub("", text)

    if strip_stage_cues:
        # 舞台提示（中文方括号）：[停顿]、[掌声] 等——注意不能误伤英文情绪标签
        # 英文标签 [pause]/[excited tone] 由分句器透传给引擎；仅剥 [中文] 方括号
        text = re.sub(r"[（(\[][\u4e00-\u9fff][^）)\]]{0,8}[）)\]](?![A-Za-z])", "", text)

    # 压缩空行：连续 2+ 空行 → 1 空行（段落边界保留，多余空白无意义）
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 去每行首尾空白（保留行本身）
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()
