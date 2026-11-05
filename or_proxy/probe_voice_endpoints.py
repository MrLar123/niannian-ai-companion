#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Probe every doubao-voice endpoint: existence + which key family works.

Three variants per path:
  mirror  -> /api/v3/xxx becomes /api/v3/plan/xxx   (Agent Plan Key)
  plan    -> as documented, Agent Plan Key
  normal  -> as documented, standard ARK key

Verdict rules (measured 2026-11-05):
  404 / HTML error page      -> endpoint does not exist
  401 code 45000010 Invalid  -> endpoint exists, key family is wrong
  400 code 45000000 app key  -> endpoint exists, no key header seen
  anything else (400/200)    -> endpoint exists AND auth passed (body rejected)
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_voice_probe_result.json")

# 凭证只从环境变量读，绝不写进源码（本脚本会 push 到公开 GitHub）
NORMAL_KEY = os.environ.get("ARK_API_KEY") or ""
PLAN_KEY = os.environ.get("ARK_PLAN_API_KEY") or os.environ.get("VOLC_SPEECH_API_KEY") or ""
if not NORMAL_KEY or not PLAN_KEY:
    sys.exit("[凭证缺失] 请设置 ARK_API_KEY 与 ARK_PLAN_API_KEY（或 VOLC_SPEECH_API_KEY）；Key 不写入源码")
HOST = "https://openspeech.bytedance.com"

# (path, category, resource_id)
EPS = [
    ("/api/v3/tts/create", "音频生成", "seed-audio-1.0"),
    ("/api/v3/tts/unidirectional", "单向流式TTS(HTTP)", "seed-tts-2.0"),
    ("/api/v3/tts/unidirectional/sse", "单向流式TTS(SSE)", "seed-tts-2.0"),
    ("/api/v3/tts/unidirectional/stream", "单向流式TTS(WS)", "seed-tts-2.0"),
    ("/api/v3/tts/bidirection", "双向流式TTS(WS)", "seed-tts-2.0"),
    ("/api/v3/tts/voice_clone", "声音复刻", "volc.megatts.voiceclone"),
    ("/api/v3/tts/get_voice", "音色查询", ""),
    ("/api/v3/tts/upgrade_voice", "音色升级", "volc.megatts.voiceclone"),
    ("/api/v3/tts/voice_design", "音色设计", "volc.speech.voicedesign"),
    ("/api/v1/tts", "同步合成(旧版v1)", ""),
    ("/api/v3/sauc/bigmodel", "流式ASR", "volc.bigasr.sauc.duration"),
    ("/api/v3/sauc/bigmodel_async", "双向流式ASR", "volc.bigasr.sauc.duration"),
    ("/api/v3/sauc/bigmodel_nostream", "单向流式ASR", "volc.seedasr.sauc.duration"),
    ("/api/v3/auc/bigmodel/submit", "录音文件识别-提交", "volc.bigasr.auc"),
    ("/api/v3/auc/bigmodel/query", "录音文件识别-查询", "volc.bigasr.auc"),
    ("/api/v3/auc/bigmodel/recognize/flash", "录音文件识别-极速", "volc.bigasr.auc_turbo"),
    ("/api/v3/auc/lark/submit", "会议纪要-提交", "volc.bigasr.auc"),
    ("/api/v3/auc/lark/query", "会议纪要-查询", "volc.bigasr.auc"),
    ("/api/v2/asr", "流式ASR(旧版v2)", ""),
    ("/api/v1/auc/submit", "录音文件(旧版v1)-提交", ""),
    ("/api/v1/auc/query", "录音文件(旧版v1)-查询", ""),
    ("/api/v3/duplex/realtime/dialogue", "端到端Seeduplex", "volc.speech.dialog"),
    ("/api/v3/podcast/sse", "语音播客(SSE)", "volc.speech.podcast"),
    ("/api/v3/sami/podcasttts", "语音播客(WS)", "volc.speech.podcast"),
    ("/api/v4/ast/v2/translate", "同声传译", "volc.speech.ast"),
    ("/api/v3/machine_translation/matx_translate", "机器翻译", "volc.speech.mt"),
]
# 对照：一个肯定不存在的路径，用于确认 404 形态
CONTROL = ("/api/v3/tts/__probe_not_exist__", "对照-不存在路径", "")

