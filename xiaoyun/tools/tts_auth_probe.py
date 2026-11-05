#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Doubao voice auth matrix probe.

Goal: settle which key works on which openspeech endpoint.

Matrix:
  key   = normal ARK key (ARK_API_KEY)  /  Agent-Plan key (ARK_PLAN_API_KEY)
  ep    = /api/v3/tts/unidirectional          (official doc, no /plan)
          /api/v3/plan/tts/unidirectional     (the one our proxy uses today)
          /api/v3/tts/create                  (seed-audio-1.0 audio generation)

Usage:
  python tts_auth_probe.py                # run full matrix
  python tts_auth_probe.py --only tts     # only tts/unidirectional combos
  python tts_auth_probe.py --json         # machine readable

Never prints full keys. Pure stdlib.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

# 凭证只从环境变量读，绝不写进源码（本脚本会 push 到公开 GitHub）
NORMAL_KEY = os.environ.get("ARK_API_KEY") or ""
PLAN_KEY = os.environ.get("ARK_PLAN_API_KEY") or os.environ.get("VOLC_SPEECH_API_KEY") or ""
if not NORMAL_KEY or not PLAN_KEY:
    sys.exit("[凭证缺失] 请设置 ARK_API_KEY 与 ARK_PLAN_API_KEY（或 VOLC_SPEECH_API_KEY）；Key 不写入源码")

BASE = "https://openspeech.bytedance.com"
EP_TTS_DOC = BASE + "/api/v3/tts/unidirectional"
EP_TTS_PLAN = BASE + "/api/v3/plan/tts/unidirectional"
EP_TTS_CREATE = BASE + "/api/v3/tts/create"

VOICE = "zh_female_gaolengyujie_uranus_bigtts"
RESOURCE = "seed-tts-2.0"

# sandbox injects http_proxy; openspeech must go direct
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)

_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)


def mask(k: str) -> str:
    if not k:
        return "(empty)"
    return "%s...%s(%d)" % (k[:12], k[-6:], len(k))


