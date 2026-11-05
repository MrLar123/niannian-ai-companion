"""情绪旁路的后端集成测试（不联网、不启服务，走 Flask test_client）。

跑法：
    cd D:/LunaAI/project-nn/xiaoyun
    XIAOYUN_EMBED_BACKEND=http python -m unittest tools.test_emo_backend -v

守的是"旁路"这件事本身的几条硬约束：
  ① 规则层先落地 —— 模型超时/失败时表情仍有初值，不能一片空白；
  ② req 单调 —— 连说两句时，旧判定不许覆盖新判定（否则表情会倒着演）；
  ③ 解析失败不改状态 —— 模型回 "none"/拼错/被 max_tokens 截断，都保留规则层结果；
  ④ /api/last 两条分支都带 emo —— 表情不依赖台词就绪，否则"脸比嘴早"不成立；
  ⑤ TTS 韵律真的进了请求体 —— 两个维度都实测生效，别只写在文档里。
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("XIAOYUN_EMBED_BACKEND", "http")

import xiaoyun_server as S   # noqa: E402


class _Resp(object):
    def __init__(self, content="", finish="stop", code=200):
        self.status_code = code
        self._content = content
        self._finish = finish
        self.text = "{}"

    def json(self):
        return {"choices": [{"message": {"content": self._content},
                             "finish_reason": self._finish}]}


class _Ctx(object):
    """给 tts_volc 用的伪流式响应（它要 with 语法 + iter_lines）。"""

    def __init__(self, body_sink, payload, code=500):
        body_sink.append(payload)
        self.status_code = code
        self.text = "stub"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode=True):
        return iter(())


class _Session(object):
    def __init__(self, sink):
        self.sink = sink

    def post(self, url, json=None, headers=None, stream=False, timeout=None):
        return _Ctx(self.sink, json)


class TestEmotionState(unittest.TestCase):
    def setUp(self):
        self._orig_post = S.requests.post
        S.EMO_STATE.update({"label": None, "intensity": 0.0, "src": "none",
                            "req": 0, "ts": 0.0, "ms": 0, "conf": 0.0})
        S.EMO_SEQ[0] = 0

    def tearDown(self):
        S.requests.post = self._orig_post

    def _wait(self, pred, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def test_rule_layer_lands_immediately(self):
        """规则层必须同步落地：模型还没回，表情就得有初值。"""
        def slow_post(*a, **k):
            time.sleep(0.3)          # 模拟真实判定耗时，制造"规则层先到"的窗口
            return _Resp("shy")
        S.requests.post = slow_post
        S._kick_emotion("我升职了！")
        self.assertEqual(S.EMO_STATE["label"], "happy")
        self.assertEqual(S.EMO_STATE["src"], "rule")
        self.assertGreater(S.EMO_STATE["intensity"], 0.4)
        self._wait(lambda: S.EMO_STATE["src"] == "llm")

    def test_llm_upgrades_rule(self):
        S.requests.post = lambda *a, **k: _Resp("shy")
        req = S._kick_emotion("我升职了！")
        self.assertTrue(self._wait(lambda: S.EMO_STATE["src"] == "llm"),
                        "判定结果没有写回状态")
        self.assertEqual(S.EMO_STATE["label"], "shy")
        self.assertEqual(S.EMO_STATE["req"], req)

    def test_judge_request_disables_thinking(self):
        """不带 thinking:disabled 的判定请求会让部分模型 0% 命中且不报错。"""
        seen = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            seen.update(json or {})
            return _Resp("happy")

        S.requests.post = fake_post
        S._kick_emotion("我回来了")
        self.assertTrue(self._wait(lambda: seen.get("max_tokens") == 8))
        self.assertEqual(seen.get("thinking"), {"type": "disabled"})
        self.assertEqual(seen.get("temperature"), 0.1)
        self.assertEqual(seen.get("model"), S.EMO_MODEL)
        # 输入只有他的话
        self.assertTrue(seen["messages"][1]["content"].startswith("他："))

    def test_unparseable_output_keeps_rule(self):
        for content, finish in (("hyppy", "stop"), ("", "length"),
                                ("happy 或 shy", "stop")):
            S.EMO_STATE.update({"label": None, "intensity": 0.0, "src": "none",
                                "req": 0, "ts": 0.0, "ms": 0})
            S.EMO_SEQ[0] = 0
            S.requests.post = (lambda c, f: (lambda *a, **k: _Resp(c, f)))(content, finish)
            req = S._kick_emotion("我升职了！")
            time.sleep(0.25)
            self.assertEqual(S.EMO_STATE["src"], "rule", "畸形输出 %r 不该改状态" % content)
            self.assertEqual(S.EMO_STATE["label"], "happy")
            self.assertEqual(S.EMO_STATE["req"], req)

    def test_explicit_none_overrides_rule(self):
        """LLM 明确判 none 是有效结论（v3 里 none 有适用范围），要覆盖规则层。"""
        def slow_post(*a, **k):
            time.sleep(0.25)
            return _Resp("none")
        S.requests.post = slow_post
        S._kick_emotion("好累啊，加班到现在。")     # 规则层先给 tired
        self.assertEqual(S.EMO_STATE["label"], "tired")
        self.assertTrue(self._wait(lambda: S.EMO_STATE["src"] == "llm"))
        self.assertIsNone(S.EMO_STATE["label"])
        self.assertEqual(S.EMO_STATE["intensity"], 0.0)

    def test_stale_result_dropped(self):
        """旧请求号的结果不许覆盖新结果（连说两句时表情会倒着演）。"""
        S._emo_set("happy", 0.5, "rule", 7)
        ok = S._emo_set("sad", 0.5, "llm", 6, ms=100)
        self.assertFalse(ok)
        self.assertEqual(S.EMO_STATE["label"], "happy")

    def test_http_error_keeps_rule(self):
        S.requests.post = lambda *a, **k: _Resp("happy", code=500)
        S._kick_emotion("我升职了！")
        time.sleep(0.2)
        self.assertEqual(S.EMO_STATE["label"], "happy")
        self.assertEqual(S.EMO_STATE["src"], "rule")

    def test_empty_message_no_thread(self):
        req = S._kick_emotion("")
        self.assertEqual(req, 1)
        self.assertEqual(S.EMO_STATE["src"], "none")
        self.assertIsNone(S.EMO_STATE["label"])


class TestApiLast(unittest.TestCase):
    def setUp(self):
        self.client = S.app.test_client()
        self._last = dict(S.LAST)
        self._seq = S.SEQ
        S.EMO_STATE.update({"label": "jealous", "intensity": 0.7, "src": "llm",
                            "req": 3, "ts": time.time(), "ms": 420, "conf": 0.6})

    def tearDown(self):
        S.LAST.clear()
        S.LAST.update(self._last)
        S.SEQ = self._seq

    def test_fresh_branch_has_emo(self):
        S.SEQ = 42
        S.LAST.update({"seq": 42, "reply": "哼。", "audio": None, "voice": "v",
                       "stream": False, "emo": S._emo_snapshot()})
        j = self.client.get("/api/last?since=0").get_json()
        self.assertTrue(j["fresh"])
        self.assertEqual(j["emo"]["label"], "jealous")
        self.assertAlmostEqual(j["emo"]["intensity"], 0.7, places=2)

    def test_stale_branch_still_has_emo(self):
        """没有新台词也要带 emo：rule→llm 的升级靠这条推给前端。"""
        S.SEQ = 42
        j = self.client.get("/api/last?since=42").get_json()
        self.assertFalse(j["fresh"])
        self.assertEqual(j["emo"]["label"], "jealous")
        self.assertEqual(j["emo"]["req"], 3)


class TestPublishAndProsody(unittest.TestCase):
    def setUp(self):
        self._last = dict(S.LAST)
        self._seq = S.SEQ
        S.EMO_STATE.update({"label": "shy", "intensity": 0.5, "src": "llm",
                            "req": 9, "ts": time.time(), "ms": 380, "conf": 0.6})

    def tearDown(self):
        S.LAST.clear()
        S.LAST.update(self._last)
        S.SEQ = self._seq

    def test_publish_carries_emo(self):
        payload, _ = S._publish_reply("才、才没有呢。", use_stream=True)
        self.assertEqual(payload["emo"]["label"], "shy")
        self.assertEqual(S.LAST["emo"]["label"], "shy")
        self.assertEqual(S.LAST["seq"], S.SEQ)

    def test_prosody_uses_state_then_falls_back(self):
        self.assertEqual(S._emo_prosody("随便什么"), (-6, -14))     # shy @0.5
        # 状态过期后按文本兜底
        S.EMO_STATE["ts"] = time.time() - S.EMO_TTL - 1
        r, l = S._emo_prosody("烦死了")
        inten = S.EMO.intensity_from("烦死了", "annoyed")
        self.assertEqual((r, l), S.EMO.tts_params("annoyed", inten))

    def test_prosody_none_when_no_signal(self):
        S.EMO_STATE["ts"] = time.time() - S.EMO_TTL - 1
        self.assertEqual(S._emo_prosody("今天几号？"), (0, 0))

    def test_tts_body_carries_both_dimensions(self):
        """音量维度必须真的进请求体——它才是 shy/sad/tired 唯一能听出来的差别。"""
        import tts_stream as TS
        sink = []
        orig_key, orig_sess = S._plan_key, None
        S._plan_key = lambda: "test-key"
        orig_post = TS.session
        TS.session = lambda: _Session(sink)
        try:
            S.tts_volc("我在呢。")
        finally:
            S._plan_key = orig_key
            TS.session = orig_post
        self.assertTrue(sink, "没有发出请求")
        ap = sink[0]["req_params"]["audio_params"]
        self.assertIn("loudness_rate", ap)
        self.assertEqual(ap["loudness_rate"], -14)     # shy 在 0.5 强度
        base = int(S.CFG.get("volc_speech_rate") or 0)
        self.assertEqual(ap["speech_rate"], max(-50, min(100, base - 6)))


class TestSentenceProsody(unittest.TestCase):
    def test_long_reply_softens_only_last_sentence(self):
        text = "今天好累。你回来了？先去洗澡吧。"
        segs = S.EMO.split_sentences(text)
        self.assertEqual(len(segs), 3)
        self.assertEqual(S.EMO.sentence_rate(8, 0, 3), 8)
        self.assertEqual(S.EMO.sentence_rate(8, 2, 3), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