for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def mirror(path: str) -> str:
    if path.startswith("/api/v3/"):
        return "/api/v3/plan/" + path[len("/api/v3/"):]
    if path.startswith("/api/v4/"):
        return "/api/v4/plan/" + path[len("/api/v4/"):]
    return path.replace("/api/v1/", "/api/v1/plan/", 1)


def probe(path: str, key: str, resource: str) -> dict:
    url = HOST + path
    body = json.dumps({}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-Api-Key", key)
    if resource:
        req.add_header("X-Api-Resource-Id", resource)
    req.add_header("X-Api-Request-Id", str(uuid.uuid4()))
    out = {"path": path, "http": None, "code": None, "msg": "", "ms": 0}
    t0 = time.time()
    try:
        with _OPENER.open(req, timeout=25) as resp:
            raw = resp.read()
            out["http"] = resp.status
    except urllib.error.HTTPError as exc:
        out["http"] = exc.code
        try:
            raw = exc.read()
        except Exception:                                    # noqa: BLE001
            raw = b""
    except Exception as exc:                                 # noqa: BLE001
        out["ms"] = int((time.time() - t0) * 1000)
        out["msg"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    out["ms"] = int((time.time() - t0) * 1000)
    txt = raw.decode("utf-8", "replace").strip()
    try:
        j = json.loads(txt)
        hdr = j.get("header") or {}
        out["code"] = j.get("code", hdr.get("code"))
        out["msg"] = str(j.get("message") or hdr.get("message") or "")[:110]
    except Exception:                                        # noqa: BLE001
        out["msg"] = txt[:110].replace("\n", " ")
    return out


def verdict(r: dict) -> str:
    http, code, msg = r.get("http"), r.get("code"), (r.get("msg") or "")
    if http == 404:
        return "不存在"
    if code == 45000010 or "Invalid X-Api-Key" in msg:
        return "存在·Key体系不对"
    if code == 45000000 or "app key not found" in msg:
        return "存在·无Key头"
    if http == 200 and (code in (0, None)):
        return "★可用"
    if code is not None or http in (200, 400, 403, 500):
        return "存在·鉴权已过"
    return "未知"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="substring filter on path")
    ap.add_argument("--variants", default="mirror,plan,normal")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    variants = [v.strip() for v in a.variants.split(",") if v.strip()]

    eps = list(EPS)
    if a.only:
        eps = [e for e in eps if a.only in e[0]]
    print("[keys] normal=%s plan=%s" % (NORMAL_KEY[:14], PLAN_KEY[:14]))
    print("[control] 先测一个不存在的路径，确认 404 形态\n")
    c = probe(CONTROL[0], PLAN_KEY, "")
    print("  %-40s http=%s code=%s msg=%s" % (CONTROL[0], c["http"], c["code"], c["msg"][:60]))
    print()

    results = []
    for path, cat, res in eps:
        row = {"path": path, "category": cat, "resource": res, "variants": {}}
        print("== %s  (%s)" % (cat, path))
        for v in variants:
            p = mirror(path) if v == "mirror" else path
            key = PLAN_KEY if v in ("mirror", "plan") else NORMAL_KEY
            r = probe(p, key, res)
            r["verdict"] = verdict(r)
            row["variants"][v] = r
            print("   %-6s %-52s http=%-4s code=%-9s %s"
                  % (v, p, r["http"], r["code"], r["verdict"]))
            if r["msg"] and r["verdict"] not in ("不存在",):
                print("          msg=%s" % r["msg"][:95])
            time.sleep(0.25)
        results.append(row)
        print()

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    print("=" * 78)
    print("%-30s %-16s %-16s %-16s" % ("能力", "plan镜像", "plan直连", "普通key"))
    for row in results:
        cells = []
        for v in ("mirror", "plan", "normal"):
            r = row["variants"].get(v)
            cells.append(r["verdict"] if r else "-")
        print("%-30s %-16s %-16s %-16s" % (row["category"][:28], cells[0], cells[1], cells[2]))
    print("\nwritten: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
