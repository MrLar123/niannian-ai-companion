# -*- coding: utf-8 -*-
"""点击反馈端点自测：POST /api/poke、/api/gaze 顺带的 poke 字段、点击行为模式。

用 test_client 不起进程（沙箱里 agent 拉起的常驻进程活不过回合）。
★ 会临时改写 data/click_mode.txt —— 测试结束按原值还原，不留痕迹。
"""
import os, sys, io
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


# ---- 0. 备份持久化文件，测试结束还原 ----
def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return None


def _restore_file(path, content):
    try:
        if content is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
    except Exception:
        pass


_backup_mode = _read(S.CLICK_MODE_FILE)
_backup_style = _read(S.POKE_STYLE_FILE)
_orig_mode = S.CLICK["mode"]
_orig_style = S.CLICK["style"]


def restore():
    S.CLICK["mode"] = _orig_mode
    S.CLICK["style"] = _orig_style
    _restore_file(S.CLICK_MODE_FILE, _backup_mode)
    _restore_file(S.POKE_STYLE_FILE, _backup_style)


# ---- 1. poke 自增 ----
base = S.POKE["seq"]
r = c.post("/api/poke")
j = r.get_json()
check("POST /api/poke ok", r.status_code == 200 and j["ok"] is True, "code=%d" % r.status_code)
check("seq 自增 1", j["seq"] == base + 1, "seq=%r base=%r" % (j.get("seq"), base))

c.post("/api/poke")
c.post("/api/poke")
check("连点三次 seq=+3", S.POKE["seq"] == base + 3, "seq=%r" % S.POKE["seq"])

# 空 body 也不能炸（壳可能不带 Content-Type）
r = c.post("/api/poke", data="")
check("空 body 不报错", r.status_code == 200 and r.get_json()["ok"] is True,
      "code=%d" % r.status_code)

# ---- 1.5 点击坐标透传（壳算好归一化坐标 -> 后端搬运 -> 页面拿去做射线判定）----
r = c.post("/api/poke", json={"x": 0.42, "y": 0.17})
j = r.get_json()
check("POST /api/poke 回传坐标", j.get("x") == 0.42 and j.get("y") == 0.17, str(j))
check("POKE 全局记录坐标", S.POKE["x"] == 0.42 and S.POKE["y"] == 0.17,
      "x=%r y=%r" % (S.POKE["x"], S.POKE["y"]))
j = c.get("/api/gaze").get_json()
check("/api/gaze 带回 poke.x/y",
      j["poke"].get("x") == 0.42 and j["poke"].get("y") == 0.17, str(j["poke"]))

c.post("/api/poke", json={"x": 1.7, "y": -0.4})
check("坐标越界钳制到 0~1", S.POKE["x"] == 1.0 and S.POKE["y"] == 0.0,
      "x=%r y=%r" % (S.POKE["x"], S.POKE["y"]))

# ★ 坐标坏了不能让反应哑火：非数字一律退化成 None（页面会当"点在身上"处理）
for bad in ({"x": "0.5", "y": 0.2}, {"x": None, "y": 1}, {}, {"x": True, "y": 0.2}):
    c.post("/api/poke", json=bad)
    check("非法坐标 %r -> None" % (bad,),
          S.POKE["x"] is None and S.POKE["y"] is None,
          "x=%r y=%r" % (S.POKE["x"], S.POKE["y"]))

# ---- 2. /api/gaze 顺带返回 poke ----
r = c.get("/api/gaze")
j = r.get_json()
check("GET /api/gaze 带 poke 字段", isinstance(j.get("poke"), dict), str(j.get("poke")))
check("poke.seq 与全局一致", j["poke"]["seq"] == S.POKE["seq"],
      "resp=%r global=%r" % (j["poke"].get("seq"), S.POKE["seq"]))
check("poke.age 非负（刚上报过）", j["poke"]["age"] >= 0,
      "age=%.3f" % (j["poke"]["age"] if j["poke"]["age"] is not None else -1))

# 从未上报过时 age = -1（把时间戳清掉再验）
_saved_ts = S.POKE["ts"]
S.POKE["ts"] = 0.0
j = c.get("/api/gaze").get_json()
check("poke 从未上报 -> age=-1", j["poke"]["age"] == -1.0, "age=%r" % j["poke"]["age"])
S.POKE["ts"] = _saved_ts

# ---- 3. 点击行为模式读取 ----
r = c.get("/api/click")
j = r.get_json()
check("GET /api/click ok", r.status_code == 200 and j["ok"] is True)
check("mode 在合法集合内", j["mode"] in S.CLICK_MODES, "mode=%r" % j["mode"])
check("modes 字段齐全", j["modes"] == list(S.CLICK_MODES), str(j["modes"]))

