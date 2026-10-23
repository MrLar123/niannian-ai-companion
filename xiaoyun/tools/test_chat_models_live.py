# -*- coding: utf-8 -*-
"""接线测试：/api/chat-model 的清单是否跟着 or_proxy 实况走。

为什么要有这个测试：这一处曾经把「魔搭 Qwen3.5-27B / GLM-5.2」写死在菜单里，
而魔搭整档随后被 or_proxy 禁用 —— 菜单照样显示、选中照样 200，只是代理静默
回退到别家。手抄的清单没有任何机制保证它和代理一致，所以这里直接断言
"清单 = 顺序表 ∩ 代理实况"，并且把三种坏情况都造出来打一遍。

隔离措施（绝不碰正在运行的实例）：
- XIAOYUN_PORT=18090，app.run 只在 __main__ 下触发；
- 记忆/情境/感知全关，不碰 memory.db / 屏幕；
- 只读访问真实 or_proxy（8787）拿实况；探测 POST 一律打桩，不烧额度。
运行：Miniconda3\\python.exe tools/test_chat_models_live.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

os.environ["XIAOYUN_MEMORY"] = "0"
os.environ["XIAOYUN_CONTEXT"] = "0"
os.environ["XIAOYUN_PERCEPTION"] = "0"
os.environ["XIAOYUN_PORT"] = "18090"

import xiaoyun_server as xs   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


client = xs.app.test_client()

# ---- 1. 真实代理：清单必须与实况一致 ----
print("\n== 实况清单（真读 or_proxy 8787）==")
opts, ctx = xs._chat_model_state(force=True)
for _ in range(2):                       # /models 兜底路径偶尔慢，重试两次
    if ctx["live"]:
        break
    opts, ctx = xs._chat_model_state(force=True)
ids = [o["id"] for o in opts]
LIVE = bool(ctx["live"])
print("  live=%s  active=%s  disabled=%s" % (LIVE, ctx["active"], sorted(ctx["disabled"])))
print("  options=%s" % ids)
if not LIVE:
    # 代理没起时"清单 = 实况"无从验证，但降级路径必须在下面单独成立。
    # 直接判失败会把"代理停着"误报成"代码坏了"。
    print("  [SKIP] or_proxy 8787 没在运行 —— 实况断言跳过，只验降级路径")
else:
    check("代理可达", LIVE)
    check("auto 固定第一", ids[:1] == ["auto"], str(ids[:1]))
    check("无被禁用上游的模型", not [i for i in ids
                                  if ctx["models"].get(i) in ctx["disabled"]])
    check("魔搭 Qwen/GLM 已不在菜单", not [i for i in ids
                                     if i.startswith("Qwen/") or i.startswith("ZhipuAI/")])
    check("清单非空", len(ids) > 2, "%d 项" % len(ids))
    check("每项都有说明", all(o["note"] for o in opts))
    # 有真实采样的模型，note 里应出现"实测 x.xxs"
    sampled = [o for o in opts if o["latency"]]
    check("延迟来自真实 EMA", bool(sampled) and all("实测" in o["note"] for o in sampled),
          "%d 项带采样" % len(sampled))

# ---- 2. 当前选择已被弃用（模拟历史遗留的 Qwen）----
# 只在代理在线时成立：离线时清单退回静态表，Qwen 反而"在列"，不会触发 stale。
if LIVE:
    print("\n== 当前选择不可用（stale）==")
    xs.CFG["model"] = "Qwen/Qwen3.5-27B"
    xs._CHAT_OPT_CACHE["val"] = None
    r = client.get("/api/chat-model")
    j = r.get_json()
    print("  current=%s stale=%s label=%s" % (j["current"], j["stale"], j["label"]))
    print("  warnings=%s" % j["warnings"])
    print("  options[0:3]=%s" % [o["id"] for o in j["options"][:3]])
    check("HTTP 200", r.status_code == 200)
    check("标记 stale", j["stale"] is True)
    check("仍出现在选项里", j["options"][1]["id"] == "Qwen/Qwen3.5-27B",
          str([o["id"] for o in j["options"][:3]]))
    check("有醒目警告", any("已不可用" in w for w in j["warnings"]))
    check("警告指向 auto", any("自动" in w for w in j["warnings"]))
    check("上游链路字段齐", {u["name"] for u in j["upstreams"]} >= {"ark", "openrouter"})
else:
    print("\n== 当前选择不可用（stale）==  [SKIP] 需代理在线")

# ---- 3. 切到不可用模型：必须拒绝而不是"切成功" ----
# 同样需要代理在线：离线时走的是 503「无法确认」分支，语义不同，别混在一起来判。
if LIVE:
    print("\n== POST 拒绝不可用模型 ==")
    r = client.post("/api/chat-model", json={"model": "Qwen/Qwen3.5-27B"})
    j = r.get_json()
    print("  -> %d %s" % (r.status_code, j.get("error")))
    check("拒绝 404/409", r.status_code in (404, 409), str(r.status_code))
    check("错误说清原因", "不可" in (j.get("error") or ""))
    check("CFG 未被改写", xs.CFG["model"] == "Qwen/Qwen3.5-27B")

    r = client.post("/api/chat-model", json={"model": "nvidia/does-not-exist:free"})
    check("不存在的 id 也拒绝", r.status_code == 404, str(r.status_code))
else:
    print("\n== POST 拒绝不可用模型 ==  [SKIP] 需代理在线")

# ---- 4. 大脑没起：降级但明说 ----
print("\n== 代理不可达（降级）==")
real = xs._brain_live
xs._brain_live = lambda: (None, {})
xs._CHAT_OPT_CACHE["val"] = None
j = client.get("/api/chat-model").get_json()
print("  live=%s n_options=%d warnings=%s"
      % (j["live"], len(j["options"]), j["warnings"]))
check("live=False", j["live"] is False)
pref = [m for m, _n in xs.CHAT_MODEL_PREFERRED]
check("退回顺序表（全部在列）", set(pref) <= {o["id"] for o in j["options"]},
      "%d 项" % len(j["options"]))
check("明说可能过期", any("过期" in w or "连不上" in w for w in j["warnings"]))
xs._probe_chat_model = lambda mid, timeout=45: (False, "", 0.0, "connection refused")
r = client.post("/api/chat-model", json={"model": "deepseek-v4-1-flash-261017"})
print("  具体模型 -> %d %s" % (r.status_code, r.get_json().get("error")))
check("降级时拒绝具体模型", r.status_code == 503, str(r.status_code))
xs._brain_live = real

# ---- 5. 正常的切换路径（探活打桩，不烧额度）----
print("\n== 正常切换（探活打桩）==")
xs._CHAT_OPT_CACHE["val"] = None
xs._probe_chat_model = lambda mid, timeout=45: (True, mid if mid != "auto"
                                                else "doubao-seed-character-260628", 0.42, "")
saved = []
xs._save_chat_model = lambda mid: saved.append(mid)
xs.evlog = lambda *a, **k: None
r = client.post("/api/chat-model", json={"model": "auto"})
j = r.get_json()
print("  -> %s verified=%s used=%s" % (r.status_code, j.get("verified"), j.get("used")))
check("切换成功", r.status_code == 200 and j["ok"])
check("已持久化 model=auto", saved == ["auto"], str(saved))
check("CFG 已更新", xs.CFG["model"] == "auto")

print("\n%s（%d 项失败）" % ("全部通过" if not FAILS else "有失败: " + ", ".join(FAILS),
                          len(FAILS)))
sys.exit(1 if FAILS else 0)
