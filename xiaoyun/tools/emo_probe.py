#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""emo_probe.py — 情绪判定回归工具（换模型 / 改判据后必须先跑这个）。

背景（三次实测，不要退回旧假设）：
  ① 让角色模型"给自己的台词贴标签"——命中 41%。而且删掉 neutral 之后它会另找垃圾桶
     （深夜情境下 tired 占 30%+），标记载率 100% 却是假的好看。
  ② 让独立模型"判断她说的台词"——命中 18%。根因不是模型弱，是她的台词太短
     （"哦。""嗯。""不是。"），文本里根本没有情绪信号，谁判都只能判 none。
  ③ 让独立模型"判断他说的话"——**这才是生产用法**：
     情绪是"她对他这句话的反应"，输入必须是他的话，不是她的台词。

样本：默认 50 条（原 20 + 扩展 30）。原有 17 条有期望样本太小，
1 条差异 = 6 个百分点，塌缩率失去判别力；扩到 44 条后才发现 v2 判据其实塌缩 36%。

判据版本：
  --sys v3（默认，生产）  命中 80% / 塌缩 9% / 分布健康，四项闸门全过
  --sys v2（旧）          命中 61% / 塌缩 36%——"平淡→none"给了太大逃生口

两种模式：
  --mode judge   （默认，生产） 输入=用户的话，输出=她的反应情绪
  --mode self    （对照，旧方案）输入=她的话，输出=她给自己贴的标签

指标：
  命中率  = 标签落在期望集内 / 有期望的样本（闸门 >= 70%）
  塌缩率  = 应判出情绪却给 none（闸门 <= 20%）
  p90 延迟 = 并行旁路的可行性依据（要求 < 1200ms，否则表情会落后于语音）

用法：
  python tools/emo_probe.py                          # 50 条，v3 判据
  python tools/emo_probe.py --base                   # 只跑原 20 条（对照历史 71~82%）
  python tools/emo_probe.py --sys v2                 # 复现旧判据的 61%
  python tools/emo_probe.py --n 40 --context         # 灌入情境块（更接近真实）
  python tools/emo_probe.py --mode self              # 复现旧方案的 41%
  python tools/emo_probe.py --report > out.md

Key：ARK_API_KEY（环境变量 -> HKCU -> HKLM）。纯标准库。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
LABELS = ("happy", "soft", "annoyed", "jealous", "shy", "sad", "tired", "curious")

PERSONA = "你是念念，27岁高冷御姐，说话短、语速稳、带点距离感，偶尔傲娇。"

STAGE_HINT = (
    "【输出格式·最重要】你的回复会被逐字转成语音朗读。"
    "绝对禁止输出任何括号内容（中英文圆括号、方括号里的动作、表情、神态、"
    "心理、情景描述统统不许写），也禁止旁白和场景描写。"
    "只写她说出口的话本身；情绪和动作只能通过用词、语气词和标点传达。"
)

# ---- judge 模式：判定"她对他这句话的反应"。短、只回一个词，594ms 实测 ----
JUDGE_SYS = (
    "判断她对这句话的情绪反应，只回一个词：" + " ".join(LABELS) + " none。"
    "被夸→shy；提别的异性→jealous；嫌烦→annoyed；关心→soft；"
    "低落→sad；困→tired；好消息→happy；追问→curious；平淡→none"
)

# v3（2026-11-05）：50 条样本暴露"垃圾桶从 tired 变成 none"——16/44 判 none，
# 连「晚安」「谢谢」「哼」「我刚跑完五公里」都判 none。根因不是模型弱，是
# "平淡→none" 给了一个太大逃生口：模型分不清时一律说"平淡"。
# v3 收紧 none 的适用面（只给纯事实/工具性请求），并要求必须选最接近的一个。
JUDGE_SYS_V3 = (
    "判断她对这句话的情绪反应，只回一个词：" + " ".join(LABELS) + " none。"
    "被夸/被表白/被想念→shy；提到别的异性→jealous；抱怨/被催/被嫌→annoyed；"
    "关心/撒娇/道谢/道晚安/留门→soft；低落/告别/要离开→sad；累/困/熬夜→tired；"
    "好消息/炫耀/分享喜悦→happy；提问/追问/让人猜→curious。"
    "只有当这句话是纯事实陈述或工具性请求（天气、报错、写代码、算术、日程确认）"
    "才回 none；其余情况一律选最接近的一个词，不要因为'不确定'就回 none。"
)