# ---- 4. 非法值拦截 ----
for bad in ("", "click", "POKE2", None, 123):
    r = c.post("/api/click/config", json={"mode": bad})
    check("非法 mode=%r -> 400" % (bad,), r.status_code == 400, "code=%d" % r.status_code)
check("被拒后 mode 未变", S.CLICK["mode"] == _orig_mode, "mode=%r" % S.CLICK["mode"])

# ---- 5. 合法值写入 + 持久化 ----
target = "chat" if _orig_mode == "poke" else "poke"
r = c.post("/api/click/config", json={"mode": target})
j = r.get_json()
check("切到 %s ok" % target, r.status_code == 200 and j["mode"] == target, str(j))
check("label 非空", bool(j.get("label")), j.get("label"))
check("无写入警告", j.get("warning") == "", repr(j.get("warning")))
check("文件已写入", os.path.exists(S.CLICK_MODE_FILE)
      and open(S.CLICK_MODE_FILE, encoding="utf-8").read().strip() == target,
      "content=%r" % (open(S.CLICK_MODE_FILE, encoding="utf-8").read() if os.path.exists(S.CLICK_MODE_FILE) else None))
check("GET 反映新值", c.get("/api/click").get_json()["mode"] == target)
# 大小写 / 空白容错
r = c.post("/api/click/config", json={"mode": "  %s  " % target.upper()})
check("大小写+空白容错", r.status_code == 200 and r.get_json()["mode"] == target,
      "code=%d" % r.status_code)

# ---- 5.5 点击手感档（soft / vivid）----
j = c.get("/api/click").get_json()
check("GET /api/click 带 style", j.get("style") in S.POKE_STYLES, "style=%r" % j.get("style"))
check("styles 字段齐全", j.get("styles") == list(S.POKE_STYLES), str(j.get("styles")))
check("回归：/api/gaze 带 click_style",
      c.get("/api/gaze").get_json().get("click_style") in S.POKE_STYLES,
      repr(c.get("/api/gaze").get_json().get("click_style")))

for bad in ("", "hard", None, 5):
    r = c.post("/api/click/config", json={"style": bad})
    check("非法 style=%r -> 400" % (bad,), r.status_code == 400, "code=%d" % r.status_code)
check("被拒后 style 未变", S.CLICK["style"] == _orig_style, "style=%r" % S.CLICK["style"])

target_style = "vivid" if _orig_style == "soft" else "soft"
r = c.post("/api/click/config", json={"style": target_style})
j = r.get_json()
check("切手感到 %s" % target_style,
      r.status_code == 200 and j["style"] == target_style, str(j))
check("style_label 非空", bool(j.get("style_label")), j.get("style_label"))
check("手感切换无写入警告", j.get("warning") == "", repr(j.get("warning")))
check("手感文件已写入",
      os.path.exists(S.POKE_STYLE_FILE)
      and open(S.POKE_STYLE_FILE, encoding="utf-8").read().strip() == target_style,
      "content=%r" % _read(S.POKE_STYLE_FILE))
check("gaze 反映新手感", c.get("/api/gaze").get_json()["click_style"] == target_style)

# ★ 只给 style 时不能把 mode 连带改坏（壳的两个子菜单各发各的）
mode_before = S.CLICK["mode"]
c.post("/api/click/config", json={"style": _orig_style})
check("只改 style 不动 mode", S.CLICK["mode"] == mode_before, "mode=%r" % S.CLICK["mode"])

r = c.post("/api/click/config", json={"mode": S.CLICK["mode"], "style": target_style})
check("mode+style 一起改 ok",
      r.status_code == 200 and r.get_json()["style"] == target_style, str(r.get_json()))

r = c.post("/api/click/config", json={})
check("mode/style 都不给 -> 400", r.status_code == 400, "code=%d" % r.status_code)

# ---- 6. 回归：眼神端点没被破坏 ----
r = c.post("/api/gaze/cursor", json={"x": 10, "y": 20, "rect": [0, 0, 100, 200]})
check("回归 /api/gaze/cursor", r.status_code == 200)
j = c.get("/api/gaze").get_json()
check("回归 /api/gaze 字段完整",
      all(k in j for k in ("ok", "age", "x", "y", "rect", "mode", "poke", "click_style")),
      str(sorted(j)))
check("回归 mode 仍可读", j["mode"] in S.GAZE_MODES, "mode=%r" % j["mode"])
r = c.get("/api/health")
check("回归 /api/health", r.status_code == 200, "code=%d" % r.status_code)

restore()
print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
