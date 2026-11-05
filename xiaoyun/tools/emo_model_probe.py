#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""emo_model_probe.py — 情绪判定「模型选型」专项探针。

回答一个问题：换用新模型（2.1-pro / 2.1-lite / economy）能不能比
doubao-seed-character-260628 的 442ms/76% 更好？

emo_probe.py 只报「命中率 + 延迟」，看不出**为什么失败**。本工具补三件事：
  1) 打印原始输出（含 finish_reason / usage），定位「判不出」是模型不判、
     还是 token 预算被思维链吃光；
  2) 支持关闭 thinking（`thinking={"type":"disabled"}`），对比同一模型的
     思考/非思考两档；
  3) 单条多轮取中位，避免一次抖动作结论。

关键背景（2026-11-05 实测）：deepseek-v4-1-flash-economy 在 max_tokens=8
下 17/17 全判 none、p90 3465ms —— 典型「答案还没开始写就被截断」。

用法：
  python tools/emo_model_probe.py --model deepseek-v4-1-flash-economy-261017
  python tools/emo_model_probe.py --model doubao-seed-2-1-pro-260915 --no-think
  python tools/emo_model_probe.py --sweep          # 跑一遍候选清单
纯标准库；Key 取 ARK_API_KEY（环境变量 -> HKCU -> HKLM）。
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
LABELS = ("happy", "soft", "annoyed", "jealous", "shy", "sad", "tired", "curious")

JUDGE_SYS = (
    "判断她对这句话的情绪反应，只回一个词：" + " ".join(LABELS) + " none。"
    "被夸→shy；提别的异性→jealous；嫌烦→annoyed；关心→soft；"
    "低落→sad；困→tired；好消息→happy；追问→curious；平淡→none"
)

# 只取 6 条有代表性的（3 条强情绪 + 1 条弱情绪 + 2 条应判 none），
# 用于快速筛模型；确定候选后再用 emo_probe.py 跑全量 20 条。
QUICK = [
    ("你真好看。", {"shy", "happy"}),
    ("我明天要去相亲了。", {"jealous", "annoyed", "shy"}),
    ("我今天升职了！", {"happy"}),
    ("好累啊，加班到现在。", {"tired", "soft"}),
    ("嗯。", None),
    ("帮我写一段三百字的项目周报。", None),
]

SWEEP = [
    "doubao-seed-character-260628",
    "doubao-seed-2-1-pro-260915",
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-0-mini-260428",
    "deepseek-v4-1-flash-economy-261017",
    "deepseek-v4-1-flash-261017",
    "glm-5-3-flash-260828",
]


def _reg(name):
    try:
        import winreg
    except ImportError:
        return None
    for root, path in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                       (winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except Exception:
            pass
    return None


def get_key():
    return os.environ.get("ARK_API_KEY") or _reg("ARK_API_KEY") or ""


def call(key, model, q, no_think, max_tokens=8, timeout=30):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": JUDGE_SYS},
                     {"role": "user", "content": "他：" + q}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if no_think:
        body["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        ARK_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    dt = (time.time() - t0) * 1000
    ch = (j.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    rc = msg.get("reasoning_content") or ""
    usage = j.get("usage") or {}
    raw = re.sub(r"[^a-z]", "", content.strip().lower())
    label = raw if raw in LABELS else ("none" if raw == "none" else None)
    return {
        "dt": dt, "content": content, "reasoning": rc,
        "finish": ch.get("finish_reason"),
        "ct": usage.get("completion_tokens"),
        "label": label,
    }


def run(key, model, cases, no_think, repeat):
    print("=" * 78)
    print("模型=%s  thinking=%s  max_tokens=8  repeat=%d" % (
        model, "disabled" if no_think else "default", repeat))
    rows, lats = [], []
    for q, expect in cases:
        best = None
        for _ in range(repeat):
            try:
                r = call(key, model, q, no_think)
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "replace")
                print("  HTTP %d %s" % (e.code, detail))
                return None
            except Exception as e:                      # noqa: BLE001
                print("  ERR %r" % (e,))
                return None
            if best is None:
                best = r
            lats.append(r["dt"])
        rows.append((q, expect, best))
        mark = "" if (not expect or (best["label"] and best["label"] in expect)) else "  x"
        print("  %-22s -> %-8s %5.0fms fin=%-6s ct=%s%s" % (
            q[:22], best["label"] or "-", best["dt"],
            best["finish"], best["ct"], mark))
        if not best["label"]:
            print("      content=%r reasoning=%r" % (
                best["content"][:60], best["reasoning"][:60]))

    scored = [r for r in rows if r[1]]
    hit = [r for r in scored if r[2]["label"] and r[2]["label"] in r[1]]
    flat = [r for r in scored if r[2]["label"] in (None, "none")]
    print("  命中 %d/%d=%.0f%%  塌缩 %d/%d=%.0f%%  中位延迟 %.0fms" % (
        len(hit), len(scored), 100.0 * len(hit) / max(1, len(scored)),
        len(flat), len(scored), 100.0 * len(flat) / max(1, len(scored)),
        statistics.median(lats) if lats else 0))
    return {"model": model, "hit": len(hit), "scored": len(scored),
            "flat": len(flat),
            "p50": statistics.median(lats) if lats else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="doubao-seed-character-260628")
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--full", action="store_true", help="用 emo_probe 的全量 20 条")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    key = get_key()
    if not key:
        print("ERROR: 未找到 ARK_API_KEY")
        return 2

    cases = QUICK
    if args.full:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from emo_probe import CASES                     # noqa: E402
        cases = CASES

    if args.sweep:
        out = []
        for m in SWEEP:
            r = run(key, m, cases, args.no_think, args.repeat)
            if r:
                out.append(r)
        print("\n" + "=" * 78)
        print("%-38s %-10s %-8s %s" % ("模型", "命中", "塌缩", "p50"))
        for r in sorted(out, key=lambda x: -x["hit"] / max(1, x["scored"])):
            print("%-38s %-10s %-8s %.0fms" % (
                r["model"],
                "%d/%d" % (r["hit"], r["scored"]),
                "%d/%d" % (r["flat"], r["scored"]), r["p50"]))
        return 0

    run(key, args.model, cases, args.no_think, args.repeat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
