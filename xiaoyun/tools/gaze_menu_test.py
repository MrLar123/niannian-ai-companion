# -*- coding: utf-8 -*-
"""右键菜单自检：眼神子菜单存在、三档齐全、勾选跟着 gaze_mode 走、命令 ID 不串档。

popup_menu_layout 是纯数据函数（不碰 Win32），所以能用 FakeApp 直接验。
用壳自己的运行时跑：.venv/Scripts/python.exe tools/gaze_menu_test.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import desktop_app as D

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  %s %s" % (name, extra))
    else:
        fail += 1
        print("FAIL  %s %s" % (name, extra))


class FakeApp:
    models = [{"key": "peier", "label": "佩儿"}]
    model_key = "peier"
    motions = [{"key": "idle", "label": "内置待机"}, {"key": "xiangxiang", "label": "xiangxiang"}]
    motion_key = "idle"
    chat_models = [{"id": "auto", "label": "自动"}]
    chat_model = "auto"
    chat_best = "x/y:free"
    voices = [{"id": "v1", "label": "uranus"}]
    voice_id = "v1"
    presets = [{"key": "p1", "label": "高冷御姐"}]
    preset_key = "p1"
    gaze_mode = "idle"


rows = D.popup_menu_layout(FakeApp)
sub = [r for r in rows if r[0] == "submenu" and "眼神" in r[1]]
check("右键菜单里有「眼神追随」子菜单", len(sub) == 1, "找到 %d 个" % len(sub))

kids = sub[0][4] if sub else []
check("三档齐全", len(kids) == 3, "实际 %d 项：%s" % (len(kids), [k[0] for k in kids]))
check("档位顺序 off/idle/always",
      [k[1] - D.MENU_GAZE_BASE for k in kids] == [0, 1, 2],
      "cmd=%s" % [hex(k[1]) for k in kids])
check("文案说明了与动作的关系",
      any("动作" in k[0] for k in kids), str([k[0] for k in kids]))

# 勾选：gaze_mode=idle 时只有第 2 项打勾
checked = [k[2] for k in kids]
check("勾选跟随 gaze_mode（idle）", checked == [False, True, False], "checked=%s" % checked)

FakeApp.gaze_mode = "off"
kids = [r for r in D.popup_menu_layout(FakeApp) if r[0] == "submenu" and "眼神" in r[1]][0][4]
check("勾选跟随 gaze_mode（off）", [k[2] for k in kids] == [True, False, False])

FakeApp.gaze_mode = "always"
kids = [r for r in D.popup_menu_layout(FakeApp) if r[0] == "submenu" and "眼神" in r[1]][0][4]
check("勾选跟随 gaze_mode（always）", [k[2] for k in kids] == [False, False, True])

# 命令 ID 区间不能和既有档位重叠（串档会点错功能）
ranges = [
    ("模型", D.MENU_MODEL_BASE, 0x100),
    ("对话模型", D.MENU_CHATMODEL_BASE, 0x100),
    ("音色", D.MENU_VOICE_BASE, 0x100),
    ("人设", D.MENU_PRESET_BASE, 0x100),
    ("动作", D.MENU_MOTION_BASE, 0x100),
    ("眼神", D.MENU_GAZE_BASE, 0x100),
]
lo, hi = D.MENU_GAZE_BASE, D.MENU_GAZE_BASE + 0x100
clash = [n for (n, b, w) in ranges if n != "眼神" and not (hi <= b or lo >= b + w)]
check("眼神命令区间 0x%04X-0x%04X 与其它档位不重叠" % (lo, hi - 1), not clash, "冲突：%s" % clash)
check("眼神档位在最后一个兜底分支（cmd>=MENU_MODEL_BASE）之前",
      D.MENU_GAZE_BASE > D.MENU_MODEL_BASE, "0x%04X > 0x%04X" % (D.MENU_GAZE_BASE, D.MENU_MODEL_BASE))

print("\n===== 菜单: %d passed, %d failed =====" % (ok, fail))
sys.exit(1 if fail else 0)
