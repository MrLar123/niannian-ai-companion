# -*- coding: utf-8 -*-
"""ReplyPipeline — 对话发布管线（原 _brain_turn/_publish_reply 的 class 化）。

职责：人设注入 → 大脑调用 → 舞台指令清洗 → 历史入库 → seq 广播。
清洗（_strip_stage_text）只在此唯一出口做，各端点不得重复实现。
"""
import re

_STAGE_PARENS = re.compile(
    r"（[^）()<>]{0,40}）|\([^()<>]{0,40}\)|【[^【】<>]{0,40}】"
    r"|\[[^\[\]<>]{0,40}\]|<[^<>]{0,40}>|\*[^*\n]{0,40}\*")

STAGE_HINT = (
    "\n\n[输出格式要求] 只输出要念出来的自然口语。禁止一切括号/星号"
    "包裹的动作、神态、心理描写（如（歪头）（笑））；情绪只能靠语气词"
    "和文字本身传达。")


def strip_stage_text(text):
    """剥舞台指令；整条全是动作时返回原文（宁念不哑）。"""
    if not text:
        return text
    cleaned = _STAGE_PARENS.sub("", text)
    if not cleaned.strip():
        return text
    return cleaned


class ReplyPipeline:
    """一轮对话的完整处理链。线程安全（依赖组件各自持锁）。"""

    def __init__(self, cfg, brain, tts, history, voice_session):
        self.cfg = cfg
        self.brain = brain
        self.tts = tts
        self.history = history
        self.voice = voice_session

    # ------------------------------------------------------------------
    def build_messages(self, user_text):
        preset_text = self.cfg.preset.get("preset", "")
        msgs = [{"role": "system", "content": preset_text + STAGE_HINT}]
        msgs.extend(self.history.messages())
        msgs.append({"role": "user", "content": user_text})
        return msgs

    def turn(self, user_text):
        """处理一轮用户输入，返回 {"text","seq","emotion"}。"""
        self.history.append_user(user_text)
        messages = self.build_messages(user_text)
        self.voice.set_model()
        try:
            raw = self.brain.chat(messages)
        except Exception as exc:
            self.voice.finish_reply(0)
            return {"text": "", "seq": 0, "error": str(exc)}
        text = strip_stage_text(raw)
        seq = self.history.append_reply(text)
        self.voice.finish_reply(seq)
        emotion = self.tts.detect_emotion(text)
        return {"text": text, "seq": seq, "emotion": emotion}

    # ------------------------------------------------------------------
    def proactively_speak(self, text):
        """主动开口（情境引擎等）：清洗后直接发布，不入 user 轮次。"""
        cleaned = strip_stage_text(text)
        seq = self.history.append_reply(cleaned)
        return {"text": cleaned, "seq": seq}
