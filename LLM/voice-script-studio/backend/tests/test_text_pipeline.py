"""文本管道单测：Markdown 清洗 + 分句。

重点验证「不能被念出来的内容」全部剥离，段落语义保留，情绪标签透传。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline import splitter, text_clean  # noqa: E402


class TestCleanScript(unittest.TestCase):

    def test_frontmatter_and_fence_stripped(self):
        """front-matter 与代码围栏必须整体删除。"""
        raw = "---\ntitle: 不该被念\ndate: 2026\n---\n\n# 标题\n\n正文一。\n\n```python\nprint('代码不该被念')\n```\n\n正文二。"
        out = text_clean.clean_script(raw)
        self.assertNotIn("不该被念", out)
        self.assertNotIn("print", out)
        self.assertIn("正文一。", out)
        self.assertIn("正文二。", out)

    def test_markdown_inline_syntax(self):
        """标题#、粗斜体、链接、图片剥离；链接文字保留。"""
        raw = ("## 大标题\n\n"
               "这**加粗**和*斜体*都要保留文字。\n\n"
               "参考[官方文档](https://fish.audio)的链接。\n\n"
               "![配图](https://x.com/a.png)\n\n"
               "- 第一条\n- 第二条\n")
        out = text_clean.clean_script(raw)
        self.assertNotIn("##", out)
        self.assertNotIn("**", out)
        self.assertNotIn("https://", out)
        self.assertNotIn("![", out)
        self.assertNotIn("- ", out)
        self.assertIn("加粗", out)
        self.assertIn("斜体", out)
        self.assertIn("官方文档", out)
        self.assertIn("第一条", out)
        self.assertIn("第二条", out)

    def test_table_to_text(self):
        """表格：分隔行删除，单元格文字保留。"""
        raw = "| 引擎 | 采样率 |\n|---|---|\n| Fish | 44100 |"
        out = text_clean.clean_script(raw)
        self.assertNotIn("|", out)
        self.assertIn("Fish", out)
        self.assertIn("44100", out)

    def test_emotion_tags_preserved(self):
        """英文情绪标签 [pause]/[excited] 必须保留（Fish 引擎透传）。"""
        raw = "大家好。[pause] 今天我们讲 [excited] 三件大事。"
        out = text_clean.clean_script(raw)
        self.assertIn("[pause]", out)
        self.assertIn("[excited]", out)

    def test_chinese_stage_cue_stripped(self):
        """中文舞台提示 [掌声] 剔除，但英文标签不受影响。"""
        raw = "谢谢大家。[掌声] 让我们继续。[pause] 下一个话题。"
        out = text_clean.clean_script(raw)
        self.assertNotIn("[掌声]", out)
        self.assertIn("[pause]", out)

    def test_paragraphs_preserved(self):
        """空行分段语义保留（分句器依赖）。"""
        raw = "第一段内容。\n\n第二段内容。\n\n\n\n第三段。"
        out = text_clean.clean_script(raw)
        self.assertEqual(out, "第一段内容。\n\n第二段内容。\n\n第三段。")


class TestSplitter(unittest.TestCase):

    def test_basic_split_by_punct(self):
        """按终结标点聚簇到目标区间。"""
        text = "第一句话。第二句话稍微长一点点。第三句。第四句收尾。"
        segs = splitter.split_into_segments(text, max_chars=20, min_chars=10)
        self.assertTrue(len(segs) >= 2)
        joined = "".join(s["text"] for s in segs)
        self.assertEqual(joined.replace("，", ""), text.replace("，", ""))

    def test_para_break_flagged(self):
        """第二段首段标记 para_break=True。"""
        text = "这是第一段，内容完整一些。\n\n这是第二段，同样完整。"
        segs = splitter.split_into_segments(text, max_chars=80, min_chars=40)
        flags = [s["para_break"] for s in segs]
        self.assertTrue(flags[-1] or len(segs) == 1)  # 第二段存在则其首段标记

    def test_oversize_hard_split(self):
        """无标点超长文本硬切不丢字。"""
        text = "无标点" * 50
        segs = splitter.split_into_segments(text, max_chars=80)
        joined = "".join(s["text"] for s in segs)
        self.assertEqual(joined, text)
        for s in segs:
            self.assertLessEqual(len(s["text"]), 100)

    def test_seq_continuous(self):
        """段序号从 1 起连续递增。"""
        text = "第一句话说完。第二句话说完。第三句话说完。第四句话说完。"
        segs = splitter.split_into_segments(text, max_chars=20, min_chars=5)
        self.assertGreater(len(segs), 1)
        self.assertEqual([s["seq"] for s in segs], list(range(1, len(segs) + 1)))


if __name__ == "__main__":
    unittest.main()