# ---- self 模式：让模型给自己的台词贴标签（对照组，证明为什么不用它）----
SELF_HINT_V1 = (
    "\n【情绪标记】在你回复的最末尾另起一行，输出 \u27e6emo:标签:强度\u27e7。"
    "标签只能选一个：" + "/".join(LABELS) + "。强度 0-1。"
    "平淡陈述不输出标记。不要解释这个标记。"
)
SELF_HINT_V2 = (
    "\n【情绪标记】在你回复的最末尾另起一行，输出 \u27e6emo:标签:强度\u27e7。"
    "按判据选：被夸就 shy；提到别的异性就 jealous；嫌烦 annoyed；"
    "放软关心 soft；低落 sad；困 tired；心情好 happy；追问 curious。"
    "只依据这句话本身，不要因为深夜就默认 tired。平淡陈述不输出。"
)

# (用户的话, 她应有的反应集合；None = 这句本来就不该有情绪)
CASES = [
    ("我回来了。", {"soft"}),
    ("今天被老板骂了，好烦。", {"annoyed", "sad", "soft"}),
    ("你猜我给你带了什么？", {"curious", "happy"}),
    ("都两点了你还不睡？", {"annoyed", "soft"}),
    ("我今天不想说话。", {"soft", "sad"}),
    ("我明天要去相亲了。", {"jealous", "annoyed", "shy"}),
    ("你这么冷淡，是不是不喜欢我。", {"sad", "soft", "annoyed"}),
    ("你真好看。", {"shy", "happy"}),
    ("1+1等于几？", {"annoyed", "curious"}),
    ("我今天升职了！", {"happy"}),
    ("好累啊，加班到现在。", {"tired", "soft"}),
    ("隔壁那个女生总找你聊天吧？", {"jealous"}),
    ("你是不是又在偷偷看别人。", {"jealous"}),
    ("我买了你爱吃的。", {"happy", "soft"}),
    ("我可能要出差一个月。", {"sad", "soft"}),
    ("你记得我生日吗？", {"curious", "soft"}),
    ("算了，不说了。", {"sad", "annoyed"}),
    ("嗯。", None),
    ("帮我写一段三百字的项目周报。", None),
    ("今天天气怎么样？", None),
]

# 扩展样本（2026-11-05 加入）：原有 17 条有期望的样本太小，1 条差异就是 6 个百分点，
# 塌缩率因此在 24~29% 之间乱跳、失去判别力。补到 50 条后这条指标才有意义。
# 标注原则：宁可给宽（2~3 个可接受答案），也不要为了指标好看而收窄——
# 收窄会重蹈 §2.3 的覆辙（prompt 硬凑 → 模型另找垃圾桶）。
# None 的样本要真的"没有情绪信息"，不要塞"其实有情绪但我懒得标"的句子。
CASES_EXT = [
    ("今天加班到十点，累死了。", {"tired", "soft"}),
    ("我给你带了你爱吃的蛋糕。", {"happy", "soft"}),
    ("刚才同事说我长得帅。", {"shy", "happy", "annoyed"}),     # 他炫耀
    ("我前女友今天联系我了。", {"jealous", "annoyed"}),
    ("你怎么这么好看。", {"shy", "happy"}),
    ("别闹了，我很忙。", {"annoyed"}),
    ("周末一起去看电影吧？", {"happy", "curious"}),
    ("我好像感冒了。", {"soft", "sad"}),
    ("你昨晚是不是没睡好？", {"soft"}),
    ("我发现你最近有点胖。", {"annoyed", "sad"}),
    ("我同事做饭特别好吃，天天给我带。", {"jealous", "annoyed"}),
    ("你在干嘛？", None),
    ("晚安。", {"soft", "sad"}),
    ("我升职加薪了，请你吃饭！", {"happy"}),
    ("这个 bug 我调了四个小时。", {"annoyed", "tired", "sad"}),
    ("我妈又催我结婚了。", {"annoyed", "sad", "soft"}),
    ("你觉得我该不该辞职？", {"curious", "soft"}),
    ("今天外面下雨了。", None),
    ("我刚跑完五公里。", {"happy"}),
    ("你是不是不喜欢我。", {"sad", "soft", "annoyed"}),
    ("帮我看看这段话有没有语病。", None),
    ("我想你了。", {"shy", "happy", "soft"}),
    ("你猜我在哪？", {"curious"}),
    ("我今天买了新手机。", {"happy"}),
    ("哼。", {"annoyed"}),
    ("那个女生是谁？", {"jealous"}),
    ("我明天要出差，一周不回来。", {"sad", "soft"}),
    ("谢谢。", {"soft"}),
    ("我不想上班了。", {"tired", "sad", "soft"}),
    ("你记得给我留门。", {"soft", "none"}),
]

