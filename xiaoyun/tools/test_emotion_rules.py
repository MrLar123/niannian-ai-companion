"""emotion_rules 的单测（纯标准库，不联网、不依赖项目其它模块）。

跑法：
    cd D:/LunaAI/project-nn/xiaoyun
    python -m unittest tools.test_emotion_rules -v
或：
    python tools/test_emotion_rules.py

这里守的是三类容易悄悄坏掉的东西：
  ① 判定输出解析——模型返回的畸形格式（两个标签、拼错、句中、带标点）必须一律丢，
     绝不能猜，否则会凭空多出一个垃圾桶；
  ② 韵律映射——speech_rate 是 1:1 语义，改错换算系数就回到"调了听不出"的老坑；
  ③ 判据文本——生产用的 JUDGE_SYS 必须和回归工具 emo_probe 的 v3 完全一致，
     不一致的话 80% 的基线就是假的。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import emotion_rules as er   # noqa: E402


class TestLabels(unittest.TestCase):
    def test_labels_shape(self):
        self.assertEqual(len(er.LABELS), 8)
        self.assertEqual(set(er.LABELS), {"happy", "soft", "annoyed", "jealous",
                                          "shy", "sad", "tired", "curious"})
        # 没有 neutral：平淡用"判 none"表达，否则一定长成垃圾桶
        self.assertNotIn("neutral", er.LABELS)
        self.assertNotIn("none", er.LABELS)

    def test_tts_table_covers_all_labels(self):
        for label in er.LABELS:
            self.assertIn(label, er.TTS_TABLE)
            r, l = er.TTS_TABLE[label]
            self.assertIsInstance(r, int)
            self.assertIsInstance(l, int)
            self.assertLessEqual(abs(r), 20, "语速幅度过大，%s 会听成另一个人" % label)
            self.assertLessEqual(abs(l), 20, "音量幅度过大，%s 会忽大忽小" % label)

    def test_judge_prompt_matches_probe(self):
        """生产判据必须与回归工具口径一致，否则 80% 的基线是假的。"""
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.exists(os.path.join(tools_dir, "emo_probe.py")):
            self.skipTest("emo_probe.py 不存在")
        sys.path.insert(0, tools_dir)
        try:
            import emo_probe
        finally:
            sys.path.pop(0)
        self.assertEqual(er.JUDGE_SYS, emo_probe.JUDGE_SYS_V3)


class TestParseLabel(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(er.parse_label("happy"), "happy")
        self.assertEqual(er.parse_label("  sad\n"), "sad")
        self.assertEqual(er.parse_label("HAPPY"), "happy")

    def test_with_punctuation_and_prefix(self):
        self.assertEqual(er.parse_label("shy。"), "shy")
        self.assertEqual(er.parse_label("情绪：jealous"), "jealous")
        self.assertEqual(er.parse_label("应该是 tired 吧"), "tired")

    def test_chinese_adjacent(self):
        self.assertEqual(er.parse_label("她shy了"), "shy")

    def test_ambiguous_two_labels_dropped(self):
        self.assertIsNone(er.parse_label("happy 或 shy"))
        self.assertIsNone(er.parse_label("happy/shy"))

    def test_typo_dropped(self):
        self.assertIsNone(er.parse_label("hyppy"))
        self.assertIsNone(er.parse_label("saddness"))

    def test_none_like_output(self):
        self.assertIsNone(er.parse_label("none"))
        self.assertIsNone(er.parse_label(""))

    def test_empty_and_none(self):
        self.assertIsNone(er.parse_label(None))
        self.assertIsNone(er.parse_label("   "))

    def test_non_string(self):
        self.assertIsNone(er.parse_label(123))

    def test_english_word_containing_label(self):
        # "saddle" 里的 sad / "happyish" 里的 happy 不算命中
        self.assertIsNone(er.parse_label("saddle"))
        self.assertIsNone(er.parse_label("happyish"))

    def test_alias(self):
        self.assertIs(er.parse_judge, er.parse_label)


class TestRuleLabel(unittest.TestCase):
    def test_each_label_hit(self):
        cases = {
            "她是哪个女生？": "jealous",
            "烦死了，别念了": "annoyed",
            "今天加班到现在，好累": "tired",
            "算了，不说了": "sad",
            "我升职了！": "happy",
            "你今天真好看": "shy",
            "晚安，早点睡": "soft",
            "为什么会这样？": "curious",
        }
        for msg, want in cases.items():
            got, conf = er.rule_label(msg)
            self.assertEqual(got, want, "规则层判错：%r -> %r（期望 %r）" % (msg, got, want))
            self.assertGreater(conf, 0.3)
            self.assertLessEqual(conf, 0.75)

    def test_no_hit(self):
        for msg in ("今天几号？", "帮我写一段 Python", "嗯", ""):
            label, conf = er.rule_label(msg)
            self.assertIsNone(label, "不该命中：%r" % msg)
            self.assertEqual(conf, 0.0)

    def test_none_input(self):
        self.assertEqual(er.rule_label(None), (None, 0.0))

    def test_priority_specific_first(self):
        # 同时命中"吃醋"和"关心"时，吃醋优先：不能被"早点睡"抢走
        label, _ = er.rule_label("你前女友找你干嘛？早点睡")
        self.assertEqual(label, "jealous")


class TestIntensity(unittest.TestCase):
    def test_default(self):
        self.assertEqual(er.intensity_from("今天过得还行吧", "soft"), 0.5)

    def test_no_label_is_zero(self):
        self.assertEqual(er.intensity_from("随便什么", None), 0.0)

    def test_bang_raises(self):
        self.assertGreater(er.intensity_from("太好了！", "happy"),
                           er.intensity_from("太好了", "happy"))

    def test_repeat_punctuation(self):
        self.assertGreater(er.intensity_from("真的吗？？", "curious"),
                           er.intensity_from("真的吗？", "curious"))

    def test_short_message_lower(self):
        self.assertLess(er.intensity_from("哦。", "soft"), 0.5)

    def test_question_only_for_some_labels(self):
        q = er.intensity_from("你说呢？", "curious")
        plain = er.intensity_from("你说呢", "curious")
        self.assertGreater(q, plain)
        # 对 soft 不加：平淡的关心不该因为问号变激烈
        self.assertEqual(er.intensity_from("吃了吗？", "soft"),
                         er.intensity_from("吃了吗", "soft"))

    def test_clamp(self):
        hot = er.intensity_from("太好了！！！" * 5, "happy")
        self.assertLessEqual(hot, 0.9)
        cold = er.intensity_from("嗯", "soft")
        self.assertGreaterEqual(cold, 0.15)

    def test_empty_text(self):
        self.assertEqual(er.intensity_from("", "happy"), 0.5)


class TestTTSParams(unittest.TestCase):
    def test_table_at_half(self):
        for label, (r, l) in er.TTS_TABLE.items():
            self.assertEqual(er.tts_params(label, 0.5), (r, l))

    def test_zero_intensity_keeps_sixty_percent(self):
        r, l = er.tts_params("sad", 0.0)
        self.assertEqual((r, l), (int(round(-10 * 0.6)), int(round(-8 * 0.6))))

    def test_full_intensity(self):
        r, l = er.tts_params("shy", 1.0)
        self.assertEqual((r, l), (int(round(-6 * 1.4)), int(round(-14 * 1.4))))

    def test_unknown_label_is_neutral(self):
        self.assertEqual(er.tts_params(None, 0.5), (0, 0))
        self.assertEqual(er.tts_params("angry", 0.5), (0, 0))

    def test_bad_intensity_falls_back(self):
        self.assertEqual(er.tts_params("happy", "x"), er.TTS_TABLE["happy"])

    def test_distinguishes_shy_sad_tired(self):
        """三者的"音量—语速"组合必须不同，否则听感上分不开（§2.6/§2.8）。"""
        combo = {lb: er.tts_params(lb, 0.5) for lb in ("shy", "sad", "tired")}
        self.assertEqual(len(set(combo.values())), 3)
        # shy：不怎么慢但明显小声；tired：又慢又轻
        self.assertGreater(combo["shy"][0], combo["sad"][0])
        self.assertLess(combo["shy"][1], combo["sad"][1])
        self.assertLess(combo["tired"][0], combo["sad"][0])


class TestSentences(unittest.TestCase):
    def test_single(self):
        self.assertEqual(er.split_sentences("哦。"), ["哦。"])

    def test_multi(self):
        got = er.split_sentences("今天好累。你回来了？先去洗澡吧。")
        self.assertEqual(got, ["今天好累。", "你回来了？", "先去洗澡吧。"])

    def test_no_punctuation(self):
        self.assertEqual(er.split_sentences("今天不错"), ["今天不错"])

    def test_empty(self):
        self.assertEqual(er.split_sentences(""), [])
        self.assertEqual(er.split_sentences(None), [])

    def test_whitespace_only(self):
        self.assertEqual(er.split_sentences("   "), [])

    def test_newline_splits(self):
        self.assertEqual(len(er.split_sentences("第一句\n第二句")), 2)

    def test_sentence_rate_single_untouched(self):
        self.assertEqual(er.sentence_rate(8, 0, 1), 8)

    def test_sentence_rate_last_softens(self):
        self.assertEqual(er.sentence_rate(8, 0, 3), 8)
        self.assertEqual(er.sentence_rate(8, 2, 3), 5)

    def test_sentence_rate_clamped(self):
        self.assertEqual(er.sentence_rate(-50, 1, 2), -50)


class TestJudgeMessages(unittest.TestCase):
    def test_input_is_his_words_only(self):
        msgs = er.judge_messages("你今天真好看")
        self.assertEqual(len(msgs), 2)
        self.assertTrue(msgs[1]["content"].startswith("他："))
        self.assertEqual(msgs[1]["content"], "他：你今天真好看")

    def test_context_has_reverse_instruction(self):
        msgs = er.judge_messages("我回来了", "现在是深夜，他刚加班回来。")
        self.assertIn("不要因此默认 tired", msgs[0]["content"])

    def test_system_prompt_is_v3(self):
        sysmsg = er.judge_messages("x")[0]["content"]
        self.assertIn("纯事实陈述或工具性请求", sysmsg)
        self.assertIn("不要因为'不确定'就回 none", sysmsg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
