# -*- coding: utf-8 -*-
"""用量看板接线测试：/api/usage 的聚合口径。

为什么要用桩代理而不是真代理：真代理的记账是"跑起来以后攒的"，刚起时全是 0，
测不出边界（超额度、非方舟档没有 50 万、熔断、代理没起）。所以这里起一个只
回 /health 与 /registry 的本地桩，把各种状态一次摆好。

隔离：XIAOYUN_PORT=18097、记忆/情境/感知全关；CFG 指向桩端口，不碰 8787。
运行：Miniconda3\\python.exe tools/test_usage_api.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
for k, v in (("XIAOYUN_MEMORY", "0"), ("XIAOYUN_CONTEXT", "0"),
             ("XIAOYUN_PERCEPTION", "0"), ("XIAOYUN_PORT", "18097")):
    os.environ[k] = v

import xiaoyun_server as xs   # noqa: E402

PORT = 18096
HEALTH = {
    "requests": 42, "fallbacks": 3, "errors": 1, "uptime_sec": 3725,
    "upstream_priority": ["openrouter", "ark"],
    "disabled_upstreams": ["modelscope"],
    "quota_cooldown": {"doubao-seed-2-1-lite-260915": 900},
    "auto_routing": {"best_now": "doubao-seed-character-260628",
                     "latency": {"doubao-seed-character-260628": {"ema_s": 0.4, "samples": 2}},
                     "pool": []},
    "token_usage": {
        # 故意超过 50 万：验证 percent >= 100 的"已耗尽"分支
        "doubao-seed-2-1-lite-260915": {"prompt": 400000, "completion": 120000,
                                        "total": 520000, "calls": 300},
        "doubao-seed-character-260628": {"prompt": 1000, "completion": 500,
                                         "total": 1500, "calls": 20},
        # OpenRouter 免费层不是按 token 配额，必须给 None 而不是算百分比
        "nvidia/nemotron-3-super-120b-a12b:free": {"prompt": 90, "completion": 10,
                                                   "total": 100, "calls": 1},
    },
}
REGISTRY = {
    "object": "registry",
    "active_upstreams": ["openrouter", "ark"],
    "disabled_upstreams": ["modelscope"],
    "default_model": "auto",
    "models": {
        "ark": ["doubao-seed-2-1-lite-260915", "doubao-seed-character-260628"],
        "arkplan": [],
        "modelscope": ["Qwen/Qwen3.5-27B"],
        "openrouter": ["nvidia/nemotron-3-super-120b-a12b:free"],
    },
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                        # noqa: N802
        p = self.path.rstrip("/")
        if p == "/health":
            self._send(HEALTH)
        elif p in ("/v1/registry", "/registry"):
            self._send(REGISTRY)
        else:
            self._send({"error": {"message": "not found"}}, 404)

    def log_message(self, *a):                               # 静音
        pass


srv = HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

xs.CFG["base"] = "http://127.0.0.1:%d/v1" % PORT
xs.ROOT_URL = "http://127.0.0.1:%d" % PORT
xs.BRAIN_HEALTH_URL = "http://127.0.0.1:%d/health" % PORT
xs._CHAT_OPT_CACHE["val"] = None
client = xs.app.test_client()

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def get_usage():
    xs._CHAT_OPT_CACHE["val"] = None
    r = client.get("/api/usage")
    return r.status_code, r.get_json()


print("\n== 正常记账 ==")
code, j = get_usage()
rows = {r["id"]: r for r in j["rows"]}
print("  sum=%s" % j["sum"])
for r in j["rows"]:
    print("    %-40s total=%-7d calls=%-4d avg=%-7s pct=%-8s bar=%s"
          % (r["id"], r["total"], r["calls"], r["avg"], r["quota_pct"], r["bar"]))
check("HTTP 200", code == 200)
check("live/supported", j["live"] and j["supported"])
check("行数", len(j["rows"]) == 3, str(len(j["rows"])))
check("按用量降序", [r["id"] for r in j["rows"]][0] == "doubao-seed-2-1-lite-260915")
lite = rows["doubao-seed-2-1-lite-260915"]
check("额度百分比算对（520000/500000=104）", lite["quota_pct"] == 104.0, str(lite["quota_pct"]))
check("超额度行 bar 拉满", lite["bar"] == 100.0, str(lite["bar"]))
char = rows["doubao-seed-character-260628"]
check("bar 相对最大值", abs(char["bar"] - 1500 * 100.0 / 520000) < 0.1, str(char["bar"]))
check("中文名来自顺序表", char["label"] == "豆包 Character 2.0", char["label"])
check("lite 名称带额度告急", "额度告急" in lite["label"], lite["label"])
check("上游归属", char["upstream"] == "火山方舟", char["upstream"])
check("熔断剩余秒数透出", lite["cooling"] == 900, str(lite["cooling"]))
orow = rows["nvidia/nemotron-3-super-120b-a12b:free"]
check("非方舟档不算额度", orow["quota"] == 0 and orow["quota_pct"] is None,
      "%s/%s" % (orow["quota"], orow["quota_pct"]))
check("非方舟档上游名", orow["upstream"] == "OpenRouter", orow["upstream"])
check("合计 tokens", j["sum"]["total"] == 521600, str(j["sum"]["total"]))
check("合计调用次数", j["sum"]["calls"] == 321, str(j["sum"]["calls"]))
check("平均每次", abs(j["sum"]["avg"] - 521600 / 321.0) < 0.2, str(j["sum"]["avg"]))
check("prompt/completion 拆分",
      j["sum"]["prompt"] == 401090 and j["sum"]["completion"] == 120510,
      "%s/%s" % (j["sum"]["prompt"], j["sum"]["completion"]))
check("透出运行时长与请求数", j["uptime_sec"] == 3725 and j["requests"] == 42)

print("\n== 代理版本旧（没有 token_usage）==")
HEALTH.pop("token_usage")
code, j = get_usage()
check("live=True 但 supported=False", j["live"] is True and j["supported"] is False,
      "%s/%s" % (j["live"], j["supported"]))
check("空表不报错", j["rows"] == [] and j["sum"]["total"] == 0)

print("\n== 代理没起 ==")
xs.BRAIN_HEALTH_URL = "http://127.0.0.1:18098/health"     # 无人监听
xs.CFG["base"] = "http://127.0.0.1:18098/v1"
xs.ROOT_URL = "http://127.0.0.1:18098"
code, j = get_usage()
check("HTTP 200 且 live=False", code == 200 and j["live"] is False,
      "%s/%s" % (code, j["live"]))
check("rows 为空", j["rows"] == [])

srv.shutdown()
print("\n%s（%d 项失败）" % ("全部通过" if not FAILS else "有失败: " + ", ".join(FAILS),
                          len(FAILS)))
sys.exit(1 if FAILS else 0)