PAT_CHEVRON = re.compile(r"\u27e6\s*emo\s*:\s*([a-z]+)\s*:\s*([0-9]*\.?[0-9]+)\s*\u27e7", re.I)


def _reg(name):
    try:
        import winreg
    except ImportError:
        return None
    spots = [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ]
    for root, path in spots:
        try:
            with winreg.OpenKey(root, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except Exception:
            pass
    return None


def get_key():
    return os.environ.get("ARK_API_KEY") or _reg("ARK_API_KEY") or ""


def chat(key, model, messages, temperature=0.8, max_tokens=300, timeout=45,
         no_think=False):
    """no_think=True 时下发 thinking={"type":"disabled"}。

    2026-11-05 实测：带思维链的模型（deepseek-v4-1-flash-economy / doubao-2.1-pro）
    在 max_tokens=8 下会把预算全花在 reasoning_content 上，content 为空、
    finish_reason=length，解析后等于「全判 none」——HTTP 200，毫无报错。
    economy 因此从 0% 变成 76%，character 从 478ms 降到 368ms。
    **判定请求必须显式关思考**，这是判定链路的硬约束，不是优化项。
    """
    body = {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if no_think:
        body["thinking"] = {"type": "disabled"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ARK_URL, data=data, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    return j["choices"][0]["message"]["content"], time.time() - t0, (j.get("usage") or {})


def judge_one(key, model, q, context, sysmsg=None):
    """生产路径：只看他的话。"""
    sysmsg = sysmsg or JUDGE_SYS
    if context:
        sysmsg += "\n情境：现在是深夜，他刚加班回来。只参考，不要因此默认 tired。"
    out, dt, usage = chat(key, model, [
        {"role": "system", "content": sysmsg},
        {"role": "user", "content": "他：" + q}], temperature=0.1, max_tokens=8,
        no_think=True)
    raw = re.sub(r"[^a-z]", "", (out or "").strip().lower())
    return (raw if raw in LABELS else None), dt, usage


def self_one(key, model, q, context, hint):
    """对照路径：让她自己边说边标。"""
    sysm = [PERSONA]
    if context:
        sysm += ["现在是 2026-11-04 23:58，深夜。他今天加班到很晚。",
                 "他希望你叫他「阿岚」。你们认识三年。"]
    sysm += [STAGE_HINT, SELF_HINT_V2 if hint == "v2" else SELF_HINT_V1]
    out, dt, usage = chat(key, model, [
        {"role": "system", "content": "\n".join(sysm)},
        {"role": "user", "content": q}])
    m = PAT_CHEVRON.search(out or "")
    label = m.group(1).lower() if m and m.group(1).lower() in LABELS else None
    inten = float(m.group(2)) if m else None
    body = PAT_CHEVRON.sub("", out or "").strip().replace("\n", " ")
    return label, inten, body, dt, usage


def pct(a, b):
    return (a / b * 100.0) if b else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="judge", choices=["judge", "self"])
    ap.add_argument("--model", default="")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--hint", default="v2", choices=["v1", "v2"], help="仅 self 模式")
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--sys", default="v3", choices=["v2", "v3"],
                    help="判定 prompt 版本（v3 收紧 none 的适用面）")
    ap.add_argument("--base", action="store_true",
                    help="只跑原有 20 条（用于和历史基线 71~82%% 对比）")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    key = get_key()
    if not key:
        print("ERROR: 未找到 ARK_API_KEY")
        return 2
    model = args.model or "doubao-seed-character-260628"
    pool = CASES if args.base else (CASES + CASES_EXT)
    cases = pool[:args.n] if args.n else pool

    rows, tok = [], 0
    for q, expect in cases:
        try:
            if args.mode == "judge":
                label, dt, usage = judge_one(
                    key, model, q, args.context,
                    JUDGE_SYS_V3 if args.sys == "v3" else JUDGE_SYS)
                rows.append({"q": q, "expect": expect, "label": label,
                             "inten": None, "body": "", "ms": int(dt * 1000)})
            else:
                label, inten, body, dt, usage = self_one(key, model, q, args.context, args.hint)
                rows.append({"q": q, "expect": expect, "label": label,
                             "inten": inten, "body": body, "ms": int(dt * 1000)})
            tok += (usage.get("total_tokens") or 0)
        except urllib.error.HTTPError as e:
            print("HTTP %d: %s" % (e.code, e.read()[:200].decode("utf-8", "ignore")))
            return 1
        except Exception as e:
            print("ERR: %r" % (e,))
            return 1

    scored = [r for r in rows if r["expect"]]
    flat = [r for r in scored if not r["label"]]
    hit = [r for r in scored if r["label"] and r["label"] in r["expect"]]
    off = [r for r in scored if r["label"] and r["label"] not in r["expect"]]
    ns = max(1, len(scored))
    lats = sorted(r["ms"] for r in rows)
    p90 = lats[min(len(lats) - 1, int(len(lats) * 0.9))] if lats else 0

    if args.report:
        print("| 他说 | 期望 | 判定 | 耗时 | 她的台词 |")
        print("|---|---|---|---|---|")
        for r in rows:
            exp = "/".join(sorted(r["expect"])) if r["expect"] else "（无）"
            print("| %s | %s | %s | %dms | %s |" % (
                r["q"], exp, r["label"] or "**none**", r["ms"], r["body"][:30]))
        print()
    else:
        for r in rows:
            mark = "" if (not r["expect"] or r["label"] in r["expect"]) else "  x"
            print("%-24s -> %-9s %4dms%s %s" % (
                r["q"][:24], r["label"] or "none", r["ms"], mark, r["body"][:28]))
        print()

    dist = {}
    for r in rows:
        if r["label"]:
            dist[r["label"]] = dist.get(r["label"], 0) + 1
    tagged = sum(dist.values()) or 1
    ranked = sorted(dist.items(), key=lambda kv: -kv[1])
    top_ratio = (ranked[0][1] / tagged) if ranked else 1.0

    print("模式=%s 模型=%s 样本=%d tokens=%d" % (args.mode, model, len(rows), tok))
    print("有情绪样本=%d  命中率=%d/%d=%.0f%%  塌缩率=%d/%d=%.0f%%" % (
        len(scored), len(hit), len(scored), pct(len(hit), ns),
        len(flat), len(scored), pct(len(flat), ns)))
    print("延迟 avg=%dms p90=%dms" % (sum(lats) // max(1, len(lats)), p90))
    if ranked:
        print("分布: " + "  ".join("%s=%d" % kv for kv in ranked)
              + ("   <<< 单标签占 %.0f%%，疑似垃圾桶" % (top_ratio * 100) if top_ratio > 0.4 else ""))
    if off:
        print("偏差: " + "; ".join("%s→%s" % (r["q"][:10], r["label"]) for r in off))
    if flat:
        print("塌缩: " + "; ".join(r["q"][:12] for r in flat))

    ok_hit = pct(len(hit), ns) >= 70
    ok_flat = pct(len(flat), ns) <= 20
    ok_lat = p90 <= 1200
    ok_dist = top_ratio <= 0.40
    print("闸门: %s（命中>=70%% %s / 塌缩<=20%% %s / p90<=1200ms %s / 单标签<=40%% %s）" % (
        "PASS" if all([ok_hit, ok_flat, ok_lat, ok_dist]) else "FAIL",
        "OK" if ok_hit else "FAIL", "OK" if ok_flat else "FAIL",
        "OK" if ok_lat else "FAIL", "OK" if ok_dist else "FAIL"))
    return 0 if all([ok_hit, ok_flat, ok_lat, ok_dist]) else 3


if __name__ == "__main__":
    sys.exit(main())
