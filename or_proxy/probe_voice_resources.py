#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Which X-Api-Resource-Id values are granted on the Agent Plan voice gateway.

Sending an empty body is enough: the gateway checks auth + resource grant BEFORE
body validation, so:
    code 45000030 "requested resource not granted"  -> not granted
    45000000 "request does not contain req_params"  -> granted (reached body parse)
    401     45000010 Invalid X-Api-Key              -> wrong key family
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

HOST = "https://openspeech.bytedance.com"
# 凭证只从环境变量读，绝不写进源码（本脚本会 push 到公开 GitHub）
PLAN_KEY = os.environ.get("ARK_PLAN_API_KEY") or os.environ.get("VOLC_SPEECH_API_KEY") or ""
if not PLAN_KEY:
    sys.exit("[凭证缺失] 请设置 ARK_PLAN_API_KEY 或 VOLC_SPEECH_API_KEY；Key 不写入源码")

# POST 型 plan 端点（WS 型另测）
ENDPOINTS = [
    "/api/v3/plan/tts/unidirectional",
    "/api/v3/plan/auc/bigmodel/submit",
    "/api/v3/plan/auc/bigmodel/query",
]

RESOURCES = [
    "seed-tts-2.0", "seed-icl-2.0", "seed-audio-1.0", "volc.megatts.voiceclone",
    "volc.bigasr.auc", "volc.bigasr.auc_turbo", "volc.seedasr.auc",
    "volc.bigasr.sauc.duration", "volc.seedasr.sauc.duration",
    "volc.speech.mt", "volc.speech.dialog", "volc.speech.ast",
    "volc.speech.podcast", "volc.speech.voicedesign",
]

for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def post(path: str, resource: str) -> dict:
    req = urllib.request.Request(HOST + path, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", PLAN_KEY)
    if resource:
        req.add_header("X-Api-Resource-Id", resource)
    req.add_header("X-Api-Request-Id", str(uuid.uuid4()))
    out = {"http": None, "code": None, "msg": ""}
    try:
        with _OPENER.open(req, timeout=20) as resp:
            raw, out["http"] = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        out["http"] = exc.code
        try:
            raw = exc.read()
        except Exception:                                    # noqa: BLE001
            raw = b""
    except Exception as exc:                                 # noqa: BLE001
        out["msg"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    txt = raw.decode("utf-8", "replace").strip()
    try:
        j = json.loads(txt)
        hdr = j.get("header") or {}
        out["code"] = j.get("code", hdr.get("code"))
        out["msg"] = str(j.get("message") or hdr.get("message") or "")[:100]
    except Exception:                                        # noqa: BLE001
        out["msg"] = txt[:100].replace("\n", " ")
    return out


def judge(r: dict) -> str:
    code, msg = r.get("code"), r.get("msg") or ""
    if code == 45000030 or "not granted" in msg:
        return "未授权"
    if code == 45000010 or "Invalid X-Api-Key" in msg:
        return "Key不对"
    if r.get("http") == 404:
        return "端点不存在"
    if code in (0, 45000000, None):
        return "已授权"
    return "其他(%s)" % code


def main() -> int:
    print("[plan key] %s\n" % PLAN_KEY[:16])
    grid = {}
    print("%-34s" % "resource_id", end="")
    for ep in ENDPOINTS:
        print("%-16s" % ep.split("/plan/")[-1][:14], end="")
    print()
    for res in RESOURCES:
        print("%-34s" % res, end="")
        grid[res] = {}
        for ep in ENDPOINTS:
            r = post(ep, res)
            v = judge(r)
            grid[res][ep.split("/plan/")[-1]] = {"verdict": v, "http": r["http"],
                                                 "code": r["code"], "msg": r["msg"]}
            mark = {"已授权": "OK", "未授权": "--", "Key不对": "KEY", "端点不存在": "404"}.get(v, "?")
            print("%-16s" % mark, end="")
            time.sleep(0.2)
        print()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_voice_resource_matrix.json"), "w", encoding="utf-8") as fh:
        json.dump(grid, fh, ensure_ascii=False, indent=2)
    print("\n说明: OK=已授权(进入参数校验)  --=resource未授权  KEY=Key体系不对  404=无此端点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
