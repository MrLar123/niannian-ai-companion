#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tts_emo_probe.py — 检测某个豆包音色是否真的支持 emotion 参数。

为什么需要它：豆包文档写"仅部分音色支持情感"，但**不支持的音色不会报错**——
HTTP 200、音频正常返回，只是把 emotion 静默忽略。实测 5 个 `*_uranus_bigtts`
通用音色全部如此（F0 无方向性差异）。换音色前先跑这个，否则会误判成"已生效"。

判据（同一句话、同参数，只改 emotion）：
  ① 时长差 —— 情绪会改韵律，通常伴随 ±5% 以上的时长变化
  ② 中位基频方向 —— happy 应高于 neutral，sad 应低于 neutral
  只要 F0 跨情绪差 < 2% 且时长差 < 2%，即判"静默忽略"。

用法：
  python tools/tts_emo_probe.py                              # 测当前默认音色
  python tools/tts_emo_probe.py --speaker zh_female_vv_uranus_bigtts
  python tools/tts_emo_probe.py --speaker a b c --text "随便说句话"

Key：VOLC_SPEECH_API_KEY（环境变量 -> HKCU -> HKLM）。纯标准库。
"""
import argparse
import base64
import json
import os
import sys
import uuid
import urllib.request

URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
RESOURCE = "seed-tts-2.0"
SR = 24000
EMOS = [None, "happy", "sad", "angry"]


def _reg(name):
    try:
        import winreg
    except ImportError:
        return None
    for root, path in [(winreg.HKEY_CURRENT_USER, r"Environment"),
                       (winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]:
        try:
            with winreg.OpenKey(root, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except Exception:
            pass
    return None


def get_key():
    return os.environ.get("VOLC_SPEECH_API_KEY") or _reg("VOLC_SPEECH_API_KEY") or ""


def synth(key, speaker, text, emo=None, scale=4, timeout=45):
    ap = {"format": "pcm", "sample_rate": SR, "speech_rate": 0}
    if emo:
        ap["emotion"] = emo
        ap["emotion_scale"] = scale
    body = json.dumps({"user": {"uid": "probe"}, "req_params": {
        "text": text, "speaker": speaker, "audio_params": ap}}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Api-Key": key,
        "X-Api-Resource-Id": RESOURCE,
        "X-Api-Request-Id": str(uuid.uuid4())})
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                j = json.loads(line)
            except Exception:
                continue
            d = j.get("data")
            if isinstance(d, str):
                try:
                    buf.extend(base64.b64decode(d))
                except Exception:
                    pass
    return bytes(buf)


def f0_median(pcm, use_numpy=None):
    """自相关估基频，返回 (中位 F0, 有效帧数)。

    有 numpy 走 24k 全采样（准确）；没有则降采样到 12k（快但分辨率低，仅作方向参考）。
    降采样到 8k 会因 lag 量化把 182Hz 和 200Hz 判成同一档，实测已踩过。
    """
    n = len(pcm) // 2
    if n < SR // 2:
        return None, 0
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        fl, hop = SR // 40, SR // 80          # 25ms / 12.5ms
        lo, hi = SR // 400, SR // 70          # 60-400Hz
        vals = []
        for i in range(0, len(a) - fl, hop):
            f = a[i:i + fl]
            if float(np.sqrt((f * f).mean())) < 0.01:
                continue
            f = f - f.mean()
            ac = np.correlate(f, f, "full")[fl - 1:]
            if ac[0] <= 0:
                continue
            seg = ac[lo:hi]
            if not len(seg):
                continue
            k = int(np.argmax(seg)) + lo
            if ac[k] / ac[0] < 0.25:
                continue
            if 1 <= k < len(ac) - 1:          # 抛物线插值，突破整数 lag 量化
                d = ac[k - 1] - 2 * ac[k] + ac[k + 1]
                off = 0.5 * (ac[k - 1] - ac[k + 1]) / d if d else 0.0
                k = k + max(-0.5, min(0.5, off))
            vals.append(SR / k)
        if len(vals) < 10:
            return None, len(vals)
        vals.sort()
        return vals[len(vals) // 2], len(vals)

    # 纯标准库兜底：12k + 整数 lag（精度约 ±3Hz @200Hz）
    x = [0.0] * (n // 2)
    for i in range(len(x)):
        v1 = int.from_bytes(pcm[i * 4:i * 4 + 2], "little", signed=True)
        v2 = int.from_bytes(pcm[i * 4 + 2:i * 4 + 4], "little", signed=True)
        x[i] = (v1 + v2) / 2.0 / 32768.0
    sr = SR // 2
    fl, hop = 300, 150
    lo, hi = sr // 400, sr // 70
    vals = []
    for s in range(0, len(x) - fl, hop):
        f = x[s:s + fl]
        e = sum(v * v for v in f) / fl
        if e < 1e-4:
            continue
        m = sum(f) / fl
        f = [v - m for v in f]
        den = sum(v * v for v in f)
        if den <= 0:
            continue
        best, bestv = 0, 0.0
        for lag in range(lo, hi):
            acc = 0.0
            for i in range(fl - lag):
                acc += f[i] * f[i + lag]
            if acc > bestv:
                bestv, best = acc, lag
        if best and bestv / den > 0.25:
            vals.append(sr / best)
    if len(vals) < 10:
        return None, len(vals)
    vals.sort()
    return vals[len(vals) // 2], len(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", nargs="+", default=["zh_female_gaolengyujie_uranus_bigtts"])
    ap.add_argument("--text", default="你怎么现在才回来，我等了好久了。")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    key = get_key()
    if not key:
        print("ERROR: 未找到 VOLC_SPEECH_API_KEY")
        return 2

    for spk in args.speaker:
        rows = []
        for emo in EMOS:
            try:
                b = synth(key, spk, args.text, emo)
            except Exception as e:
                print("%s / %s -> ERR %r" % (spk, emo, e))
                continue
            if not b:
                rows.append((emo, 0.0, None, 0))
                continue
            f0, nf = f0_median(b)
            rows.append((emo, len(b) / 2.0 / SR, f0, nf))

        if not rows or all(r[1] == 0 for r in rows):
            print("%-42s 无音频（音色与该资源不匹配，或未开通）" % spk)
            continue

        print("### %s" % spk)
        for emo, dur, f0, nf in rows:
            print("   %-6s dur=%.2fs  F0=%s  浊音帧=%d" % (
                str(emo), dur, ("%.1fHz" % f0) if f0 else "?", nf))

        d = {r[0]: r for r in rows}
        if not all(k in d and d[k][2] for k in (None, "happy", "sad")):
            print("   判定: 采样不足（有情绪帧数太少），请换一句更长的话再测")
            continue

        n_f0 = d[None][2]
        h_f0, h_dur = d["happy"][2], d["happy"][1]
        s_f0, s_dur = d["sad"][2], d["sad"][1]
        # 方向性判据：生效时 happy 应高于 neutral、sad 应低于 happy 且更慢。
        # 只看"跨度"会被噪声骗——实测通用音色跨度 9.6% 却方向全反。
        c1 = s_f0 < h_f0 * 0.97
        c2 = s_dur > h_dur * 1.03
        c3 = h_f0 > n_f0 * 1.02
        score = sum([c1, c2, c3])
        print("   happy=%.1fHz/%.2fs  sad=%.1fHz/%.2fs  neutral=%.1fHz" % (
            h_f0, h_dur, s_f0, s_dur, n_f0))
        print("   方向判据: sad<happy %s | sad更长 %s | happy>neutral %s  (成立 %d/3)" % (
            "Y" if c1 else "N", "Y" if c2 else "N", "Y" if c3 else "N", score))
        print("   结论: %s" % (
            "**疑似支持情绪**（需人耳复核，别只看数字）" if score >= 2
            else "**静默忽略**——该音色不支持 emotion，TTS 侧只能调语速"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
