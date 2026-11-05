#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Scan doubao-voice doc pages -> extract every upstream endpoint + auth scheme.

Docs site is SSR: page HTML already contains the rendered body, so a plain GET
plus a regex sweep gives us the real upstream paths (no guessing).

Usage:
  python scan_voice_endpoints.py            # scan, print table, write _voice_endpoints.json
  python scan_voice_endpoints.py --no-cache # refetch
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "_voice_doc_cache")
OUT_JSON = os.path.join(HERE, "_voice_endpoints.json")
BASE = "https://docs.volcengine.com/docs/doubao-voice/"

# slug -> 截图里的产品线归属
SLUGS = [
    # —— 语音合成大模型 ——
    ("audio-generation-http", "音频生成"),
    ("http-interface-one-time-composition-non-streaming", "同步语音合成"),
    ("http-one-timecompositenon-streaming-interface-v1-not-recommended", "同步语音合成(旧版)"),
    ("unidirectional-streaming-text-to-speech-http", "单向流式TTS(HTTP)"),
    ("unidirectional-streaming-text-to-speech-websocket", "单向流式TTS(WS)"),
    ("bidirectional-streaming-text-to-speech-websocket", "双向流式TTS(WS)"),
    ("http-chunked-sse-unidirectional-streaming-v3", "单向流式v3(HTTP/SSE)"),
    ("websocket-unidirectional-streaming-v3", "单向流式v3(WS)"),
    ("websocket-bidirectional-streaming-v3", "双向流式v3(WS)"),
    ("task-submission-http-1", "长文本-任务提交"),
    ("result-query-http-1", "长文本-结果查询"),
    ("sound-replica-api-v3", "声音复刻"),
    ("sound-design-api", "音色设计"),
    ("tone-training-http", "音色训练"),
    ("tone-upgrade-http", "音色升级"),
    ("tone-query-http", "音色查询"),
    # —— 语音识别大模型 ——
    ("unidirectional-streaming-automatic-speech-recognition-websocket", "流式ASR(WS)"),
    ("large-model-streaming-automatic-speech-recognition-api-1", "流式ASR(API)"),
    ("streaming-automatic-speech-recognition-1", "流式ASR"),
    ("bidirectional-streaming-automatic-speech-recognition-websocket", "双向流式ASR(WS)"),
    ("large-model-recording-file-recognition-standard-version-api", "录音文件识别(标准版)"),
    ("large-model-recording-file-lite-recognition-api", "录音文件识别(lite)"),
    ("audio-file-recognition-lite", "录音文件识别(lite2)"),
    # —— 端到端 / 播客 / 同传 / 翻译 ——
    ("end-to-end-real-time-voice-full-duplex-version", "端到端实时语音(Seeduplex)"),
    ("podcast", "语音播客"),
    ("podcast-api-websocket-v3protocol", "语音播客(WS协议)"),
    ("simultaneous-interpretation2-0-api-access-documentation", "同声传译"),
    ("doubao-voice-minutes-api-access-documentation", "会议纪要"),
]

EP_RE = re.compile(r"(?:wss?|https?)://([a-z0-9.-]*bytedance\.com)(/[A-Za-z0-9_\-./{}:%]*)", re.I)
BARE_RE = re.compile(r"openspeech\.bytedance\.com(/[A-Za-z0-9_\-./{}:%]+)", re.I)
AUTH_KEYS = ["X-Api-Key", "X-Api-App-Id", "X-Api-Access-Key",
             "X-Api-Resource-Id", "X-Api-Request-Id", "Authorization"]
SKIP_EXT = (".png", ".jpg", ".svg", ".css", ".js", ".gif", ".ico", ".woff")


def fetch(slug: str, use_cache: bool = True) -> str:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, slug + ".html")
    if use_cache and os.path.isfile(path) and os.path.getsize(path) > 20000:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    url = BASE + slug + "?lang=zh"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=45) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as exc:                                 # noqa: BLE001
        return "__ERROR__ %s" % exc
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(html)
    return html


def clean(raw: str) -> str:
    # unescape json-escaped slashes then strip tags for auth-key text search
    return raw.replace("\\/", "/").replace("\\u002F", "/")


def extract(slug: str, category: str, html: str) -> dict:
    out = {"slug": slug, "category": category, "ok": False,
           "title": "", "endpoints": [], "auth": [], "error": ""}
    if html.startswith("__ERROR__"):
        out["error"] = html[10:]
        return out
    out["ok"] = True
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        out["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
    body = clean(html)
    eps = {}
    for host, path in EP_RE.findall(body):
        p = path.rstrip(".,);\"'")
        if p.lower().endswith(SKIP_EXT) or p in ("/", ""):
            continue
        full = "https://%s%s" % (host, p)
        if host.startswith("w"):
            pass
        eps[full] = True
    for p in BARE_RE.findall(body):
        p = p.rstrip(".,);\"'")
        if p.lower().endswith(SKIP_EXT):
            continue
        eps["https://openspeech.bytedance.com" + p] = True
    # dedupe: drop a path that is a strict prefix duplicate of another
    keys = sorted(eps)
    out["endpoints"] = keys
    txt = re.sub(r"<[^>]+>", " ", body)
    for k in AUTH_KEYS:
        if k in txt or k in body:
            out["auth"].append(k)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    rows = []
    for slug, cat in SLUGS:
        html = fetch(slug, use_cache=not a.no_cache)
        r = extract(slug, cat, html)
        rows.append(r)
        flag = "OK " if r["ok"] else "ERR"
        eps = [e.replace("https://openspeech.bytedance.com", "") for e in r["endpoints"]]
        print("[%s] %-34s %s" % (flag, cat, slug))
        if r["error"]:
            print("      error=%s" % r["error"][:120])
        for e in eps:
            print("      %s" % e)
        if r["auth"]:
            print("      auth=%s" % ",".join(r["auth"]))
        time.sleep(0.4)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print("\nwritten: %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
