---
name: mdToPdf
description: 把任意 Markdown 渲染成排版规整的 A4 PDF（中文友好）。聚焦格式问题——折行/换行、缩进层级、字体、分页，确保 md 里的元素不缺失、不被静默丢弃。适用于用户给一份 md 要求导出 PDF 的通用场景（文档、笔记、说明、简历等）。
---

# mdToPdf — 通用 Markdown → A4 PDF

把 Markdown 渲染成排版规整的 A4 PDF，**聚焦格式正确性**，不假设内容是简历还是文档。

## 何时用

- 用户给一份 `.md`，要求转成 PDF。
- 关注的是：折行好看（不把英文术语拦腰拆开）、缩进层级正确、该有的内容都在、分页合理。

不适用：需要保留网页 CSS / 复杂表格 / 图文混排的导出，另选方案。

## 命令

```bash
python3 scripts/render.py --md 输入.md --out 输出.pdf \
    [--photo 图片.jpg] [--font-size 9.5] [--margin 12]
```

- `--photo`：可选，放在第一页右上角（证件照、Logo）。不需要就别传。
- `--font-size`：正文字号，默认 9.5。内容多想压页就调小，想铺满就调大。
- `--margin`：左右边距 mm，默认 12。

## 渲染哪些 md 元素（不丢内容）

| md | PDF |
|---|---|
| `# / ## / ### / ####` | 四级标题，逐级变小，`##` 带分隔线 |
| `- ` 或 `* ` | 列表，按行首缩进自动分一/二/三级（`•` / `◦`） |
| 普通行 | 正文段落兜底，不丢弃 |
| `**粗体**` | 粗体 |
| `` `代码` `` | 灰色 |
| `---` | 细分隔线 |

任何无法识别的行都会按**普通段落**渲染，保证 md 里写的内容在 PDF 里都出现，不会被静默跳过。

## 渲染后必做：视觉自检

生成后必须把每页渲成 PNG 看一眼，不只看页数：

```python
import pymupdf
d = pymupdf.open(out_pdf)
print("pages:", d.page_count)
for i, p in enumerate(d, 1):
    p.get_pixmap(matrix=pymupdf.Matrix(1.8, 1.8)).save(f"_p{i}.png")
```

按这份清单逐项查：

1. **不缺失元素**：对照 md，确认每个 `#` 分节、每个列表块在 PDF 里都在（开头/结尾尤其不要丢）。
2. **折行**：英文/混排术语（`Function Calling`、`VS Code`、`RAG /`、`Token 消耗`）有没有被拆到两行中间。脚本已用"全空格不间断 + wordWrap=CJK"处理，正常不会；若出现，回到版式说明调字号。
3. **缩进**：二级列表是不是 `◦` 且有缩进；行首 `-` 有没有被字面打印出来。
4. **分页**：页数是否合理。内容被切得太碎 / 某页空大半，微调 `--font-size` 或 `--margin`。

## 依赖

`reportlab`、`Pillow`。字体用 macOS 自带 `/System/Library/Fonts/STHeiti*.ttc`。

## 格式问题的原理和坑

折行、缩进、字体、分页这些格式问题的根因和踩坑清单，见 [`reference/layout-notes.md`](reference/layout-notes.md)。调参前先读。
