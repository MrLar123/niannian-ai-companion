#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tts_prosody_probe.py — 验证 seed-tts-2.0 到底有哪些「能演情绪」的旋钮。

背景：一期方案假设「只有 speech_rate，没有 pitch」。2026-11-05 重读官方文档
发现 unidirectional 端点根本没有 `emotion` 字段，但有三个**后处理/韵律**字段
一直是空的：
    speech_rate   [-50, 100]   100 = 2.0 倍速
    loudness_rate [-50, 100]   100 = 2.0 倍音量      ← 方案没用过
    post_process.pitch [-12, 12] 半音，负=低沉        ← 方案以为没有
另有 enable_subtitle = 字级时间戳（口型对齐的硬需求）。

本工具用「方向性判据」逐项确认是否真生效（沿用 tts_emo_probe 的方法论：
不看幅度看方向，单次采样不可信）：
    pitch +  → 中位 F0 升高；pitch −  → 降低
    speech_rate + → 时长变短
    loudness_rate + → RMS 变高

用法：
  python tools/tts_prosody_probe.py                 # 全项
  python tools/tts_prosody_probe.py --only pitch    # 单项
  python tools/tts_prosody_probe.py --times 3       # 每项重复取中位
纯标准库。Key: ARK_PLAN_API_KEY / VOLC_SPEECH_API_KEY。
"""
import argparse
import base64
import json
import math
import os
import ssl
import struct
import sys
import time
import urllib.request
import uuid

BASE = "https://openspeech.bytedance.com"
EP = BASE + "/api/v3/plan/tts/unidirectional"
VOICE = "zh_female_gaolengyujie_uranus_bigtts"
RESOURCE = "seed-tts-2.0"
TEXT = "今天过得还不错，你呢？我等你很久了。"

for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)

_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def plan_key():
    # 凭证只从环境变量读，绝不写进源码（本脚本会 push 到公开 GitHub）
    for n in ("ARK_PLAN_API_KEY", "VOLC_SPEECH_API_KEY"):
        if os.environ.get(n):
            return os.environ[n]
    sys.exit("[凭证缺失] 请设置 ARK_PLAN_API_KEY 或 VOLC_SPEECH_API_KEY；Key 不写入源码")


def synth(extra_ap=None, extra_body=None, text=TEXT, fmt="pcm", sr=24000):
    """返回 (pcm_bytes, events) —— events 保留原始 NDJSON 便于看字幕结构。"""
    ap = {"format": fmt, "sample_rate": sr}
    ap.update(extra_ap or {})
    rp = {"text": text, "speaker": VOICE, "audio_params": ap}
    if extra_body:
        rp.update(extra_body)
    body = {"user": {"uid": "emo_probe"}, "req_params": rp}
    req = urllib.request.Request(
        EP, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", plan_key())
    req.add_header("X-Api-Resource-Id", RESOURCE)
    req.add_header("X-Api-Request-Id", str(uuid.uuid4()))
    t0 = time.time()
    with _OPENER.open(req, timeout=180) as resp:
        raw = resp.read()
    dt = time.time() - t0
    buf, events = bytearray(), []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        events.append(j)
        d = j.get("data")
        if isinstance(d, str) and d:
            try:
                buf += base64.b64decode(d)
            except Exception:
                pass
    return bytes(buf), events, dt


# ---- 音频分析（PCM s16le 单声道）------------------------------------------
def rms(pcm):
    if not pcm:
        return 0.0
    n = len(pcm) // 2
    if not n:
        return 0.0
    s = struct.unpack("<%dh" % n, pcm[:n * 2])
    return math.sqrt(sum(v * v for v in s) / float(n))


def median_f0(pcm, sr=24000, frame=1024, hop=512, fmin=70, fmax=400):
    """自相关 + 抛物线插值，返回有声帧的中位基频。"""
    n = len(pcm) // 2
    if n < frame * 2:
        return 0.0
    s = struct.unpack("<%dh" % n, pcm[:n * 2])
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for st in range(0, n - frame, hop):
        fr = s[st:st + frame]
        e = sum(v * v for v in fr)
        if e < 1e6:                                  # 静音/气声跳过
            continue
        best, bestlag = -1e18, 0
        for lag in range(lo, min(hi, frame - 2)):
            c = 0
            for i in range(0, frame - lag, 2):       # 2 步抽样，够用且快
                c += fr[i] * fr[i + lag]
            if c > best:
                best, bestlag = c, lag
        if not bestlag or best <= 0:
            continue
        # 抛物线插值（用相邻 lag 相关系数）
        def corr(lg):
            cc = 0
            for i in range(0, frame - lg, 2):
                cc += fr[i] * fr[i + lg]
            return cc
        c0, c1, c2 = corr(bestlag - 1), best, corr(bestlag + 1)
        den = (c0 - 2 * c1 + c2) or 1
        delta = 0.5 * (c0 - c2) / den
        out.append(sr / (bestlag + delta))
    if not out:
        return 0.0
    out.sort()
    return out[len(out) // 2]


def dur(pcm, sr=24000):
    return (len(pcm) // 2) / float(sr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    choices=["", "pitch", "rate", "loud", "subtitle"])
    ap.add_argument("--times", type=int, default=1)
    ap.add_argument("--no-f0", action="store_true",
                    help="跳过基频分析（F0 是最慢的一步，测语速/音量时不需要）")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("音色=%s 文本=%r times=%d\n" % (VOICE, TEXT, args.times))

    def measure(tag, extra_ap=None, extra_body=None, want_f0=True):
        rows = []
        for _ in range(args.times):
            pcm, ev, dt = synth(extra_ap, extra_body)
            codes = [e.get("code") for e in ev if "code" in e]
            if pcm:
                rows.append((median_f0(pcm) if want_f0 else 0.0,
                             rms(pcm), dur(pcm), dt))
            else:
                print("  %-22s 无音频 code=%s msg=%s" % (
                    tag, codes[:1], (ev[0].get("message") if ev else "")[:60]))
                return None
        f0s = sorted(r[0] for r in rows)
        rs = sorted(r[1] for r in rows)
        ds = sorted(r[2] for r in rows)
        f0, rm, du = f0s[len(f0s) // 2], rs[len(rs) // 2], ds[len(ds) // 2]
        # 单次采样不可信：把每次的值都打出来，抖动大于差值时结论无效
        spread = ""
        if len(rows) > 1:
            spread = "  [F0 %.0f~%.0f  时长%.2f~%.2f]" % (
                f0s[0], f0s[-1], ds[0], ds[-1])
        print("  %-22s F0=%6.1fHz  RMS=%7.0f  时长=%5.2fs  请求=%4.0fms%s" % (
            tag, f0, rm, du, rows[0][3] * 1000, spread))
        return {"f0": f0, "rms": rm, "dur": du,
                "f0_all": f0s, "dur_all": ds}

    want_f0 = not args.no_f0
    base = measure("baseline", want_f0=want_f0)

    if args.only in ("", "pitch"):
        print("\n[1] post_process.pitch（半音，-12..12）")
        hi = measure("pitch=+6", extra_body={"post_process": {"pitch": 6}},
                     want_f0=want_f0)
        lo = measure("pitch=-6", extra_body={"post_process": {"pitch": -6}},
                     want_f0=want_f0)
        if base and hi and lo:
            ok = hi["f0"] > base["f0"] > lo["f0"]
            print("  方向判据 F0: -6 < base < +6 → %s  (%.0f < %.0f < %.0f)" % (
                "生效" if ok else "**不生效**", lo["f0"], base["f0"], hi["f0"]))

    if args.only in ("", "rate"):
        print("\n[2] speech_rate（-50..100，100=2 倍速）")
        hi = measure("speech_rate=60", extra_ap={"speech_rate": 60}, want_f0=want_f0)
        lo = measure("speech_rate=-30", extra_ap={"speech_rate": -30}, want_f0=want_f0)
        if base and hi and lo:
            ok = hi["dur"] < base["dur"] < lo["dur"]
            print("  方向判据 时长: 快 < base < 慢 → %s  (%.2f < %.2f < %.2f)" % (
                "生效" if ok else "**不生效**", hi["dur"], base["dur"], lo["dur"]))

    if args.only in ("", "loud"):
        print("\n[3] loudness_rate（-50..100，100=2 倍音量）")
        hi = measure("loudness_rate=40", extra_ap={"loudness_rate": 40}, want_f0=want_f0)
        lo = measure("loudness_rate=-40", extra_ap={"loudness_rate": -40}, want_f0=want_f0)
        if base and hi and lo:
            ok = hi["rms"] > base["rms"] > lo["rms"]
            print("  方向判据 RMS: 小 < base < 大 → %s  (%.0f < %.0f < %.0f)" % (
                "生效" if ok else "**不生效**", lo["rms"], base["rms"], hi["rms"]))

    if args.only in ("", "subtitle"):
        print("\n[4] enable_subtitle（字级时间戳）")
        pcm, ev, dt = synth(extra_ap={"enable_subtitle": True})
        found = [e for e in ev if any(k in json.dumps(e, ensure_ascii=False)
                                      for k in ("word", "subtitle", "start_time",
                                                "end_time", "words"))]
        print("  事件数=%d 音频=%dB" % (len(ev), len(pcm)))
        if found:
            s = json.dumps(found[0], ensure_ascii=False)
            print("  首条含时间戳事件: %s" % s[:600])
        else:
            print("  未发现字级时间戳字段；事件键样本: %s" % (
                json.dumps([sorted(e.keys()) for e in ev[:3]], ensure_ascii=False)[:400]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
