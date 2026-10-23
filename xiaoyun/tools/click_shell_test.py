# -*- coding: utf-8 -*-
"""点击行为自检：右键菜单项 + 壳的单击/双击分发。

菜单是纯数据（popup_menu_layout），点击分发是纯逻辑（Catcher._body_click 不碰 Win32），
所以两层都能用 FakeApp 直接验，不需要真窗口。
用壳自己的运行时跑：.venv/Scripts/python.exe tools/click_shell_test.py
"""
import sys, io, time
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


# ==================== 1. 菜单 ====================
class FakeApp:
    models = [{"key": "peier", "label": "佩儿"}]
    model_key = "peier"
    motions = [{"key": "idle", "label": "内置待机"}]
    motion_key = "idle"
    chat_models = [{"id": "auto", "label": "自动"}]
    chat_model = "auto"
    chat_best = ""
    voices = [{"id": "v1", "label": "uranus"}]
    voice_id = "v1"
    presets = [{"key": "p1", "label": "高冷御姐"}]
    preset_key = "p1"
    gaze_mode = "always"
    click_mode = "poke"
    poke_style = "soft"


rows = D.popup_menu_layout(FakeApp)
sub = [r for r in rows if r[0] == "submenu" and "点击反应" in r[1]]
check("右键菜单里有「点击反应」子菜单", len(sub) == 1, "找到 %d 个" % len(sub))

kids = sub[0][4] if sub else []
check("两档齐全", len(kids) == 2, "实际 %d 项：%s" % (len(kids), [k[0] for k in kids]))
check("档位顺序 poke/chat", [k[1] - D.MENU_CLICK_BASE for k in kids] == [0, 1],
      "cmd=%s" % [hex(k[1]) for k in kids])
check("默认档文案说明双击仍可开聊天窗",
      bool(kids) and "双击" in kids[0][0] and "聊天" in kids[0][0],
      kids[0][0] if kids else "")
check("勾选跟随 click_mode（poke）", [k[2] for k in kids] == [True, False],
      "checked=%s" % [k[2] for k in kids])

FakeApp.click_mode = "chat"
kids = [r for r in D.popup_menu_layout(FakeApp) if r[0] == "submenu" and "点击反应" in r[1]][0][4]
check("勾选跟随 click_mode（chat）", [k[2] for k in kids] == [False, True])
FakeApp.click_mode = "poke"

# ---- 点击手感子菜单（soft / vivid）----
srows = [r for r in D.popup_menu_layout(FakeApp) if r[0] == "submenu" and "点击手感" in r[1]]
check("右键菜单里有「点击手感」子菜单", len(srows) == 1, "找到 %d 个" % len(srows))
skids = srows[0][4] if srows else []
check("手感两档齐全", len(skids) == 2, "实际 %d 项" % len(skids))
check("手感档顺序 soft/vivid",
      [k[1] - D.MENU_POKE_STYLE_BASE for k in skids] == [0, 1],
      "cmd=%s" % [hex(k[1]) for k in skids])
check("手感勾选跟随 poke_style（soft）", [k[2] for k in skids] == [True, False],
      "checked=%s" % [k[2] for k in skids])
FakeApp.poke_style = "vivid"
skids = [r for r in D.popup_menu_layout(FakeApp) if r[0] == "submenu" and "点击手感" in r[1]][0][4]
check("手感勾选跟随 poke_style（vivid）", [k[2] for k in skids] == [False, True])
FakeApp.poke_style = "soft"

# 命令 ID 区间不能和既有档位重叠（串档会点到别的功能）
ranges = [
    ("模型", D.MENU_MODEL_BASE), ("对话模型", D.MENU_CHATMODEL_BASE),
    ("音色", D.MENU_VOICE_BASE), ("人设", D.MENU_PRESET_BASE),
    ("动作", D.MENU_MOTION_BASE), ("眼神", D.MENU_GAZE_BASE),
    ("点击反应", D.MENU_CLICK_BASE),
]
for label, base in (("点击反应", D.MENU_CLICK_BASE), ("点击手感", D.MENU_POKE_STYLE_BASE)):
    others = [(n, b) for (n, b) in ranges if b != base]
    clash = [n for (n, b) in others if not (base + 0x100 <= b or base >= b + 0x100)]
    check("%s 区间 0x%04X-0x%04X 与其它档位不重叠" % (label, base, base + 0xFF),
          not clash, "冲突：%s" % clash)
check("点击档位在兜底分支（cmd>=MENU_MODEL_BASE）之前",
      D.MENU_CLICK_BASE > D.MENU_MODEL_BASE,
      "0x%04X > 0x%04X" % (D.MENU_CLICK_BASE, D.MENU_MODEL_BASE))


# ==================== 2. 壳的点击分发 ====================
class ClickApp(FakeApp):
    def __init__(self):
        self.chats = 0
        self.pokes = 0
        self.t0 = time.time()
        self.click_mode = "poke"
        self.last_poke = None       # 最后一次 /api/poke 的 body（验坐标透传）

    def show_chat(self):
        self.chats += 1

    def _voice_post(self, path, obj=None):
        if path == "/api/poke":
            self.pokes += 1
            self.last_poke = obj
        return {"ok": True}