def post(url: str, headers: dict, body: dict, timeout: int = 45):
    """Return dict: status, raw_len, logid, code, message, audio_len, err, body_head."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    t0 = time.time()
    out = {"status": None, "ms": 0, "logid": "", "code": None, "message": "",
           "audio_len": 0, "raw_len": 0, "err": "", "body_head": ""}
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            out["status"] = resp.status
            out["logid"] = resp.headers.get("X-Tt-Logid") or resp.headers.get("x-tt-logid") or ""
            raw = resp.read()
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["logid"] = e.headers.get("X-Tt-Logid") or e.headers.get("x-tt-logid") or ""
        try:
            raw = e.read()
        except Exception:
            raw = b""
        out["err"] = "HTTP %s" % e.code
    except Exception as e:  # noqa: BLE001
        out["ms"] = int((time.time() - t0) * 1000)
        out["err"] = "%s: %s" % (type(e).__name__, e)
        return out
    out["ms"] = int((time.time() - t0) * 1000)
    out["raw_len"] = len(raw)
    out["body_head"] = raw[:400].decode("utf-8", "replace")

    # streamed TTS: one JSON object per line, audio chunks in .data (base64)
    audio = 0
    codes, msgs = [], []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if "code" in j:
            codes.append(j.get("code"))
        if "message" in j:
            msgs.append(str(j.get("message"))[:80])
        d = j.get("data")
        if isinstance(d, str) and d and d != "".join([]):
            try:
                audio += len(base64.b64decode(d))
            except Exception:
                pass
    if codes:
        out["code"] = codes[0]
        out["message"] = (msgs[0] if msgs else "")
    elif raw:
        # non-streamed JSON (tts/create style)
        try:
            j = json.loads(raw.decode("utf-8", "replace"))
            out["code"] = j.get("code")
            out["message"] = str(j.get("message") or "")[:80]
            a = j.get("audio")
            if isinstance(a, str) and a:
                try:
                    audio = len(base64.b64decode(a))
                except Exception:
                    audio = -1
        except Exception:
            pass
    out["audio_len"] = audio
    return out


def case_tts(key_name: str, key: str, url: str, text: str):
    body = {"req_params": {
        "text": text,
        "speaker": VOICE,
        "audio_params": {"format": "mp3", "sample_rate": 24000},
    }}
    headers = {
        "X-Api-Key": key,
        "X-Api-Resource-Id": RESOURCE,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    r = post(url, headers, body)
    r.update({"case": "tts", "key": key_name, "url": url.replace(BASE, "")})
    return r


def case_create(key_name: str, key: str, url: str, text: str):
    body = {
        "model": "seed-audio-1.0",
        "text_prompt": text,
        "audio_config": {"format": "mp3", "sample_rate": 24000},
    }
    headers = {"X-Api-Key": key, "X-Api-Request-Id": str(uuid.uuid4())}
    r = post(url, headers, body, timeout=120)
    r.update({"case": "create", "key": key_name, "url": url.replace(BASE, "")})
    return r


def run_variants(text: str):
    """Same endpoint, different auth header shapes. Rules out 'wrong header' theory."""
    body = {"req_params": {
        "text": text,
        "speaker": VOICE,
        "audio_params": {"format": "mp3", "sample_rate": 24000},
    }}
    rid = str(uuid.uuid4())
    variants = [
        ("X-Api-Key(normal)", {"X-Api-Key": NORMAL_KEY, "X-Api-Resource-Id": RESOURCE,
                               "X-Api-Request-Id": rid}),
        ("Bearer normal", {"Authorization": "Bearer " + NORMAL_KEY,
                           "X-Api-Resource-Id": RESOURCE, "X-Api-Request-Id": rid}),
        ("Bearer; normal", {"Authorization": "Bearer; " + NORMAL_KEY,
                            "X-Api-Resource-Id": RESOURCE, "X-Api-Request-Id": rid}),
        ("no-auth", {"X-Api-Resource-Id": RESOURCE, "X-Api-Request-Id": rid}),
    ]
    rows = []
    print("== header variants on %s" % EP_TTS_DOC.replace(BASE, ""))
    for name, hdr in variants:
        r = post(EP_TTS_DOC, hdr, body)
        r.update({"case": "variant", "key": name, "url": EP_TTS_DOC.replace(BASE, "")})
        rows.append(r)
        print("  %-18s http=%s code=%s audio=%dB %s"
              % (name, r["status"], r["code"], r["audio_len"],
                 (r["message"] or r["err"])[:90]))
        if r["audio_len"] <= 0 and r["body_head"]:
            print("      head=%s" % r["body_head"][:190].replace("\n", " "))
    print()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="你好，我是念念。")
    ap.add_argument("--only", default="", choices=["", "tts", "create", "variants"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    print("[keys] normal=%s  plan=%s" % (mask(NORMAL_KEY), mask(PLAN_KEY)))
    print("[env ] proxy vars cleared for direct connection\n")

    todo = []
    rows = []
    if a.only == "variants":
        rows = run_variants(a.text)
    if a.only in ("", "tts"):
        todo += [
            ("normal", NORMAL_KEY, EP_TTS_DOC, case_tts),
            ("plan", PLAN_KEY, EP_TTS_DOC, case_tts),
            ("normal", NORMAL_KEY, EP_TTS_PLAN, case_tts),
            ("plan", PLAN_KEY, EP_TTS_PLAN, case_tts),
        ]
    if a.only in ("", "create"):
        todo += [
            ("normal", NORMAL_KEY, EP_TTS_CREATE, case_create),
            ("plan", PLAN_KEY, EP_TTS_CREATE, case_create),
        ]

    for key_name, key, url, fn in todo:
        r = fn(key_name, key, url, a.text)
        rows.append(r)
        print("--- %-6s %-38s" % (key_name, r["url"]))
        print("    http=%s ms=%s code=%s audio=%dB logid=%s"
              % (r["status"], r["ms"], r["code"], r["audio_len"], r["logid"][:24]))
        if r["message"]:
            print("    message=%s" % r["message"])
        if r["err"]:
            print("    err=%s" % r["err"])
        if r["audio_len"] <= 0 and r["body_head"]:
            print("    head=%s" % r["body_head"][:240].replace("\n", " "))
        print()

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))

    ok = [r for r in rows if r["audio_len"] > 0]
    print("=" * 64)
    print("可用组合：%d / %d" % (len(ok), len(rows)))
    for r in ok:
        print("  [OK] %s key + %s  (%d bytes, %dms)" % (r["key"], r["url"], r["audio_len"], r["ms"]))
    if not ok:
        print("  无可用组合。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
