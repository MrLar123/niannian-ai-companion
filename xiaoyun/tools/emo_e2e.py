"""emo_e2e.py — 情绪旁路的端到端验收（走真实后端代码 + 真实代理，不起服务）。

与 tools/emo_probe.py 的分工：
  · emo_probe.py  只测「判据本身」：直连方舟，验证 prompt 版本与命中率基线；
  · 本脚本测「接线」：_kick_emotion → 旁路线程 → or_proxy → EMO_STATE，
    量的是**真实链路**的标签、延迟、降级情况和 req 时序。

什么时候必须跑：
  · 改了 JUDGE_SYS / EMO_MODEL / or_proxy 的模型池；
  · 升级后端（换代理端口、换 Key）；
  · 上线前验收。

用法：
    cd D:/LunaAI/project-nn/xiaoyun
    python tools/emo_e2e.py                 # 12 条
    python tools/emo_e2e.py --n 20
    python tools/emo_e2e.py --msg "我升职了！"

前置：or_proxy 在 8787（或配置里的 base）跑着。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("XIAOYUN_EMBED_BACKEND", "http")

import xiaoyun_server as S   # noqa: E402

# (他说的话, 可接受标签集合；None = 这句本就该平淡)
CASES = [
    ("我升职了！", {"happy"}),
    ("你真好看。", {"shy", "happy"}),
    ("你前女友今天联系我了。", {"jealous", "annoyed"}),
    ("烦死了，别念了。", {"annoyed"}),
    ("晚安，早点睡。", {"soft", "sad"}),
    ("我好像感冒了。", {"soft", "sad"}),
    ("好累啊，加班到现在。", {"tired", "soft"}),
    ("你猜我给你带了什么？", {"curious", "happy"}),
    ("我可能要出差一个月。", {"sad", "soft"}),
    ("你是不是又在偷偷看别人。", {"jealous", "annoyed"}),
    ("今天天气怎么样？", None),
    ("帮我写一段 Python 脚本。", None),
]


def health_ok():
    import requests
    try:
        r = requests.get(S.ROOT_URL + "/health", timeout=3,
                         proxies={"http": None, "https": None})
        return r.status_code == 200, (r.text or "")[:120]
    except Exception as e:                                   # noqa: BLE001
        return False, repr(e)


def run_one(msg, timeout):
    t0 = time.time()
    req = S._kick_emotion(msg)
    rule = S.EMO_STATE["label"]
    while time.time() - t0 < timeout:
        if S.EMO_STATE["req"] >= req and S.EMO_STATE["src"] == "llm":
            break
        time.sleep(0.01)
    else:
        pass
    dt = (time.time() - t0) * 1000
    st = dict(S.EMO_STATE)
    return {"msg": msg, "label": st["label"], "src": st["src"], "rule": rule,
            "intensity": st["intensity"], "ms": st["ms"] or int(dt),
            "stale": st["req"] != req}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--msg", default="", help="只测这一句")
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()

    ok, detail = health_ok()
    if not ok:
        print("代理不可用：%s\n%s" % (S.ROOT_URL, detail))
        return 2
    print("代理 OK：%s   判定模型：%s\n" % (S.ROOT_URL, S.EMO_MODEL))

    cases = [(args.msg, None)] if args.msg else CASES[:args.n]
    rows, fails = [], []
    for msg, expect in cases:
        r = run_one(msg, args.timeout)
        hit = None if expect is None else (r["label"] in expect)
        if expect is not None and not hit:
            fails.append((msg, r["label"], expect))
        if expect is None and r["label"]:
            fails.append((msg, r["label"], "应平淡"))
        rows.append(r)
        print("%-22s -> %-8s %-5s %5dms  规则层=%s"
              % (msg[:22], str(r["label"]), r["src"], r["ms"], r["rule"]))
        time.sleep(0.2)

    judged = [r for r in rows if r["ms"] > 0]
    lat = sorted(r["ms"] for r in judged)
    p90 = lat[int(len(lat) * 0.9) - 1] if lat else 0
    llm = sum(1 for r in rows if r["src"] == "llm")
    hit = sum(1 for (m, e), r in zip(cases, rows)
              if e is not None and r["label"] in e)
    n_exp = sum(1 for _, e in cases if e is not None)

    print("\n走通 LLM 判定：%d/%d   命中：%d/%d = %s   p90：%dms"
          % (llm, len(rows), hit, n_exp,
             ("%.0f%%" % (100.0 * hit / n_exp)) if n_exp else "-", p90))
    if fails:
        print("偏差：")
        for m, got, exp in fails:
            print("  %-22s 判成 %-8s 期望 %s" % (m[:22], got, exp))
    # 判定全走规则层 = 接线断了（模型不可用/端口错/Key 失效），必须当失败看
    if n_exp and llm == 0:
        print("\nFAIL：一次 LLM 判定都没成功，表情全靠规则层兜底——查代理与 EMO_MODEL")
        return 1
    if p90 > 1200:
        print("\nFAIL：p90 %dms 超过 1200ms 闸门（表情会明显落后于语音）" % p90)
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
