# -*- coding: utf-8 -*-
"""BrainClient — 对话大脑客户端。

对接 or_proxy（OpenAI 兼容 /chat/completions）。原版 ollama_chat/brain_chat
的过程式实现重组为本类；流式与非流式合一，超时/回退在此层处理。
"""
import json
import time

import requests


class BrainClient:
    """对话大脑 HTTP 客户端。线程安全（requests.Session 连接池）。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=8)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self.online = False

    # ------------------------------------------------------------------
    def health(self, timeout=3):
        """探活 or_proxy 的 /health（非 OpenAI 端点）。"""
        base = self.cfg.brain_base.rsplit("/v1", 1)[0]
        try:
            r = self._session.get(base + "/health", timeout=timeout)
            self.online = (r.status_code == 200)
            return self.online
        except Exception:
            self.online = False
            return False

    # ------------------------------------------------------------------
    def chat(self, messages, max_tokens=None, temperature=None,
             stream=False, timeout=90):
        """一轮对话。messages = [{"role":..., "content":...}, ...]。

        返回 (text, seq_hint)——本层不管理 seq，历史由 HistoryStore 负责。
        stream=True 时返回迭代器（SSE 已解析为增量文本）。
        """
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
        }
        headers = {
            "Authorization": "Bearer " + self.cfg.brain_key,
            "Content-Type": "application/json",
        }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
            return self._stream(payload, headers, timeout)
        r = self._session.post(
            self.cfg.brain_base + "/chat/completions",
            json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message", {})
        text = msg.get("content")
        if not text and msg.get("reasoning"):
            # 推理模型把正文放 reasoning 字段的兜底（原 _normalize_content 行为）
            text = msg["reasoning"]
        return text or ""

    # ------------------------------------------------------------------
    def _stream(self, payload, headers, timeout):
        resp = self._session.post(
            self.cfg.brain_base + "/chat/completions",
            json=payload, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            body = raw[5:].strip()
            if body == "[DONE]":
                break
            try:
                frame = json.loads(body)
            except ValueError:
                continue
            delta = ((frame.get("choices") or [{}])[0]
                     .get("delta", {}).get("content"))
            if delta:
                yield delta

    # ------------------------------------------------------------------
    def warmup(self):
        """8 token 最小请求，核对 model 字段防止静默回退（原 /api/chat-model 探活）。"""
        try:
            payload = {
                "model": self.cfg.model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 8,
            }
            r = self._session.post(
                self.cfg.brain_base + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + self.cfg.brain_key},
                timeout=20)
            used = r.json().get("model", "")
            return used == self.cfg.model, used
        except Exception:
            return False, ""
