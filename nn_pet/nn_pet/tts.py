# -*- coding: utf-8 -*-
"""TTSEngine — 语音合成（豆包 volc 云端 + edge-tts 保底）。

原版 tts_volc/tts_mp3/tts_edge + 情绪调度(_detect_emotion/_compose_sps)
重组为本类。合成失败自动回退 edge，保证"她不会哑"。
"""
import base64
import os
import re

import requests

# 情绪 → 语速/音调偏移（原 _detect_emotion/_compose_sps 的映射表）
EMOTION_SHIFTS = {
    "开心":  (8, 3),
    "傲娇":  (5, 2),
    "吃醋":  (0, 2),
    "心疼":  (-6, -2),
}
_EMOTION_RE = re.compile(
    r"(哈哈|嘿嘿|太好了|开心|哼|才不是|气死了|别难过|辛苦了|抱抱)")


class TTSEngine:
    """文字 → mp3 bytes。线程安全。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=8)
        self._session.mount("https://", adapter)
        self._volc_key = self._resolve_volc_key()

    @staticmethod
    def _resolve_volc_key():
        """VOLC_SPEECH_API_KEY：环境变量 → HKCU 注册表兜底（原 _reg_env 行为）。"""
        key = os.environ.get("VOLC_SPEECH_API_KEY", "")
        if key:
            return key
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val, _ = winreg.QueryValueEx(k, "VOLC_SPEECH_API_KEY")
                return val
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def detect_emotion(self, text):
        if not text:
            return None
        for emotion in EMOTION_SHIFTS:
            if emotion in text:
                return emotion
        return None

    def synthesize(self, text, emotion=None):
        """主入口：按 cfg.tts_engine 分派，失败回退 edge。返回 mp3 bytes 或 None。"""
        if not text:
            return None
        if self.cfg.tts_engine == "volc" and self._volc_key:
            audio = self._volc(text, emotion)
            if audio:
                return audio
        return self._edge(text)

    # ------------------------------------------------------------------
    def _volc(self, text, emotion=None):
        """豆包 2.0 HTTP 单向流式：base64 分片拼 mp3。"""
        shift_rate, shift_pitch = EMOTION_SHIFTS.get(emotion or "", (0, 0))
        body = {
            "user": {"uid": "nn_pet"},
            "req_params": {
                "text": text,
                "speaker": self.cfg.volc_speaker,
                "audio_params": {
                    "format": "mp3", "sample_rate": 24000,
                    "speech_rate": max(-50, min(100,
                        self.cfg.volc_speech_rate + shift_rate)),
                },
            },
        }
        try:
            r = self._session.post(
                "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                json=body,
                headers={"X-Api-Key": self._volc_key,
                         "Resource-Id": "seed-tts-2.0"},
                timeout=30)
            r.raise_for_status()
            chunks = []
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        import json
                        frame = json.loads(line[5:])
                        b64 = (frame.get("data") or {}).get("audio") or ""
                        if b64:
                            chunks.append(base64.b64decode(b64))
                    except Exception:
                        continue
            audio = b"".join(chunks)
            return audio or None
        except Exception:
            return None

    def _edge(self, text):
        """edge-tts 保底（需联网）。"""
        try:
            import edge_tts
            import asyncio

            async def _run():
                com = edge_tts.Communicate(
                    text, self.cfg.edge_voice,
                    rate=self.cfg.edge_rate, pitch=self.cfg.edge_pitch)
                buf = bytearray()
                async for chunk in com.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                return bytes(buf)

            return asyncio.run(_run()) or None
        except Exception:
            return None