def settle(sec=0.25):
    time.sleep(sec)     # 分发是丢到后台线程做的，等它跑完


def fresh(mode):
    app = ClickApp()
    app.click_mode = mode
    c = D.Catcher(app)
    c.max_move = 0
    return app, c


# ---- poke 模式：单击戳一下（不弹窗）----
app, c = fresh("poke")
c._body_click()
settle()
check("poke 模式单击 -> 上报 poke", app.pokes == 1, "pokes=%d" % app.pokes)
check("poke 模式单击 -> 不弹聊天窗", app.chats == 0, "chats=%d" % app.chats)

# ---- poke 模式：两次点击间隔超过双击窗口 -> 都只是"戳" ----
app, c = fresh("poke")
c._body_click()
settle(0.55)             # > CLICK_DOUBLE_SEC(0.42)
c._body_click()
settle()
check("间隔超过双击窗口 -> 两次都只是戳，不弹窗",
      app.pokes == 2 and app.chats == 0,
      "pokes=%d chats=%d" % (app.pokes, app.chats))

app, c = fresh("poke")
c._body_click()          # 第一次：戳
c._last_click = time.monotonic() - 0.1   # 假装只过了 100ms（< 420ms 双击窗口）
c._body_click()          # 第二次：双击 -> 开窗
settle()
check("poke 模式双击 -> 打开聊天窗", app.chats == 1, "chats=%d" % app.chats)
check("双击时第一下仍然是一次戳", app.pokes >= 1, "pokes=%d" % app.pokes)

# ---- chat 模式：单击直接开窗（老行为）----
app, c = fresh("chat")
c._body_click()
settle()
check("chat 模式单击 -> 打开聊天窗", app.chats == 1, "chats=%d" % app.chats)
check("chat 模式单击 -> 不上报 poke", app.pokes == 0, "pokes=%d" % app.pokes)

# ---- 未知模式（配置文件被手改成乱码）时退回默认 poke ----
app, c = fresh("garbage")
c._body_click()
settle()
check("未知 click_mode 退回 poke 行为", app.pokes == 1 and app.chats == 0,
      "pokes=%d chats=%d" % (app.pokes, app.chats))

# ---- 点击位置上报：窗口内归一化坐标（页面拿它做射线判定部位）----
# Catcher._click_norm 只依赖 GetWindowRect 一个 API，所以整个 user32 换掉即可
_orig_user32 = D.user32


class RectUser32:
    """窗口固定落在 (100,200)-(660,1140)，即 560x940。"""

    def __init__(self, w=560, h=940):
        self.w, self.h = w, h

    def GetWindowRect(self, hwnd, pref):
        r = pref._obj
        r.left, r.top = 100, 200
        r.right, r.bottom = 100 + self.w, 200 + self.h
        return 1


D.user32 = RectUser32()

app, c = fresh("poke")
c.pet_hwnd = 1
c.start_cur = (380, 435)          # 窗口内偏移 (280, 235) -> (0.5, 0.25)
c._body_click()
settle()
check("上报带归一化坐标", app.last_poke == {"x": 0.5, "y": 0.25}, repr(app.last_poke))

app, c = fresh("poke")
c.pet_hwnd = 1
c.start_cur = (100, 200)
c._body_click()
settle()
check("窗口左上角 -> (0,0)", app.last_poke == {"x": 0.0, "y": 0.0}, repr(app.last_poke))

app, c = fresh("poke")
c.pet_hwnd = 1
c.start_cur = (660, 1140)
c._body_click()
settle()
check("窗口右下角 -> (1,1)", app.last_poke == {"x": 1.0, "y": 1.0}, repr(app.last_poke))

# ★ 坐标拿不到时必须退化成空对象（而不是干脆不上报）：
#   点击反馈是体验项，宁可"部位判不准"，也不能"戳了没反应"
app, c = fresh("poke")
c.pet_hwnd = 1
c.start_cur = None
c._body_click()
settle()
check("没拿到按下点 -> 仍上报（空对象）",
      app.pokes == 1 and app.last_poke == {}, "pokes=%d body=%r" % (app.pokes, app.last_poke))

D.user32 = RectUser32(w=0, h=0)
app, c = fresh("poke")
c.pet_hwnd = 1
c.start_cur = (10, 10)
c._body_click()
settle()
check("窗口尺寸为 0 -> 退化成空对象", app.last_poke == {}, repr(app.last_poke))

D.user32 = _orig_user32

# ---- 上报失败不能抛异常（后端没起时点她一下不应该崩）----
class BrokenApp(ClickApp):
    def _voice_post(self, path, obj=None):
        raise RuntimeError("backend down")


app = BrokenApp()
app.click_mode = "poke"
c = D.Catcher(app)
c.max_move = 0
try:
    c._body_click()
    settle()
    check("后端挂了时戳一下不抛异常", True)
except Exception as e:
    check("后端挂了时戳一下不抛异常", False, repr(e))

print("\n===== 点击: %d passed, %d failed =====" % (ok, fail))
sys.exit(1 if fail else 0)
