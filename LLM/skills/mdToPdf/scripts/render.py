#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mdToPdf —— 通用 Markdown -> A4 PDF 渲染脚本（中文友好）。

用法：
    python3 render.py --md input.md --out output.pdf [--photo 照片.jpg]
                      [--font-size 9.5] [--margin 12]

核心解决的是「格式问题」而非内容假设：
    - 折行：所有普通空格转不间断空格 + wordWrap=CJK，避免英文/混排术语
      （如 Function Calling、VS Code、RAG /）被拦腰拆到两行；断点落在中文标点处。
    - 缩进：列表按行首缩进自动判定层级（一级 / 二级 / 三级），不会把缩进
      当普通段落、也不会把行首 "-" 字面打印。
    - 不丢内容：按 md 标题层级（#/##/###/####）顺序渲染，不硬编码行号截断；
      未知行按普通段落兜底，任何 md 元素都不会被静默丢弃。

依赖：reportlab, Pillow
"""
import argparse
import os
import re
import sys

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Image as RLImage
from PIL import Image as PILImage

HEITI_LIGHT = "/System/Library/Fonts/STHeiti Light.ttc"
HEITI_MEDIUM = "/System/Library/Fonts/STHeiti Medium.ttc"
ACCENT = colors.HexColor("#1f4e79")   # 标题/分隔线主色（通用深蓝）
INK = colors.HexColor("#222222")
GRAY = colors.HexColor("#444444")


def register_fonts():
    pdfmetrics.registerFont(TTFont("Heiti", HEITI_LIGHT, subfontIndex=0))
    pdfmetrics.registerFont(TTFont("Heiti-B", HEITI_MEDIUM, subfontIndex=0))
    registerFontFamily("Heiti", normal="Heiti", bold="Heiti-B",
                      italic="Heiti", boldItalic="Heiti-B")


def make_styles(base_size: float):
    """base_size 是正文段落字号，其余样式按比例派生。"""
    s = {}
    s["h1"] = ParagraphStyle("h1", fontName="Heiti-B", fontSize=base_size * 2.0,
                            leading=base_size * 2.6, textColor=colors.HexColor("#111111"),
                            spaceAfter=6, wordWrap="CJK")
    s["h2"] = ParagraphStyle("h2", fontName="Heiti-B", fontSize=base_size * 1.35,
                             leading=base_size * 1.9, textColor=ACCENT,
                             spaceBefore=8, spaceAfter=2, wordWrap="CJK")
    s["h3"] = ParagraphStyle("h3", fontName="Heiti-B", fontSize=base_size * 1.15,
                             leading=base_size * 1.6, textColor=INK,
                             spaceBefore=4, spaceAfter=1, wordWrap="CJK")
    s["h4"] = ParagraphStyle("h4", fontName="Heiti-B", fontSize=base_size * 1.05,
                             leading=base_size * 1.5, textColor=INK,
                             spaceBefore=2, spaceAfter=0, wordWrap="CJK")
    s["body"] = ParagraphStyle("body", fontName="Heiti", fontSize=base_size,
                                leading=base_size * 1.55, textColor=INK,
                                spaceAfter=2, wordWrap="CJK")
    s["li1"] = ParagraphStyle("li1", fontName="Heiti", fontSize=base_size,
                             leading=base_size * 1.55, textColor=INK,
                             spaceAfter=1.5, wordWrap="CJK")
    s["li2"] = ParagraphStyle("li2", fontName="Heiti", fontSize=base_size * 0.95,
                              leading=base_size * 1.48, leftIndent=16,
                              textColor=GRAY, spaceAfter=1, wordWrap="CJK")
    s["li3"] = ParagraphStyle("li3", fontName="Heiti", fontSize=base_size * 0.9,
                              leading=base_size * 1.4, leftIndent=30,
                              textColor=GRAY, spaceAfter=1, wordWrap="CJK")
    return s


def inline(t: str) -> str:
    """转义 &<>、**粗体**、`代码`；兼容常见行内 HTML；标签外空格全部转不间断空格。"""
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 兼容行内 HTML：<strong>x</strong> -> 粗体；去掉块级 <div> 标签
    t = re.sub(r"&lt;strong&gt;(.+?)&lt;/strong&gt;", r"<b>\1</b>", t)
    t = re.sub(r"&lt;/?div[^&]*&gt;", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r'<font color="#888">\1</font>', t)
    # 切出 <...> 标签，只把标签外的普通空格转不间断空格（保护标签属性里的空格）
    parts = re.split(r"(<[^>]+>)", t)
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace(" ", "\xa0")
    return "".join(parts)


def list_prefix(indent: int) -> str:
    """按缩进层级返回 bullet 前缀。"""
    if indent >= 4:
        return "◦ "   # 三级
    if indent >= 2:
        return "◦ "   # 二级
    return "• "        # 一级


def render(md_path, out_path, photo_path=None, base_size=9.5, margin=12):
    register_fonts()
    styles = make_styles(base_size)
    lines = open(md_path, encoding="utf-8").read().split("\n")

    story = []

    # 可选：第一页右上角放一张图（如证件照/Logo）。不传则不渲染。
    if photo_path and os.path.exists(photo_path):
        iw, ih = PILImage.open(photo_path).size
        ph_w = 26 * mm
        ph_h = ph_w * ih / iw
        story.append(RLImage(photo_path, width=ph_w, height=ph_h))
        story.append(Spacer(1, 4))

    for raw_line in lines:
        s = raw_line.rstrip()
        stt = s.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if not stt:
            continue
        if stt == "---":
            story.append(HRFlowable(width="100%", thickness=0.6,
                                    color=colors.HexColor("#cccccc"),
                                    spaceBefore=4, spaceAfter=4))
            continue
        if stt.startswith("#### "):
            story.append(Paragraph(inline(stt[5:]), styles["h4"]))
        elif stt.startswith("### "):
            story.append(Paragraph(inline(stt[4:]), styles["h3"]))
        elif stt.startswith("## "):
            story.append(Paragraph(inline(stt[3:]), styles["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.8, color=ACCENT,
                                    spaceBefore=1, spaceAfter=3))
        elif stt.startswith("# "):
            story.append(Paragraph(inline(stt[2:]), styles["h1"]))
        elif stt.startswith("- ") or stt.startswith("* "):
            body_text = inline(stt[2:])
            if indent >= 4:
                story.append(Paragraph(list_prefix(indent) + body_text, styles["li3"]))
            elif indent >= 2:
                story.append(Paragraph(list_prefix(indent) + body_text, styles["li2"]))
            else:
                story.append(Paragraph(list_prefix(indent) + body_text, styles["li1"]))
        else:
            # 普通段落兜底：不丢任何内容
            story.append(Paragraph(inline(stt), styles["body"]))

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=margin * mm, rightMargin=margin * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=os.path.splitext(os.path.basename(out_path))[0])
    doc.build(story)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Markdown -> A4 PDF (通用)")
    ap.add_argument("--md", required=True, help="输入 md 文件路径")
    ap.add_argument("--out", required=True, help="输出 pdf 文件路径")
    ap.add_argument("--photo", default=None, help="可选：第一页右上角图片（证件照/Logo）")
    ap.add_argument("--font-size", type=float, default=9.5,
                    help="正文字号，默认 9.5")
    ap.add_argument("--margin", type=float, default=12,
                    help="左右边距 mm，默认 12")
    args = ap.parse_args()

    if not os.path.exists(args.md):
        sys.exit(f"找不到 md 文件: {args.md}")

    out = render(args.md, args.out, photo_path=args.photo,
                 base_size=args.font_size, margin=args.margin)
    print(f"OK -> {out}")


if __name__ == "__main__":
    main()
