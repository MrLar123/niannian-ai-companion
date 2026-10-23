# -*- coding: utf-8 -*-
"""眼神端点自测：POST /api/gaze/cursor + GET /api/gaze。
用 test_client 不起进程，避免沙箱进程树问题。"""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import xiaoyun_server as S

c = S.app.test_client()
ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  %s %s" % (name, extra))
    else:
        fail += 1
        print("FAIL  %s %s" % (name, extra))

# ---- 1. 初始状态：从未收到 ----
r = c.get("/api/gaze")
j = r.get_json()
check("GET /api/gaze 初始 ok", r.status_code == 200 and j["ok"] is True)
check("初始 age=-1（从未收到）", j["age"] == -1.0, "age=%r" % j["age"])
check("初始 x/y/rect 为 None", j["x"] is None and j["rect"] is None)

# ---- 2. 正常上报 ----
r = c.post("/api/gaze/cursor", json={"x": 1200, "y": 400,
                                     "rect": [1000, 300, 1400, 700]})
j = r.get_json()
check("POST 返回 ok", r.status_code == 200 and j["ok"] is True)
r = c.get("/api/gaze")
j = r.get_json()
check("上报后可读回", j["x"] == 1200 and j["y"] == 400 and j["rect"] == [1000.0, 300.0, 1400.0, 700.0],
      "x=%r y=%r rect=%r" % (j["x"], j["y"], j["rect"]))
check("age 在 0~1s", 0 <= j["age"] < 1.0, "age=%.3f" % j["age"])

# ---- 3. 光标在窗口外（负坐标/大坐标）也接受 ----
r = c.post("/api/gaze/cursor", json={"x": -500, "y": 2000, "rect": [0, 0, 1920, 1080]})
check("窗口外坐标接受", r.status_code == 200)

# ---- 4. 参数错误：rect 长度不对 ----
r = c.post("/api/gaze/cursor", json={"x": 1, "y": 2, "rect": [1, 2, 3]})
check("rect 长度错 → 400", r.status_code == 400, "code=%d" % r.status_code)

# ---- 5. 参数错误：缺字段 ----
r = c.post("/api/gaze/cursor", json={"x": 1})
check("缺字段 → 400", r.status_code == 400, "code=%d" % r.status_code)

# ---- 6. 空 body 不炸 ----
r = c.post("/api/gaze/cursor", data="", content_type="application/json")
check("空 body → 400 不崩", r.status_code == 400, "code=%d" % r.status_code)

# ---- 7. 眼神模式（右键菜单数据源）----
j = c.get("/api/gaze").get_json()
check("GET /api/gaze 带 mode 字段", j.get("mode") in S.GAZE_MODES, "mode=%r" % j.get("mode"))
orig = S.GAZE["mode"]          # 测完还原，不污染用户配置
r = c.post("/api/gaze/config", json={"mode": "idle"})
check("POST config idle → ok", r.status_code == 200 and r.get_json().get("mode") == "idle")
check("读回 mode=idle", c.get("/api/gaze").get_json().get("mode") == "idle")
try:
    with open(S.GAZE_MODE_FILE, encoding="utf-8") as f:
        saved = f.read().strip()
except Exception as e:
    saved = "读取失败:%r" % (e,)
check("持久化文件已写入", saved == "idle", "file=%r" % saved)
r = c.post("/api/gaze/config", json={"mode": "bogus"})
check("非法 mode → 400", r.status_code == 400, "code=%d" % r.status_code)
r = c.post("/api/gaze/config", data="", content_type="application/json")
check("config 空 body → 400 不崩", r.status_code == 400, "code=%d" % r.status_code)
c.post("/api/gaze/config", json={"mode": orig})
check("恢复原模式", c.get("/api/gaze").get_json().get("mode") == orig, "mode=%s" % orig)

# ---- 8. 回归：既有端点没被破坏 ----
for path in ("/api/ui/rects", "/api/shell/beat"):
    r = c.get(path) if path == "/api/ui/rects" else c.post(path, json={})
    check("回归 %s" % path, r.status_code == 200 and r.get_json().get("ok") is True)

# ---- 9. age 随时间增长 ----
j0 = c.get("/api/gaze").get_json()
time.sleep(0.25)
j1 = c.get("/api/gaze").get_json()
check("age 随时间增长", j1["age"] > j0["age"], "%.3f -> %.3f" % (j0["age"], j1["age"]))

print("\n===== 后端: %d passed, %d failed =====" % (ok, fail))
sys.exit(1 if fail else 0)
