#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Deep probe: (1) auc submit/query on the plan gateway, (2) websocket handshakes.

Websocket endpoints answer HTTP POST with 405 "only supports GET method", so the
only way to know if they are granted is a real Upgrade handshake: 101 = ok,
401 = wrong key, 403 = resource not granted, 404 = no such endpoint.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

HOST = "openspeech.bytedance.com"
# 凭证只从环境变量读，绝不写进源码（本脚本会 push 到公开 GitHub）
PLAN_KEY = os.environ.get("ARK_PLAN_API_KEY") or os.environ.get("VOLC_SPEECH_API_KEY") or ""
if not PLAN_KEY:
    sys.exit("[凭证缺失] 请设置 ARK_PLAN_API_KEY 或 VOLC_SPEECH_API_KEY；Key 不写入源码")

for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)
_CTX = ssl.create_default_context()
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      urllib.request.HTTPSHandler(context=_CTX))


def post_json(path: str, body: dict, resource: str, req_id: str, seq: str = "-1"):
    req = urllib.request.Request("https://" + HOST + path,
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", PLAN_KEY)
    req.add_header("X-Api-Resource-Id", resource)
    req.add_header("X-Api-Request-Id", req_id)
    if seq:
        req.add_header("X-Api-Sequence", seq)
    out = {"http": None, "headers": {}, "code": None, "msg": "", "body": ""}
    try:
        with _OPENER.open(req, timeout=60) as resp:
            raw, out["http"] = resp.read(), resp.status
            out["headers"] = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        out["http"] = exc.code
        out["headers"] = dict(exc.headers or {})
        try:
            raw = exc.read()
        except Exception:                                    # noqa: BLE001
            raw = b""
    except Exception as exc:                                 # noqa: BLE001
        out["msg"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    out["body"] = raw[:600].decode("utf-8", "replace")
    try:
        j = json.loads(raw.decode("utf-8", "replace"))
        hdr = j.get("header") or {}
        out["code"] = j.get("code", hdr.get("code"))
        out["msg"] = str(j.get("message") or hdr.get("message") or "")[:200]
    except Exception:                                        # noqa: BLE001
        out["msg"] = out["body"][:150]
    return out


def ws_handshake(path: str, resource: str):
    """Raw Upgrade handshake. Returns (status_line, extra_headers)."""
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        "GET %s HTTP/1.1" % path,
        "Host: %s" % HOST,
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: %s" % key,
        "Sec-WebSocket-Version: 13",
        "X-Api-Key: %s" % PLAN_KEY,
        "X-Api-Request-Id: %s" % uuid.uuid4(),
    ]
    if resource:
        lines.append("X-Api-Resource-Id: %s" % resource)
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode()
    try:
        sock = socket.create_connection((HOST, 443), timeout=15)
        sock = _CTX.wrap_socket(sock, server_hostname=HOST)
        sock.sendall(raw)
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            chunk = sock.recv(2048)
            if not chunk:
                break
            buf += chunk
        sock.close()
    except Exception as exc:                                 # noqa: BLE001
        return "ERR %s: %s" % (type(exc).__name__, exc), ""
    text = buf.decode("utf-8", "replace")
    status = text.split("\r\n", 1)[0]
    body = text.split("\r\n\r\n", 1)[1][:220] if "\r\n\r\n" in text else ""
    return status, body.replace("\r\n", " ")


def main() -> int:
    print("=" * 78)
    print("A. 录音文件识别（volc.seedasr.auc）提交 → 查询")
    print("=" * 78)
    rid = str(uuid.uuid4())
    body = {
        "user": {"uid": "or_proxy_probe"},
        "audio": {"url": "https://example.com/probe.mp3", "format": "mp3"},
        "request": {"model_name": "bigmodel", "enable_itn": True,
                    "enable_punc": True, "enable_ddc": True},
    }
    r = post_json("/api/v3/plan/auc/bigmodel/submit", body, "volc.seedasr.auc", rid)
    print("submit http=%s code=%s msg=%s" % (r["http"], r["code"], r["msg"][:120]))
    for k in ("X-Api-Status-Code", "X-Api-Message", "X-Api-Request-Id", "X-Tt-Logid"):
        if r["headers"].get(k):
            print("   %s: %s" % (k, r["headers"][k]))
    accepted = str(r["code"]) in ("0", "20000000", "None") and r["http"] == 200
    if accepted:
        time.sleep(1.5)
        q = post_json("/api/v3/plan/auc/bigmodel/query", {}, "volc.seedasr.auc", rid, seq=None)
        print("query  http=%s code=%s msg=%s" % (q["http"], q["code"], q["msg"][:160]))
        for k in ("X-Api-Status-Code", "X-Api-Message"):
            if q["headers"].get(k):
                print("   %s: %s" % (k, q["headers"][k]))
        if q["body"]:
            print("   body=%s" % q["body"][:220].replace("\n", " "))
    else:
        print("   submit 未被接受，跳过 query")

    print()
    print("=" * 78)
    print("B. WebSocket 端点握手（101=可用 / 401=Key不对 / 403=resource未授权 / 404=不存在）")
    print("=" * 78)
    ws_cases = [
        ("/api/v3/plan/sauc/bigmodel_nostream", "volc.seedasr.sauc.duration", "单向流式ASR(在用的对照组)"),
        ("/api/v3/plan/sauc/bigmodel_async", "volc.seedasr.sauc.duration", "双向流式ASR"),
        ("/api/v3/plan/tts/unidirectional/stream", "seed-tts-2.0", "单向流式TTS(WS)"),
        ("/api/v3/plan/tts/bidirection", "seed-tts-2.0", "双向流式TTS(WS)"),
        # 同传是 wss v4，resource 是固定值 volc.service_type.10053
        ("/api/v4/ast/v2/translate", "volc.service_type.10053", "同声传译(v4 wss)"),
        ("/api/v3/sami/podcasttts", "volc.speech.podcast", "语音播客(WS)"),
        ("/api/v3/duplex/realtime/dialogue", "volc.speech.dialog", "端到端Seeduplex"),
        # 以下为对照：不在 plan 白名单的路径
        ("/api/v3/plan/sauc/bigmodel", "volc.seedasr.sauc.duration", "流式ASR(v3新版)"),
        ("/api/v3/tts/unidirectional/stream", "seed-tts-2.0", "非plan对照(应401)"),
    ]
    for path, res, label in ws_cases:
        status, body = ws_handshake(path, res)
        print("%-26s %-40s" % (label, path))
        print("   %s" % status)
        if body:
            print("   %s" % body[:170])
        time.sleep(0.3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
