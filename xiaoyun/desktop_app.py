# -*- coding: utf-8 -*-
"""
小芸桌面 - Windows 原生桌面伴侣窗口
- pywebview(EdgeChromium/WebView2) 无边框 + 透明窗口，直接贴在桌面上
- 系统托盘常驻：显示/隐藏、聊天记录、置顶、清空记忆、退出
- 页面与对话接口同源，均由小芸后端(8090)提供
"""
import os
import sys
import json
import math
import time
import ctypes
import threading
import subprocess
import urllib.request
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import webview
from PIL import Image, ImageDraw
import pystray

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:" + os.environ.get("XIAOYUN_PORT", "8090")
ICON_PATH = os.path.join(ROOT, "xiaoyun.ico")
PID_PATH = os.path.join(ROOT, "desktop.pid")

# 宠物窗口尺寸。想让她更大就同时调大这两个值（等比），
# 但高度不能超过屏幕工作区（本机 1104），否则她会跑出屏幕。
PET_W, PET_H = 670, 1010
CHAT_W, CHAT_H = 380, 560

# 宠物窗用哪一页。换形象改这里即可。
#   "/desktoppmx"= PMX 模型（MMD 原生格式，如模之屋"姵儿"；MMDLoader 加载）— 当前使用
#   "/desktopfbx"= FBX 模型（FBXLoader 不支持 Shift-JIS，MMD 包骨骼名会乱码，不推荐）
#   "/desktopvrm"= VRM 模型（three-vrm 加载标准 VRM，模型自带真实骨骼）
#   "/desktop2d" = 2D 立绘（透明 PNG + 自建骨架蒙皮，可动四肢；双眼最清晰）
#   "/desktop"   = Live2D 纸片人（原方案，Haru，双眼清晰但二次元风）
#   "/desktop3d" = 3D 模型（three.js 渲染，御姐；左眼被模型刘海遮住）
PET_PAGE = os.environ.get("XIAOYUN_PET_PAGE", "/desktoppmx")

# 透明实现说明（绕了两圈才走通，别改错）：
#  1) pywebview 的 transparent=True 只把 WebView2 背景设为透明，
#     WinForms 窗体自身的底色（系统控件色 240,240,240）仍会透出来 → 用户看到"白色幕布"。
#  2) 只给窗口加色键也不行：WebView2 走 DirectComposition 渲染，色键抠不动它的画面，
#     于是把页面刷成品红后，整片品红照样显示出来 → 用户看到"红色底"。
#  3) 正确做法：WebView2 保持真透明（页面 background:transparent），
#     把【窗体自己的底色】刷成色键色（GDI 绘制的，色键能抠），再整体抠掉。
KEY_COLOR = (255, 0, 255)   # 品红，Live2D 模型里不会出现这个纯色

# 互动模式下的卡片底色与圆角（此时窗口不透明，鼠标事件才能进得来）
CARD_COLOR = (253, 239, 243)
CARD_RADIUS = 20

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
SWP_FRAMECHANGED = 0x0020

PET_TITLE = "念念"
CHAT_TITLE = "和念念聊天"

user32 = ctypes.WinDLL("user32", use_last_error=True)
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
SWP_SHOWWINDOW = 0x0040
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


# ---------------------------------------------------------------- 图标
def make_icon(path, size=256):
    """生成粉色心形图标（ico 同时供窗口标题栏与系统托盘使用）"""
    if os.path.exists(path):
        return path
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(400):
        t = 2 * math.pi * i / 399
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x, y))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    s = size * 0.76 / max(maxx - minx, maxy - miny)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    scaled = [(size / 2 + (p[0] - cx) * s, size / 2 + (p[1] - cy) * s) for p in pts]
    d.polygon(scaled, fill=(234, 167, 178, 255))
    img.save(path, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


STATUS_PATH = os.path.join(ROOT, "桌面状态.txt")
# 统一事件日志：与前端页面 / 后端 / ASR 共写一个文件，按时间排在一起。
# 排查"点了没反应/说完不回"时，这是能证明"事件到底有没有发生"的唯一入口。
EVENT_LOG = os.path.join(ROOT, "前端事件日志.txt")

# 菜单结构诊断日志：只记「菜单到底建出了什么」——每个子菜单有几项、当前选中的是哪个、
# 构建时抛了什么异常。为什么要单独一份：pystray 的菜单项是**建菜单那一刻**从
# 内存列表里取的，刷新失败时只是静默少几项（用户看到"菜单里就剩人物模型"），
# 而 refresh_* 里的 except 会把原因吞掉。没有这份日志，这类问题无从定位。
DIAG_LOG = os.path.join(ROOT, "托盘菜单诊断.log")


def diag(msg):
    """写一条带时间戳的诊断记录（pythonw 没有 stdout，只能落文件）。"""
    try:
        with open(DIAG_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def open_log_dir():
    """打开日志目录：出问题时一眼能拿到 前端事件日志 / 托盘菜单诊断 / srv_err 等。"""
    try:
        os.startfile(ROOT)
        note("已打开日志目录: %s" % ROOT)
    except Exception as e:
        note("打开日志目录失败: %r" % (e,))


def note(msg):
    """pythonw 没有 stdout，关键结果写文件便于排查。
    同时写进统一事件日志（带 [壳] 前缀），和页面/后端/ASR 的事件对得上时间线。"""
    try:
        with open(STATUS_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass
    try:
        with open(EVENT_LOG, "a", encoding="utf-8") as f:
            f.write("%s [壳] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def run_guarded(fn, timeout, tag):
    """在守护线程里执行 fn，最多等 timeout 秒；超时返回 None（不抛、不阻塞调用方）。

    ★★ 为什么必须有这个包装（2026-10-19 定位到的"启动卡死"真因）：
       use_transparent() 里的 set_winforms_form() 会用 pythonnet 从**非 UI 线程**
       去改 WinForms 的 TransparencyKey / BackColor。pywebview 的透明窗口是
       「先隐藏、Navigating 事件里才 Show」（winforms.py:782），所以 after_show
       的第一次尝试必然 find_hwnd()=None；第二次拿到的句柄，正好是窗口刚 Show、
       WebView2 刚开始初始化渲染的那一瞬间。此时跨线程改窗体属性，WinForms 会
       Invoke 到 UI 线程并**无超时地等待**，而 UI 线程正卡在 WebView2 的加载路径上，
       两边互等 —— 永久死锁。

       日志特征（前端事件日志.txt）：
           第1次: 透明已应用=False 句柄=None
           第2次: 开始设置透明…        ← 到此为止，再无下文
       历史统计：85 次"第2次开始"里 71 次成功、14 次永久挂起（约 16%），
       且 01:57:46 那次卡死发生在任何前端改动之前 —— 是长期潜伏的竞态，不是新引入的。

       有了这层守护：即使真的死锁，也只是废掉一个 daemon 线程，启动流程照常往下走，
       不会再出现"窗口未响应 → 启动器判定失败 → kill"。
    """
    box = {"ret": None}
    ev = threading.Event()

    def _run():
        try:
            box["ret"] = fn()
        except Exception as e:
            diag("guarded[%s] 异常: %r" % (tag, e))
        finally:
            ev.set()

    threading.Thread(target=_run, daemon=True, name="guard-" + tag).start()
    if not ev.wait(timeout):
        note("%s 超过 %.1fs 未返回 -> 判定卡住，放弃本轮（不连累启动）" % (tag, timeout))
        return None
    return box["ret"]


def wait_hwnd(title, timeout=6.0, poll=0.2):
    """等窗口真正出现（可见）。透明窗口是先隐藏、Navigating 时才 Show，所以
    固定 sleep 一个数字必然有一半概率太早或太晚，改成按可见性轮询。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_hwnd(title):
            return True
        time.sleep(poll)
    return False


def wait_size_stable(title, need_rounds=2, poll=0.25, timeout=4.0):
    """等窗口尺寸连续若干次不变。
    Show 之后的头几百毫秒里窗口还在被 DPI 校正/首帧布局反复 SetWindowPos，
    这段时间正是不该跨线程碰 WinForms 的窗口期。"""
    deadline = time.time() + timeout
    last, same = None, 0
    while time.time() < deadline:
        h = find_hwnd(title)
        if h:
            r = wintypes.RECT()
            if user32.GetWindowRect(h, ctypes.byref(r)):
                cur = (r.left, r.top, r.right - r.left, r.bottom - r.top)
                same = same + 1 if cur == last else 1
                last = cur
                if same >= need_rounds:
                    return True
        time.sleep(poll)
    return False


def paint_form_keycolor(title):
    """把 WinForms 窗体自身的底色刷成色键色（见文件头 KEY_COLOR 的说明）。
    用 pythonnet 的 Control.FromHandle 从 HWND 反查托管控件，再往上层找 Form。"""
    h = find_hwnd(title)
    if not h:
        return False
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Windows.Forms import Control
        from System.Drawing import Color
        from System import IntPtr as SIntPtr

        ctrl = Control.FromHandle(SIntPtr(h))
        if ctrl is None:
            return False
        form = ctrl.FindForm() or ctrl
        col = Color.FromArgb(KEY_COLOR[0], KEY_COLOR[1], KEY_COLOR[2])
        form.BackColor = col
        form.TransparencyKey = col
        form.Refresh()
        return True
    except Exception as e:
        print("[paint_form_keycolor]", repr(e))
        return False


# ------------------------------------------------------- 置顶（Win32）
def find_hwnd(title):
    found = []

    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            if buf.value == title and user32.IsWindowVisible(h):
                found.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


def get_work_area():
    """主显示器"工作区"（排除任务栏）。
    GetSystemMetrics 在 DPI 虚拟化/多显示器下会给出虚拟尺寸，
    直接用它算右下角可能把窗口放到屏幕外，所以一律用工作区。"""
    r = wintypes.RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0) and (r.right - r.left) > 0:
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def apply_colorkey(title):
    """分层窗口 + 色键：把 KEY_COLOR 整片抠成全透明。
    这是 Windows 上让 WinForms 窗口真正"透出桌面"的可靠做法。"""
    h = find_hwnd(title)
    if not h:
        return False
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    LWA_COLORKEY = 0x00000001
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    user32.SetWindowLongW(h, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    cref = KEY_COLOR[0] | (KEY_COLOR[1] << 8) | (KEY_COLOR[2] << 16)
    return bool(user32.SetLayeredWindowAttributes(h, cref, 0, LWA_COLORKEY))


def set_winforms_form(title, back=None, key=None):
    """改窗体底色 / 色键（None 表示不动，'empty' 表示清空色键）。"""
    h = find_hwnd(title)
    if not h:
        return False
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Windows.Forms import Control
        from System.Drawing import Color
        from System import IntPtr as SIntPtr

        ctrl = Control.FromHandle(SIntPtr(h))
        if ctrl is None:
            return False
        form = ctrl.FindForm() or ctrl
        # 只在值真的需要变化时才设置：TransparencyKey 每变更一次都会触发一整轮
        # 分层窗口更新 + 整窗重绘，窗口越大越慢。重复设置会把消息线程拖死（卡死过）。
        if key == "empty":
            if int(form.TransparencyKey.ToArgb()) != 0:
                form.TransparencyKey = Color.Empty
        elif key is not None:
            want = Color.FromArgb(key[0], key[1], key[2])
            if int(form.TransparencyKey.ToArgb()) != int(want.ToArgb()):
                form.TransparencyKey = want
        if back is not None:
            want = Color.FromArgb(back[0], back[1], back[2])
            if int(form.BackColor.ToArgb()) != int(want.ToArgb()):
                form.BackColor = want
        # 不要调 form.Refresh()：跨线程强制重绘会阻塞，交给后面的 SWP_FRAMECHANGED 刷新
        return True
    except Exception as e:
        note("改窗体属性失败: %r" % (e,))
        return False


def use_transparent(title):
    """★ 桌面模式：完全透明（她像直接站在桌面上），但鼠标事件会被穿透。"""
    h = find_hwnd(title)
    if not h:
        return False
    user32.SetWindowRgn(h, None, True)                    # 取消圆角裁剪
    set_winforms_form(title, back=KEY_COLOR, key=KEY_COLOR)
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    user32.SetWindowLongW(h, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    cref = KEY_COLOR[0] | (KEY_COLOR[1] << 8) | (KEY_COLOR[2] << 16)
    ok = user32.SetLayeredWindowAttributes(h, cref, 0, 0x1)
    user32.SetWindowPos(h, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | SWP_FRAMECHANGED)
    return bool(ok)


def use_interactive(title):
    """★ 互动模式：取消透明与分层，窗口变成可点击/可拖动的圆角卡片。
    必须去掉分层色键，否则 WebView2 的内容收不到鼠标事件（微软已知限制）。"""
    h = find_hwnd(title)
    if not h:
        return False
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    user32.SetWindowLongW(h, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
    set_winforms_form(title, back=CARD_COLOR, key="empty")
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    w, hh = r.right - r.left, r.bottom - r.top
    rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, hh + 1, CARD_RADIUS, CARD_RADIUS)
    user32.SetWindowRgn(h, rgn, True)                     # 圆角卡片
    user32.SetWindowPos(h, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | SWP_FRAMECHANGED)
    return True


def clamp_into_view(title):
    """把窗口完整推回可视工作区（pywebview 给出的坐标在 DPI 虚拟化下可能偏出去）。"""
    wl, wt, wr, wb = get_work_area()
    h = find_hwnd(title)
    if not h:
        return False
    r = wintypes.RECT()
    if not user32.GetWindowRect(h, ctypes.byref(r)):
        return False
    cw, ch = r.right - r.left, r.bottom - r.top
    nx = min(max(r.left, wl), max(wl, wr - cw))
    ny = min(max(r.top, wt), max(wt, wb - ch))
    if nx != r.left or ny != r.top:
        user32.SetWindowPos(h, 0, nx, ny, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE | 0x0004)  # SWP_NOZORDER
    return True


def set_topmost(title, on):
    h = find_hwnd(title)
    if not h:
        return False
    user32.SetWindowPos(h, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    return True


# ---------------------------------------------- 鼠标钩子（接管点击/拖动）
# 为什么需要它：窗口是分层色键透明窗口，微软明确说明 WebView2 的内容区域
# 收不到鼠标事件（事件会穿透到后面的程序），所以点她、拖她都失效。
# 这里用系统级低级鼠标钩子替代：点击 = 打开聊天窗，按住拖动 = 挪窗口。
WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
# ★ 拖动判定阈值。原来写 6px —— **太小**：125% 缩放 + 灵敏鼠标下，用户"点一下"时的
#   手抖轻松超过 6px，于是每一次"点击"都被判成拖动：窗口被手抖挪走几像素，而按钮动作
#   从未触发。日志特征很明确：只有"拖动结束，位置已更新"，没有"按下语音控件/被点击"。
#   这就是"点了没反应、偶尔才生效"的真凶（越紧张手越抖就越点不中）。
#   真实拖动通常 >20px，把阈值提上来即可区分；按在语音控件上时再放宽一档。
DRAG_THRESHOLD = 14
CTRL_DRAG_THRESHOLD = 26    # 光标按在麦克风/模式控件上时的阈值（更难被误判成拖动）
# ★ 近似命中容差：页面上报的坐标与真实渲染位置偶尔会差十几像素（布局变动/取整）。
#   此时用户是照着麦克风点的，却因差十几像素落进"身体区" -> 弹出聊天窗，非常困惑。
#   所以精确命中都落空后，只把**麦克风**的命中区再放宽这么多像素兜一刀。
#   模式标签不做放宽：它与麦克风上下相邻，两边都放宽会互相抢命中。
CTRL_NEAR_TOL = 16
# ★ 双击判定窗口（秒）：click_mode="poke" 时，单击是"戳一下"，窗口内第二下才算"要聊天"。
#   0.42s 是 Windows 默认双击速度（500ms）偏紧一档 —— 取太宽会让"连戳两下玩"误开窗口，
#   取太窄则正常双击打不开。命中判定不比对位置：她的手就这么大，换位置连点也算双击。
CLICK_DOUBLE_SEC = 0.42

# 只在她身体这一块响应点击/拖动（窗口是隐形的，若按整个窗口判定，
# 点旁边桌面空白也会误触发）。数值是窗口宽高的占比，由实测模型包围盒留出余量得来。
HITBOX_X = (0.26, 0.70)
HITBOX_Y = (0.05, 0.94)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# 必须显式声明签名：否则 CallNextHookEx 的 lParam 会按默认 c_long 转换而溢出报错
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]


class MouseHook(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.btn_down = False
        self.inside = False
        self.dragging = False
        self.moved = False
        self.start_cur = (0, 0)
        self.start_win = (0, 0)
        self._proc = None
        self.hook = None

    def pet_rect(self):
        h = find_hwnd(PET_TITLE)
        if not h:
            return None
        r = wintypes.RECT()
        if not user32.GetWindowRect(h, ctypes.byref(r)):
            return None
        return h, r

    @staticmethod
    def in_hitbox(rect, x, y):
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        return (rect.left + w * HITBOX_X[0] <= x <= rect.left + w * HITBOX_X[1]
                and rect.top + h * HITBOX_Y[0] <= y <= rect.top + h * HITBOX_Y[1])

    def on_down(self, x, y):
        rect = self.pet_rect()
        self.inside = bool(rect and self.in_hitbox(rect[1], x, y))
        self.btn_down = True
        self.dragging = False
        self.moved = False
        self.start_cur = (x, y)
        if self.inside:
            self.start_win = (rect[1].left, rect[1].top)

    def on_move(self, x, y):
        if not (self.btn_down and self.inside):
            return
        dx = x - self.start_cur[0]
        dy = y - self.start_cur[1]
        if not self.dragging and (abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD):
            self.dragging = True
            self.moved = True
        if self.dragging:
            h = find_hwnd(PET_TITLE)
            if h:
                user32.SetWindowPos(h, 0, self.start_win[0] + dx, self.start_win[1] + dy,
                                    0, 0, SWP_NOSIZE | SWP_NOACTIVATE | 0x0004)

    def on_up(self, x, y):
        was_inside = self.inside
        dragged = self.dragging
        self.btn_down = False
        self.inside = False
        self.dragging = False
        if was_inside and not dragged:
            self.app.show_chat()          # 单击 = 打开聊天窗
        elif dragged:
            clamp_into_view(PET_TITLE)
            note("拖动结束，窗口位置已更新")

    def on_right(self, x, y):
        rect = self.pet_rect()
        if rect and self.in_hitbox(rect[1], x, y):
            self.app.show_chat()

    def run(self):
        def proc(nCode, wParam, lParam):
            try:
                if nCode == 0:
                    info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    x, y = info.pt.x, info.pt.y
                    if wParam == WM_LBUTTONDOWN:
                        self.on_down(x, y)
                    elif wParam == WM_MOUSEMOVE:
                        self.on_move(x, y)
                    elif wParam == WM_LBUTTONUP:
                        self.on_up(x, y)
                    elif wParam == WM_RBUTTONUP:
                        self.on_right(x, y)
            except Exception as e:
                note("鼠标钩子异常: %r" % (e,))
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        try:
            self._proc = HOOKPROC(proc)     # 必须持有引用，否则会被 GC 回收
            self.hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
            note("鼠标钩子安装: %s" % bool(self.hook))
            if not self.hook:
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            note("鼠标钩子启动失败: %r" % (e,))


# ============================================ 隐形鼠标捕获层
# 目的：让「完全透明」的她也能被点击和拖动。
# 原理：另开一个近乎全透明（alpha=1/255，肉眼看不见）的弹出窗口，盖在她身上。
#   按 Windows 规则，分层窗口只要 alpha 不为 0，就仍能接收鼠标消息；alpha=1 时看不见但点得到。
#   实测：WindowFromPoint 命中该层，收到 down/up/move 事件。
# 为什么不用全局鼠标钩子 WH_MOUSE_LL：它的回调被 GIL 拖住会冻结整个系统的鼠标（踩过）。
# 这里用的是普通窗口消息（自己的消息循环 + WndProc），不拦截系统输入，安全。
WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
HTCLIENT = 1
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x00000002
CATcher_ALPHA = 1          # 1/255，几乎不可见

# ---- 右键弹出菜单（在她身上点右键 = 切换人物模型）----
WM_NULL = 0x0000
MF_STRING, MF_GRAYED, MF_CHECKED, MF_SEPARATOR = 0x0000, 0x0002, 0x0008, 0x0800
MF_POPUP = 0x0010          # 第三参数是子菜单句柄
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
MENU_MODEL_BASE = 0x2100   # 人物模型项的命令 ID 基址（+ 序号）
MENU_CHAT = 0x2201         # 「打开聊天窗」
MENU_LOGS = 0x2202         # 「打开日志文件夹」
MENU_CHATMODEL_BASE = 0x2300   # 对话模型项的命令 ID 基址（+ 序号）
MENU_VOICE_BASE = 0x2400       # 音色项的命令 ID 基址（+ 序号）
MENU_PRESET_BASE = 0x2500      # 人设/声线项的命令 ID 基址（+ 序号）
MENU_MOTION_BASE = 0x2600      # 动作（VMD 舞蹈）项的命令 ID 基址（+ 序号；0 = 内置待机）
MENU_GAZE_BASE = 0x2700        # 眼神追随模式（off / idle / always）
MENU_CLICK_BASE = 0x2800       # 点击行为（poke / chat）
MENU_POKE_STYLE_BASE = 0x2900  # 点击手感（soft / vivid）
MENU_TUNE = 0x2203             # 「调参：亮度/饱和度」（打开调色小窗）

# 点击行为两档：标签直接说清"双击还能不能开聊天窗"，避免找不到聊天入口
CLICK_CHOICES = [
    ("poke", "戳一下（双击才开聊天窗）"),
    ("chat", "直接打开聊天窗（老行为）"),
]

# 点击手感两档：动作幅度。名字直接说观感，不说技术词。
# ★ 为什么做成档而不是滑杆：她的人设是高冷御姐，"Q 弹"过头会崩人设，
#   但这个偏好只有本人看得准，所以留两档让人自己试。
POKE_STYLE_CHOICES = [
    ("soft",  "克制（被打扰到）"),
    ("vivid", "明显（有点 Q 弹）"),
]

# 眼神三档：标签要让人一眼看懂"和动作的关系"——这是用户提过的困惑点
GAZE_CHOICES = [
    ("off",    "关闭（眼神固定）"),
    ("idle",   "只在待机时追（动作时让位给动作）"),
    ("always", "始终追（动作时也看鼠标）"),
]


def popup_menu_layout(app):
    """右键菜单内容（纯数据，不碰 Win32，便于脱离窗口自检）。

    返回 [(kind, label, cmd_id, checked, children)]，kind ∈ {header, sep, item, submenu}；
    submenu 的 children = [(label, cmd_id, checked), ...]，cmd_id=0 表示只读提示行。
    顺序：人物模型 → 调参 → 动作 → 对话模型 → 音色 → 人设/声线 → 打开聊天窗 → 打开日志文件夹。
    auto 模式会在标签后面补一句"现在走谁"，省得猜自动路由选了什么。"""
    rows = []
    rows.append(("header", "人物模型（点击切换）", 0, False, None))
    rows.append(("sep", "", 0, False, None))
    if not app.models:
        rows.append(("header", "（取不到模型列表，后端没起？）", 0, False, None))
    for i, md in enumerate(app.models):
        label = md.get("label") or md.get("key") or ("模型%d" % i)
        # VRM 与 PMX 都能选：VRM 页只认 vrm，PMX 页只认 pmx。
        # 在标签后缀标出格式，免得切了没反应时一头雾水。
        _kind = md.get("kind") or ("vrm" if md.get("vrm") else "pmx")
        if _kind == "vrm":
            label += "  · VRM"
        rows.append(("item", label, MENU_MODEL_BASE + i,
                     md.get("key") == app.model_key, None))

    # ★ 调参：只动"观感"（亮度/饱和度/对比度/伽马），和动作库完全无关
    rows.append(("item", "调参：亮度 / 饱和度…", MENU_TUNE, False, None))

    rows.append(("sep", "", 0, False, None))
    rows.append(("header", "动作（点击切换）", 0, False, None))
    if not getattr(app, "motions", None):
        rows.append(("header", "（取不到动作列表，后端没起？）", 0, False, None))
    for i, md in enumerate(app.motions):
        label = md.get("label") or md.get("key") or ("动作%d" % i)
        rows.append(("item", label, MENU_MOTION_BASE + i,
                     md.get("key") == app.motion_key, None))

    # 眼神追随紧跟在动作后面：它和动作素材是同一处冲突点 ——
    # 动作自带眼骨轨道（实测 15 个 VMD 里 13 个有 右目/左目/両目 轨道），
    # 选"只在待机时追"就交给动作表演，选"始终追"就由鼠标说了算。
    gaze_kids = [(lab, MENU_GAZE_BASE + i, key == getattr(app, "gaze_mode", "always"))
                 for i, (key, lab) in enumerate(GAZE_CHOICES)]
    rows.append(("submenu", "眼神追随（看向鼠标）", 0, False, gaze_kids))

    # 点击行为紧跟在眼神后面：两者都是"对她的身体做动作会怎么反应"这一类设置
    click_kids = [(lab, MENU_CLICK_BASE + i, key == getattr(app, "click_mode", "poke"))
                  for i, (key, lab) in enumerate(CLICK_CHOICES)]
    rows.append(("submenu", "点击反应（单击她身上）", 0, False, click_kids))

    # 手感档紧跟点击反应：一条是"点一下会发生什么"，一条是"反应有多大"
    style_kids = [(lab, MENU_POKE_STYLE_BASE + i,
                   key == getattr(app, "poke_style", "soft"))
                  for i, (key, lab) in enumerate(POKE_STYLE_CHOICES)]
    rows.append(("submenu", "点击手感（动作幅度）", 0, False, style_kids))

    rows.append(("sep", "", 0, False, None))
    rows.append(("header", "对话模型（点击切换）", 0, False, None))
    if not app.chat_models:
        rows.append(("header", "（取不到对话模型列表，后端没起？）", 0, False, None))
    for i, cm in enumerate(app.chat_models):
        label = cm.get("label") or cm.get("id") or ("模型%d" % i)
        cid = cm.get("id") or ""
        if cid == "auto" and getattr(app, "chat_best", ""):
            label += "（现在走 %s）" % app.chat_best.split("/")[-1].replace(":free", "")
        rows.append(("item", label, MENU_CHATMODEL_BASE + i, cid == app.chat_model, None))

    rows.append(("sep", "", 0, False, None))
    voice_kids = [(o.get("label") or o.get("id") or "-", MENU_VOICE_BASE + i,
                   o.get("id") == app.voice_id) for i, o in enumerate(app.voices)]
    if not voice_kids:
        voice_kids = [("（取不到音色列表，后端没起？）", 0, False)]
    rows.append(("submenu", "音色（切换声音）", 0, False, voice_kids))

    preset_kids = [(p.get("label") or p.get("key") or "-", MENU_PRESET_BASE + i,
                    p.get("key") == app.preset_key) for i, p in enumerate(app.presets)]
    if not preset_kids:
        preset_kids = [("（取不到人设列表，后端没起？）", 0, False)]
    rows.append(("submenu", "人设 / 声线（切换人格）", 0, False, preset_kids))

    rows.append(("sep", "", 0, False, None))
    rows.append(("item", "打开聊天窗", MENU_CHAT, False, None))
    rows.append(("item", "打开日志文件夹（排查问题）", MENU_LOGS, False, None))
    return rows

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def _bind_win32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleHandleW.restype = wintypes.HINSTANCE
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                                  ctypes.c_ubyte, wintypes.DWORD]
    # 这两个必须声明签名：否则 HWND 会被当成 32 位整数传参，跨窗口/跨进程调用报 1400 无效句柄
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    user32.SetCapture.argtypes = [wintypes.HWND]
    user32.ReleaseCapture.argtypes = []
    # 用来兜底检测"物理左键已抬起但没收到 WM_LBUTTONUP"
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM      # 0 = 失败/超时
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM,
                                           wintypes.LPARAM, ctypes.c_uint, ctypes.c_uint,
                                           ctypes.POINTER(ctypes.c_size_t)]
    # ---- 右键弹出菜单（切换人物模型）----
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.restype = ctypes.c_int    # TPM_RETURNCMD 时返回所选命令 ID
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    return k32


k32 = _bind_win32()


class Catcher(threading.Thread):
    """盖在小芸身**和语音控件**上的隐形点击层。

    单击：点在麦克风/模式按钮上 -> 触发对应语音动作；
          点在身体区域 -> 打开聊天窗。
    按住拖动：挪位置。

    ★ 为什么语音控件的点击也由它代劳：透明桌面模式下 WebView2 收不到鼠标事件，
      页面上能点的控件必须由壳做。之前用两个独立小窗分别盖麦克风和模式标签，
      但实测这种"同线程第二个窗口"里会有一个被系统**拒绝置顶**
      （扩展样式永远缺 WS_EX_TOPMOST 位，被普通窗口压住 -> 真实点击被上层吃掉，
       表现就是"点了没反应"，而合成点击却可能刚好命中，极难排查）。
      捕获层一直稳定置顶，改成"一个窗口 + 内部按坐标分派"后彻底绕开这个坑。"""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.hwnd = None
        self.btn_down = False
        self.dragging = False
        self.max_move = 0
        self.start_cur = (0, 0)
        self.start_pet = (0, 0)
        self._proc = None
        self._placed = False
        self._warned = False
        self.pet_hwnd = None
        self._last_click = 0.0      # 上一次单击身体的 monotonic 时间（双击判定用）
        self._cls = "XiaoyunCatcher"
        self._hot = None                 # 按下时命中的语音控件: 'mic' / 'mode' / None
        self.mic_hit = None              # 麦克风按钮的屏幕矩形 (x,y,w,h)
        self.mode_hit = None             # 模式标签的屏幕矩形
        self._top_checked = False

    # ---- 位置同步：始终盖住她的身体 + 两个语音控件 ----
    def sync(self):
        if not self.hwnd:
            return
        pet = find_hwnd(PET_TITLE)
        if not pet:
            return
        self.pet_hwnd = pet          # 缓存下来，拖动时就不用再做昂贵的枚举
        r = wintypes.RECT()
        if not user32.GetWindowRect(pet, ctypes.byref(r)):
            return
        w, h = r.right - r.left, r.bottom - r.top
        x1 = r.left + int(w * HITBOX_X[0])
        y1 = r.top + int(h * HITBOX_Y[0])
        x2 = r.left + int(w * HITBOX_X[1])
        y2 = r.top + int(h * HITBOX_Y[1])

        # 语音控件的屏幕矩形（页面报的是 CSS 像素，按 客户区宽/视口宽 换算）
        cr = wintypes.RECT()
        pt0 = wintypes.POINT(0, 0)
        cw = 0
        if user32.GetClientRect(pet, ctypes.byref(cr)):
            cw = cr.right - cr.left
            user32.ClientToScreen(pet, ctypes.byref(pt0))
        self.mic_hit = self._to_screen(getattr(self.app, "mic_rect", None), pt0, cw, pad=12)
        self.mode_hit = self._to_screen(getattr(self.app, "mode_rect", None), pt0, cw, pad=10)

        # 把窗口撑到"身体区 ∪ 语音控件区"的最小外接矩形
        boxes = [(x1, y1, x2 - x1, y2 - y1)]
        for b in (self.mic_hit, self.mode_hit):
            if b:
                boxes.append(b)
        ux1 = min(b[0] for b in boxes)
        uy1 = min(b[1] for b in boxes)
        ux2 = max(b[0] + b[2] for b in boxes)
        uy2 = max(b[1] + b[3] for b in boxes)

        ok = user32.SetWindowPos(self.hwnd, HWND_TOPMOST, ux1, uy1, ux2 - ux1, uy2 - uy1,
                                 SWP_NOACTIVATE)
        if not ok and not self._warned:
            self._warned = True
            note("捕获层定位失败 SetWindowPos=%s err=%s" % (ok, ctypes.get_last_error()))
        if not self._top_checked:
            self._top_checked = True
            try:
                ex = user32.GetWindowLongW(self.hwnd, -20)
                note("捕获层自检: exstyle=0x%08X 置顶=%s 盖住=%s"
                     % (ex & 0xFFFFFFFF, bool(ex & 0x08), (ux1, uy1, ux2, uy2)))
            except Exception:
                pass

    @staticmethod
    def _to_screen(rect, pt0, cw, pad=0):
        """页面报的 CSS 矩形 -> 屏幕物理矩形。"""
        if not rect or cw <= 10:
            return None
        mx, my, mw, mh, vw, vh = rect
        s = cw / float(vw) if vw else 1.0
        p = pad * s
        return (int(pt0.x + mx * s - p), int(pt0.y + my * s - p),
                int(mw * s + p * 2), int((mh or mw) * s + p * 2))

    @staticmethod
    def _in_box(box, pt, tol=0):
        return bool(box) and box[0] - tol <= pt.x <= box[0] + box[2] + tol \
            and box[1] - tol <= pt.y <= box[1] + box[3] + tol

    def _ctrl_at(self, pt):
        """这个点落在哪个语音控件上？（麦克风优先）

        两级判定：先精确命中（mic 再 mode），都不中时把**麦克风的命中区**再放宽
        CTRL_NEAR_TOL 像素兜一刀。原因见 CTRL_NEAR_TOL 的注释：坐标偶尔有十几像素
        残差，不该因为这点残差就把"点麦克风"理解成"点了她的身体"。
        """
        for key, box in (("mic", self.mic_hit), ("mode", self.mode_hit)):
            if self._in_box(box, pt):
                return key
        if self._in_box(self.mic_hit, pt, CTRL_NEAR_TOL):
            return "mic"
        return None

    # ---- 鼠标行为 ----
    def refresh_pet(self):
        h = find_hwnd(PET_TITLE)
        if h:
            self.pet_hwnd = h
        return self.pet_hwnd

    def on_down(self):
        # 上一次按下没等到松开（消息丢失/被别的窗口抢走）时，先把旧状态收干净，
        # 否则 btn_down 会永久卡在 True：后续点击被当成"继续拖动"，动作全不触发。
        if self.btn_down:
            note("检测到重复按下（上次未收到松开）-> 复位，避免状态卡死")
            self.btn_down = False
            self.dragging = False
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        pet = self.refresh_pet()
        r = wintypes.RECT()
        if not pet or not user32.GetWindowRect(pet, ctypes.byref(r)):
            return
        self.btn_down = True
        self.dragging = False
        self.max_move = 0
        self._hot = self._ctrl_at(pt)
        # ★ 无条件记录每一次按下（以前只在命中控件时才记）：否则"点了但没命中任何控件"
        #   在日志里完全静默，无法区分"根本没收到点击"和"点偏了/点到了身体区"。
        #   坐标新鲜度一并记下：若"上报于 x.xs 前"数值很大，说明页面坐标已过时，
        #   命中区就会偏 —— 这正是"点得正中按钮却弹出聊天窗"的判据。
        age = time.time() - getattr(self.app, "mic_rect_ts", 0.0)
        note("按下（屏幕 %d,%d）命中=%s mic区=%s mode区=%s 坐标上报于%.1fs前"
             % (pt.x, pt.y, self._hot or "身体区/空白", self.mic_hit, self.mode_hit, age))
        self.start_cur = (pt.x, pt.y)
        self.start_pet = (r.left, r.top)
        user32.SetCapture(self.hwnd)      # 抓住鼠标：拖到窗口外也能继续收到移动消息

    def on_move(self):
        if not self.btn_down:
            return
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        dx = pt.x - self.start_cur[0]
        dy = pt.y - self.start_cur[1]
        if abs(dx) > self.max_move:
            self.max_move = abs(dx)
        if abs(dy) > self.max_move:
            self.max_move = abs(dy)
        # 按在语音控件上时用更宽的阈值，避免手抖把"点击"吃成"拖动"
        thr = CTRL_DRAG_THRESHOLD if self._hot else DRAG_THRESHOLD
        if not self.dragging and (abs(dx) > thr or abs(dy) > thr):
            self.dragging = True
        if not self.dragging or not self.pet_hwnd:
            return
        # 这里只能做一次轻量 SetWindowPos。
        # 绝不能在鼠标移动里调 sync()/find_hwnd（EnumWindows 很贵）：
        # 拖动时每秒上百条移动消息，一旦积压，窗口会被系统判定为"无响应"，
        # 鼠标输入被系统吞掉，用户体感就是整机卡死。
        user32.SetWindowPos(self.pet_hwnd, 0, self.start_pet[0] + dx, self.start_pet[1] + dy,
                            0, 0, SWP_NOSIZE | SWP_NOACTIVATE | 0x0004)

    def on_up(self):
        dragged = self.dragging
        hot = self._hot
        self.btn_down = False
        self.dragging = False
        self._hot = None
        try:
            user32.ReleaseCapture()
        except Exception:
            pass
        if dragged:
            # 不做边界钳制：拖到哪就是哪（含屏幕边缘/部分出屏）
            self.sync()
            note("拖动结束，位置已更新（最大位移 %dpx）" % self.max_move)
            return
        if hot == "mic":
            note("麦克风按钮被点击（位移 %dpx）" % self.max_move)
            threading.Thread(target=self.app.voice_toggle, daemon=True).start()
        elif hot == "mode":
            note("模式标签被点击（位移 %dpx）" % self.max_move)
            threading.Thread(target=self.app.voice_mode_toggle, daemon=True).start()
        else:
            # 页面坐标还没到位时（刚启动的头一两秒、或页面上报异常），点击绝不该被理解
            # 成"点了她的身体"：否则用户点麦克风会莫名其妙弹出聊天窗（已实际发生）。
            # 这段时间只记日志、不动作；超过 20 秒仍未拿到坐标才退回原行为，
            # 免得万一页面始终没上报就把她彻底点不动了。
            if self.mic_hit is None and (time.time() - getattr(self.app, "t0", 0.0)) < 20:
                note("控件坐标尚未就绪，本次点击忽略（不打开聊天窗）")
                return
            # 放到别的地方去做，让 WndProc 立刻返回（不能阻塞消息循环）
            self._body_click()

    def _body_click(self):
        """单击身体区的分发。

        click_mode = "poke"（默认）：单击 = 戳一下（只上报，页面播反应，不弹窗）；
                                    窗口内第二下 = 双击 -> 打开聊天窗。
        click_mode = "chat"        ：老行为，单击直接打开聊天窗。

        ★ 为什么把"开窗"降级成双击：用户反馈"点上去就被文字窗糊脸"。
          聊天窗另有三个入口（右键菜单「打开聊天窗」/ 托盘 / Ctrl+Alt+X），不会丢。
        ★ 连点判定用 time.monotonic 而不是 time.time：系统时间被校准时
          time.time 会跳变，可能把"两秒后的点击"算成双击。
        """
        app = self.app
        mode = getattr(app, "click_mode", "poke")
        now = time.monotonic()
        gap = now - self._last_click
        dbl = gap < CLICK_DOUBLE_SEC
        self._last_click = now

        if mode == "chat":
            note("点击身体区 -> 打开聊天窗（位移 %dpx）" % self.max_move)
            threading.Thread(target=app.show_chat, daemon=True).start()
            return
        if dbl:
            note("双击身体区（间隔 %.0fms）-> 打开聊天窗" % (gap * 1000))
            threading.Thread(target=app.show_chat, daemon=True).start()
            return
        note("戳一下（单击，位移 %dpx）" % self.max_move)
        threading.Thread(target=self._post_poke, daemon=True).start()

    def _click_norm(self):
        """按下点在宠物窗内的归一化坐标 (x, y)，拿不到就 (None, None)。

        为什么要归一化而不是直接给屏幕坐标：页面用 three.js 的射线去打模型，
        它只需要知道"点在画布的第几成位置"。页面画布铺满窗口（renderer.setSize
        (innerWidth, innerHeight)），所以窗口比例 = 画布比例，不需要额外补偿。

        ★ 用按下点（start_cur）而不是抬起点：按住拖动后松手不触发点击，
          走到这里说明没拖动，两点重合；但按下点更贴近用户"想点的位置"。
        """
        pt = getattr(self, "start_cur", None)
        if not pt:
            return None, None
        r = wintypes.RECT()
        hwnd = self.pet_hwnd or self.refresh_pet()
        try:
            if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None, None
        except Exception:
            return None, None
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return None, None
        return ((pt[0] - r.left) / float(w), (pt[1] - r.top) / float(h))

    def _post_poke(self):
        """上报一次"被戳"，带上点击位置。

        失败只记日志 —— 点击反馈是旁路，绝不能影响别的功能。
        坐标拿不到就退化成不带坐标：页面会当"点在身上"处理，反应照播。
        """
        try:
            nx, ny = self._click_norm()
            body = {}
            if nx is not None:
                body = {"x": round(nx, 4), "y": round(ny, 4)}
            j = self.app._voice_post("/api/poke", body)
            if not j.get("ok"):
                note("戳一下上报被拒: %r" % (j,))
        except Exception as e:
            note("戳一下上报失败: %r" % (e,))

    def on_right(self):
        """在她身上点右键：弹出菜单，切换人物模型 / 对话模型，并留一个日志入口。
        菜单内容由 popup_menu_layout() 生成（纯数据，可脱离 GUI 自检），这里只负责
        画出来和分发命令。选中后热生效，不需要重启任何东西。"""
        app = self.app
        models = app.models
        chat_models = app.chat_models
        rows = popup_menu_layout(app)
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        for kind, label, cmd_id, checked, children in rows:
            if kind == "sep":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            elif kind == "header":
                user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, label)
            elif kind == "submenu":
                sub = user32.CreatePopupMenu()
                for c_label, c_cmd, c_checked in children:
                    if not c_cmd:            # 只读提示行（例如"取不到列表"）
                        user32.AppendMenuW(sub, MF_STRING | MF_GRAYED, 0, c_label)
                    else:
                        user32.AppendMenuW(sub, MF_STRING | (MF_CHECKED if c_checked else 0),
                                           c_cmd, c_label)
                user32.AppendMenuW(menu, MF_POPUP | MF_STRING, sub, label)
            else:
                user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if checked else 0),
                                   cmd_id, label)

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        diag("右键菜单 | 人物模型=%d项 对话模型=%d项 音色=%d项 人设=%d项 | 当前: 形象=%s 对话=%s(%s) 音色=%s%s"
             % (len(models), len(chat_models), len(app.voices), len(app.presets),
                app.model_key or "-", app.chat_label or "-", app.chat_model or "-",
                app.voice_label or "-", app.voice_id or ""))
        # 经典怪癖的官方解法：TrackPopupMenu 前必须把菜单宿主窗提到前台，
        # 收尾再补一个 WM_NULL，否则菜单外点第一下关不掉菜单。
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)

        if cmd:
            diag("右键菜单命令 cmd=0x%04X（人物模型 %d 项 / 对话模型 %d 项）"
                 % (cmd, len(models), len(chat_models)))
        if cmd == MENU_CHAT:
            threading.Thread(target=app.show_chat, daemon=True).start()
        elif cmd == MENU_TUNE:
            threading.Thread(target=app.open_tuner, daemon=True).start()
        elif cmd == MENU_LOGS:
            threading.Thread(target=open_log_dir, daemon=True).start()
        elif MENU_PRESET_BASE <= cmd < MENU_PRESET_BASE + 0x100:
            idx = cmd - MENU_PRESET_BASE
            if 0 <= idx < len(app.presets):
                key = app.presets[idx].get("key")
                threading.Thread(target=app.set_preset, args=(key,), daemon=True).start()
        elif MENU_VOICE_BASE <= cmd < MENU_VOICE_BASE + 0x100:
            idx = cmd - MENU_VOICE_BASE
            if 0 <= idx < len(app.voices):
                vid = app.voices[idx].get("id")
                threading.Thread(target=app.set_voice, args=(vid,), daemon=True).start()
        elif MENU_CHATMODEL_BASE <= cmd < MENU_CHATMODEL_BASE + 0x100:
            idx = cmd - MENU_CHATMODEL_BASE
            if 0 <= idx < len(chat_models):
                mid = chat_models[idx].get("id")
                threading.Thread(target=app.set_chat_model, args=(mid,), daemon=True).start()
        elif MENU_MOTION_BASE <= cmd < MENU_MOTION_BASE + 0x100:
            idx = cmd - MENU_MOTION_BASE
            if 0 <= idx < len(app.motions):
                threading.Thread(target=app.set_motion,
                                 args=(app.motions[idx].get("key"),), daemon=True).start()
        elif MENU_GAZE_BASE <= cmd < MENU_GAZE_BASE + 0x100:
            idx = cmd - MENU_GAZE_BASE
            if 0 <= idx < len(GAZE_CHOICES):
                threading.Thread(target=app.set_gaze_mode,
                                 args=(GAZE_CHOICES[idx][0],), daemon=True).start()
        elif MENU_CLICK_BASE <= cmd < MENU_CLICK_BASE + 0x100:
            idx = cmd - MENU_CLICK_BASE
            if 0 <= idx < len(CLICK_CHOICES):
                threading.Thread(target=app.set_click_mode,
                                 args=(CLICK_CHOICES[idx][0],), daemon=True).start()
        elif MENU_POKE_STYLE_BASE <= cmd < MENU_POKE_STYLE_BASE + 0x100:
            idx = cmd - MENU_POKE_STYLE_BASE
            if 0 <= idx < len(POKE_STYLE_CHOICES):
                threading.Thread(target=app.set_poke_style,
                                 args=(POKE_STYLE_CHOICES[idx][0],), daemon=True).start()
        elif cmd >= MENU_MODEL_BASE:
            idx = cmd - MENU_MODEL_BASE
            if 0 <= idx < len(models):
                threading.Thread(target=app.set_model,
                                 args=(models[idx].get("key"),), daemon=True).start()

    def run(self):
        def proc(h, msg, wparam, lparam):
            try:
                if msg == WM_LBUTTONDOWN:
                    self.on_down(); return 0
                if msg == WM_MOUSEMOVE:
                    self.on_move(); return 0
                if msg == WM_LBUTTONUP:
                    self.on_up(); return 0
                if msg == WM_RBUTTONUP:
                    self.on_right()      # 右键 = 弹出「人物模型切换」菜单
                    return 0
                if msg == WM_NCHITTEST:
                    return HTCLIENT
                if msg == WM_DESTROY:
                    return 0
            except Exception as e:
                note("捕获层异常: %r" % (e,))
            return user32.DefWindowProcW(h, msg, wparam, lparam)

        self._proc = WNDPROC(proc)          # 持引用防 GC
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = k32.GetModuleHandleW(None)
        wc.lpszClassName = self._cls
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self._cls, "catcher", WS_POPUP, 0, 0, 10, 10, None, None, wc.hInstance, None)
        if not self.hwnd:
            note("捕获层创建失败")
            return
        user32.SetLayeredWindowAttributes(self.hwnd, 0, CATcher_ALPHA, LWA_ALPHA)
        user32.ShowWindow(self.hwnd, 8)     # SW_SHOWNA
        note("隐形捕获层已启用，句柄=%s" % self.hwnd)

        msg = wintypes.MSG()
        last_sync = 0.0
        while True:
            try:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                # ★ 兜底：按下后若**物理左键已经抬起**却迟迟收不到 WM_LBUTTONUP
                #   （消息丢失/被别的窗口抢走），就补一次"松开"。否则 btn_down 永久卡 True，
                #   之后一动鼠标就变成"拖着她跑"，而所有点击都不触发 —— 实测出现过。
                if self.btn_down and not (user32.GetAsyncKeyState(0x01) & 0x8000):
                    note("补发松开（物理左键已抬起但未收到 WM_LBUTTONUP）")
                    self.on_up()
                now = time.time()
                if now - last_sync > 0.2:   # 跟随宠物窗位置（拖完马上点也要跟得上）
                    self.sync()
                    last_sync = now
                    if not self._placed:
                        r = wintypes.RECT()
                        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
                        if r.right - r.left > 50:
                            self._placed = True
                            note("捕获层已盖到她身上: (%d,%d)-(%d,%d) 尺寸 %dx%d"
                                 % (r.left, r.top, r.right, r.bottom,
                                    r.right - r.left, r.bottom - r.top))
            except Exception as e:
                note("捕获层消息循环异常: %r" % (e,))
                time.sleep(0.5)
            time.sleep(0.02)


def pet_visibility_watch(app):
    """把宠物窗的可见性上报给后端。
    后端据此决定"这句话由谁发声"：她可见就她本人说，她收进托盘了就让聊天窗说。
    网页自己感知不到窗口被隐藏，必须由桌面壳来告诉后端。"""
    last = None
    while True:
        try:
            h = find_hwnd(PET_TITLE)
            vis = bool(h) and bool(user32.IsWindowVisible(h))
            if vis != last:
                last = vis
                body = json.dumps({"visible": vis}).encode()
                req = urllib.request.Request(
                    BASE + "/api/pet_state", data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3).read()
                note("上报宠物窗可见性: %s" % vis)
        except Exception:
            pass
        time.sleep(2.0)


class RectPoller(threading.Thread):
    """从后端取"页面语音控件坐标"，同时给后端发心跳（告诉页面"壳在跑"）。

    ★ 为什么绕后端一圈、而不是让页面直接调壳的方法（pywebview JS 桥）：
      JS 桥是**同步回环**调用，要在壳的主线程上执行。页面按固定频率推坐标时会把
      WebView2 的 UI 线程堵死 —— 实测表现就是"桌宠又卡死"：窗口变成 "(未响应)"
      （IsHungAppWindow=True），并连带让 dwm 生成 Ghost 替身窗口，把该区域的所有
      点击全部吃掉（用户看到的就是"卡死 + 点不动"）。
      改成本地 HTTP 后，坐标采集与壳的窗口消息循环彻底解耦，这条卡死路径被移除；
      而且页面的坐标一旦变化，这里 0.5s 内必拿到，不会再有过时坐标。
    """

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self._warn_at = 0.0
        self._first = True

    def _get(self, path, timeout=2.0):
        import urllib.request
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _beat(self):
        import urllib.request
        req = urllib.request.Request(BASE + "/api/shell/beat", data=b"{}",
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2.0).read()

    def run(self):
        while not self.app.quitting:
            try:
                j = self._get("/api/ui/rects")
                age = j.get("age")
                mic = j.get("mic")
                # age<3s 才算新鲜：太旧的坐标宁可不用，也不能拿它去盖点击层
                if mic and age is not None and 0 <= age < 3.0:
                    vw = j.get("vw") or 0
                    vh = j.get("vh") or 0
                    self.app.mic_rect = (mic[0], mic[1], mic[2], mic[3], vw, vh)
                    # 用"上报时刻"而不是"收到时刻"，日志里的新鲜度才是真实值
                    self.app.mic_rect_ts = time.time() - float(age)
                    mode = j.get("mode")
                    if mode:
                        self.app.mode_rect = (mode[0], mode[1], mode[2], mode[3], vw, vh)
                        self.app.mode_rect_ts = self.app.mic_rect_ts
                    if self._first:
                        self._first = False
                        note("已从后端取得控件坐标 mic=(%.0f,%.0f,%.0f) mode=%s"
                             % (mic[0], mic[1], mic[2],
                                ("(%.0f,%.0f,%.0f)" % (mode[0], mode[1], mode[2]))
                                if mode else "无"))
            except Exception as e:
                now = time.time()
                if now - self._warn_at > 30:      # 别刷屏
                    self._warn_at = now
                    note("取控件坐标失败（后端没起？）: %r" % (e,))
            try:
                self._beat()
            except Exception:
                pass
            time.sleep(0.5)


class CursorPoller(threading.Thread):
    """20Hz 轮询全局光标位置 + 宠物窗位置，POST 给后端，供页面做"眼神追随"。

    ★ 为什么不用 MouseHook（WH_MOUSE_LL 系统级鼠标钩子）：
      钩子回调跑在 Python 线程里，一旦被 GIL 或长任务拖住，系统的鼠标输入会被
      一起阻塞 —— 实测整个系统鼠标卡住，所以前人已禁用 MouseHook（见 run 里的注释）。
      而 GetCursorPos 只是普通 Win32 API 读取，不注册回调、不拦截输入，
      系统鼠标该多快还是多快，两者风险完全不同。
    ★ 为什么绕后端而不是 pywebview JS 桥：与 RectPoller 同因，JS 桥是同步回环，
      高频推坐标会堵死 WebView2 UI 线程（实测桌宠卡死 + Ghost 窗）。
    """

    HZ = 20.0                 # 眼神追随的采样率：跟手足够，又不至于抢 GIL
    MIN_STEP = 3              # 光标移动小于 3px 视为没动，省一次 POST

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self._warn_at = 0.0
        self._hwnd = None                        # 宠物窗句柄缓存（见 run 里的注释）
        self._first = True
        self._last = (-1, -1, -1, -1, -1, -1)   # 上一次发出去的 (x, y, l, t, r, b)

    def _post(self, body, timeout=0.4):
        import urllib.request
        req = urllib.request.Request(BASE + "/api/gaze/cursor",
                                     data=json.dumps(body).encode("utf-8"),
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=timeout).read()

    def run(self):
        while not self.app.quitting:
            try:
                # 句柄缓存：find_hwnd 是 EnumWindows 全量枚举（几百个顶层窗口），
                # 20Hz 反复调等于每秒几千次 Win32 调用，白烧 CPU 还抢 GIL。
                # 缓存 + IsWindow 校验，只有窗口重建（重载/换形象）时才重找一次。
                h = self._hwnd
                if not h or not user32.IsWindow(h):
                    h = find_hwnd(PET_TITLE)
                    self._hwnd = h
                if not h:
                    time.sleep(0.5)
                    continue
                pt = wintypes.POINT()
                if not user32.GetCursorPos(ctypes.byref(pt)):
                    time.sleep(1.0 / self.HZ)
                    continue
                r = wintypes.RECT()
                if not user32.GetWindowRect(h, ctypes.byref(r)):
                    time.sleep(1.0 / self.HZ)
                    continue
                cur = (pt.x, pt.y, r.left, r.top, r.right, r.bottom)
                if (abs(cur[0] - self._last[0]) < self.MIN_STEP
                        and abs(cur[1] - self._last[1]) < self.MIN_STEP
                        and cur[2:] == self._last[2:]):
                    time.sleep(1.0 / self.HZ)
                    continue
                self._last = cur
                self._post({"x": cur[0], "y": cur[1],
                            "rect": [cur[2], cur[3], cur[4], cur[5]]})
                if self._first:
                    self._first = False
                    note("眼神数据通路已建立：光标(%d,%d) 窗口%s"
                         % (cur[0], cur[1], (cur[2], cur[3], cur[4], cur[5])))
            except Exception as e:
                now = time.time()
                if now - self._warn_at > 30:      # 旁路功能，失败绝不影响主链；也别刷屏
                    self._warn_at = now
                    note("上报光标失败（后端没起？）: %r" % (e,))
            time.sleep(1.0 / self.HZ)


def catcher_watchdog(app):
    """兜底：定期探测捕获层是否还在响应消息。
    万一它卡住，它会吞掉她身上那一块的鼠标输入，用户体感就是卡死。
    连续两次无响应就直接撤掉它——宁可暂时点不动，也不能让鼠标被吞。"""
    SMTO_ABORTIFHUNG = 0x0002
    while True:
        time.sleep(2.0)
        try:
            c = getattr(app, "catcher", None)
            if not c or not c.hwnd or getattr(c, "_stop", False):
                pass
            else:
                res = ctypes.c_size_t()
                ok = user32.SendMessageTimeoutW(c.hwnd, 0x0000, 0, 0,
                                                SMTO_ABORTIFHUNG, 800, ctypes.byref(res))
                if ok:
                    c._hang_count = 0
                else:
                    c._hang_count = getattr(c, "_hang_count", 0) + 1
                    if c._hang_count >= 2:
                        note("捕获层连续无响应，已撤掉（避免吞掉鼠标输入）")
                        user32.DestroyWindow(c.hwnd)
                        c.hwnd = None
        except Exception:
            pass


# ------------------------------------------- 全局热键（安全，不拦截鼠标）
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
HOTKEY_ID = 0xA1
HOTKEY_VOICE_ID = 0xA2      # Ctrl+Alt+V 语音对话开关

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


class HotkeyThread(threading.Thread):
    """Ctrl+Alt+X 打开聊天窗；Ctrl+Alt+V 开始/结束说话。
    RegisterHotKey 只是让系统通知我们按键，不拦截输入，不会像鼠标钩子那样卡住系统。

    ★ 为什么不用 GetMessageW 阻塞式取消息：语音热键常常在开机时被别的程序先占用
      （注册失败就静默失效，用户体感"快捷键偶尔才生效"）。改成"取消息 + 定时补注册"
      的循环后，同一个线程里既能持续重试注册，又能正常收 WM_HOTKEY ——
      必须同线程：RegisterHotKey(None,...) 把 WM_HOTKEY 投递到**注册它的那个线程**的
      消息队列，换线程注册就永远收不到。"""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.voice_ok = False

    def _register_chat(self):
        try:
            ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, ord('X'))
            note("全局热键 Ctrl+Alt+X 注册: %s" % bool(ok))
        except Exception as e:
            note("注册聊天热键异常: %r" % (e,))

    def _register_voice(self):
        """注册 Ctrl+Alt+V。失败时返回 False，由 run() 的循环定期重试。"""
        if self.voice_ok:
            return True
        try:
            ok = user32.RegisterHotKey(None, HOTKEY_VOICE_ID, MOD_CONTROL | MOD_ALT, ord('V'))
            if ok:
                self.voice_ok = True
                note("全局热键 Ctrl+Alt+V 注册成功（开始/结束说话）")
            else:
                note("全局热键 Ctrl+Alt+V 注册失败（可能被其它程序占用），将自动重试")
            return bool(ok)
        except Exception as e:
            note("注册语音热键异常: %r" % (e,))
            return False

    def run(self):
        try:
            self._register_chat()
            self._register_voice()
            msg = wintypes.MSG()
            last_try = time.time()
            while True:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    try:
                        if msg.message == WM_HOTKEY:
                            if msg.wParam == HOTKEY_VOICE_ID:
                                note("热键 Ctrl+Alt+V 触发")
                                threading.Thread(target=self.app.voice_toggle,
                                                 daemon=True).start()
                            else:
                                note("热键 Ctrl+Alt+X 触发")
                                self.app.show_chat()
                    finally:
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                # 语音热键没注册上就每 20 秒在本线程内补注册
                if not self.voice_ok and time.time() - last_try > 20:
                    last_try = time.time()
                    self._register_voice()
                time.sleep(0.05)
        except Exception as e:
            note("热键线程异常: %r" % (e,))


# ------------------------------------------------------- 等待后端就绪
def wait_backend(timeout=60):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ------------------------------------------------------------- 主程序
class Api:
    """暴露给页面的方法：window.pywebview.api.xxx()
    注意：内部引用必须用下划线开头的私有属性，否则 pywebview 反射暴露 JS 接口时
    会顺着 app -> Window -> .NET 窗体对象一路递归下去并报错。"""

    def __init__(self, app):
        self._app = app

    def report_mic_rect(self, x, y, w, h, vw, vh):
        """页面把麦克风按钮的真实位置报给壳（CSS 像素，相对视口）。
        透明桌面模式下 WebView2 收不到鼠标事件，桌面上的点击层必须由壳盖，
        而按钮位置受 DPI 缩放影响，壳猜不准 —— 让页面自己报最精确。"""
        try:
            r = (float(x), float(y), float(w), float(h), float(vw), float(vh))
            old = getattr(self._app, "mic_rect", None)
            self._app.mic_rect = r
            self._app.mic_rect_ts = time.time()
            # 位置明显变化才记日志，避免拖窗时刷屏
            if old is None or abs(old[0] - r[0]) > 2 or abs(old[1] - r[1]) > 2:
                note("页面上报麦克风按钮位置 x=%.0f y=%.0f 尺寸=%.0f 视口=%.0fx%.0f"
                     % (r[0], r[1], r[2], r[4], r[5]))
        except Exception:
            pass
        return True

    def report_mode_rect(self, x, y, w, h, vw, vh):
        """页面把「🎙 模式切换」标签的位置报给壳，供壳盖点击层（原因同麦克风）。"""
        try:
            r = (float(x), float(y), float(w), float(h), float(vw), float(vh))
            old = getattr(self._app, "mode_rect", None)
            self._app.mode_rect = r
            self._app.mode_rect_ts = time.time()
            if old is None or abs(old[0] - r[0]) > 2 or abs(old[1] - r[1]) > 2:
                note("页面上报模式标签位置 x=%.0f y=%.0f 尺寸=%.0fx%.0f"
                     % (r[0], r[1], r[2], r[3]))
        except Exception:
            pass
        return True

    def toggle_top(self):
        return self._app.toggle_top()

    def open_chat(self):
        self._app.show_chat()
        return True

    def hide_pet(self):
        self._app.hide_pet()
        return True

    def quit_app(self):
        self._app.quit()
        return True


class App:
    def __init__(self):
        self.pet = None
        self.chat = None
        self.tray = None
        self.top = True
        self.interactive = False      # False=透明桌面模式，True=互动模式
        self.quitting = False
        self.brain = "检测中…"        # 本地大脑状态，显示在托盘菜单
        self.voice_vad_on = False     # 连续对话是否开着
        self.mic_rect = None          # 页面报来的麦克风按钮位置 (x,y,w,h,vw,vh)
        self.mic_rect_ts = 0.0
        self.mode_rect = None         # 页面报来的「🎙 模式切换」标签位置
        self.mode_rect_ts = 0.0
        self.t0 = time.time()         # 进程启动时刻：用于"坐标还没就绪"的宽限期判断
        self.presets = []             # 人设列表（来自后端 /api/preset）
        self.preset_key = ""          # 当前生效的人设
        self.preset_label = ""
        self.voices = []              # 音色列表（来自后端 /api/voice-options）
        self.voice_id = ""            # 当前音色的 edge voice id
        self.voice_label = ""         # 当前音色的中文名
        self.models = []              # 人物模型列表（来自后端 /api/models）
        self.model_key = ""           # 当前生效的人物模型
        self.model_label = ""
        self.motions = []             # 动作列表（来自后端 /api/motions；首项固定=内置待机）
        self.motion_key = "idle"      # 当前动作（idle = 内置待机微动作）
        self.motion_label = ""
        self.gaze_mode = "always"     # 眼神追随：off / idle / always（后端持久化，见 /api/gaze）
        self.click_mode = "poke"      # 点击身体的行为：poke（戳一下，双击开窗）/ chat（老行为）
        self.poke_style = "soft"      # 点击手感幅度：soft（克制）/ vivid（明显）
        self.chat_models = []         # 对话模型列表（来自后端 /api/chat-model）
        self.chat_model = ""          # 当前对话模型 id
        self.chat_label = ""          # 当前对话模型的中文名
        self.chat_best = ""           # auto 模式下当前会走的模型（代理实时择优结果）

    # ---- 窗口 ----
    def toggle_interactive(self):
        """在「透明贴桌面」和「可点击/可拖动」之间切换。
        原因见文件头：WebView2 上这两者不能同时成立，只能让用户按需要切。"""
        self.interactive = not self.interactive
        if self.interactive:
            use_interactive(PET_TITLE)
            note("切到互动模式（可点击/可拖动）")
        else:
            use_transparent(PET_TITLE)
            set_topmost(PET_TITLE, True)
            note("切回透明模式")
        if self.tray:
            self.tray.update_menu()
        return self.interactive

    def toggle_top(self):
        self.top = not self.top
        set_topmost(PET_TITLE, self.top)
        if self.tray:
            self.tray.update_menu()
        return self.top

    def show_pet(self):
        try:
            self.pet.show()
            self.pet.restore()
            if self.top:
                threading.Timer(0.4, lambda: set_topmost(PET_TITLE, True)).start()
        except Exception as e:
            print("[show_pet]", e)

    def hide_pet(self):
        try:
            self.pet.hide()
        except Exception as e:
            print("[hide_pet]", e)

    def show_chat(self):
        try:
            self.chat.show()
            self.chat.restore()
            note("聊天窗已打开")
        except Exception as e:
            # pythonw 下 print 看不到，必须写进事件日志，否则开窗失败无人知晓
            note("打开聊天窗失败: %r" % (e,))

    def open_tuner(self):
        """右键「调参」：开一个独立小窗（Chrome/Edge app 窗口）拖亮度/饱和度。

        为什么是独立窗口而不是宠物窗里的面板：宠物窗是透明 + 点击穿透的隐形层，
        面板放进去点不动。调参窗只发参数，桌面上的她靠 /api/grade 轮询，
        1 秒内跟着变色 —— 不重载模型、不打断正在跳的舞、不碰动作库。
        """
        from urllib.parse import quote
        url = BASE + "/pmxtune?model=" + quote(self.model_key or "")
        chrome = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        exe = next((p for p in chrome if os.path.isfile(p)), None)
        args = ["--app=" + url, "--window-size=330,300", "--window-position=60,60",
                "--user-data-dir=" + os.path.join(os.environ.get("LOCALAPPDATA", "."), "PetTune"),
                "--no-first-run", "--no-default-browser-check"]
        try:
            if exe:
                subprocess.Popen([exe] + args, close_fds=True)
            else:
                os.startfile(url)      # 没有 Chrome/Edge：至少用默认浏览器打开
            note("调参窗已打开: %s（形象=%s）" % (url, self.model_key or "-"))
        except Exception as e:
            note("打开调参窗失败: %r" % (e,))

    def reset_memory(self):
        import urllib.request
        try:
            urllib.request.urlopen(urllib.request.Request(
                BASE + "/api/reset", data=b"{}", method="POST",
                headers={"Content-Type": "application/json"}), timeout=5)
        except Exception as e:
            print("[reset]", e)

    # ---- 语音对话（走后端 /api/voice/*，实际收音转写在 asr_service.py）----
    def _voice_post(self, path, obj=None):
        import urllib.request
        data = json.dumps(obj or {}).encode()
        req = urllib.request.Request(BASE + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def _voice_get(self, path):
        import urllib.request
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def voice_toggle(self):
        """点桌面麦克风按钮 / 按 Ctrl+Alt+V：没在收音就开始，正在收音就结束。
        模式以后端当前设置为准（默认连续对话：说完停顿自动识别，不用再点第二次）。
        转写中/等待取走时只提示不动作，避免撞上 409（服务在识别时拒绝重开）。"""
        # 去重：同一个物理点击偶尔会被两个入口同时收到（网页按钮 + 壳的点击层），
        # 会"开完立刻关"，体感就是点了没反应。800ms 内的重复触发只认第一次。
        now = time.time()
        if now - getattr(self, "_last_toggle", 0.0) < 0.8:
            note("忽略重复触发（距上次仅 %.0f ms）" % ((now - self._last_toggle) * 1000))
            return
        self._last_toggle = now
        try:
            st = self._voice_get("/api/voice/state")
            mode = st.get("mode") or "vad"
            self.voice_vad_on = (mode == "vad")
            stt = st.get("state")
            phase = st.get("phase") or ""
            note("麦克风触发：state=%s phase=%s mode=%s 在收音=%s"
                 % (stt, phase, mode, st.get("recording")))
            # ★ 后端已经接管一轮（等模型响应 / 等播放 / 正在说）时直接挡回去。
            #   以前这段时间在界面上完全没有反馈，用户会反复点麦克风，把时序搅乱。
            if phase in ("waiting_model", "waiting_play", "playing"):
                note("她正在%s，等她说完再点" % ("想" if phase == "waiting_model" else "说"))
                return
            if st.get("recording") or stt == "listening":
                self._voice_post("/api/voice/stop")
                note("正在识别你刚才说的话…" if mode == "vad" else "已结束，正在识别…")
            elif stt in ("thinking", "pending"):
                note("正在识别上一句，稍等一下")
            else:   # off / paused -> 开始
                j = self._voice_post("/api/voice/start", {"mode": mode})
                if j.get("ok"):
                    note("开始听你说话" + ("，说完停一下就行" if mode == "vad"
                                          else "，说完再点一次"))
                else:
                    note("语音启动失败: %s" % j.get("error"))
        except Exception as e:
            note("语音控制失败（asr_service 没起来？）: %s" % e)

    def voice_mode_toggle(self):
        """点「🎙 连续对话 / 🎤 按键说话」标签：在两种模式间切换。
        网页里这个标签在透明模式下点不到（WebView2 收不到鼠标事件），
        所以由壳的隐形点击层代劳。"""
        now = time.time()
        if now - getattr(self, "_last_mode_toggle", 0.0) < 0.8:
            note("忽略重复的模式切换")
            return
        self._last_mode_toggle = now
        try:
            st = self._voice_get("/api/voice/state")
            cur = st.get("mode") or "vad"
            nxt = "ptt" if cur == "vad" else "vad"
            j = self._voice_post("/api/voice/mode", {"mode": nxt})
            if j.get("ok"):
                self.voice_vad_on = (nxt == "vad")
                note("模式已切换：%s -> %s（%s）"
                     % (cur, nxt, "连续对话" if nxt == "vad" else "按键说话"))
            else:
                note("模式切换失败: %s" % j.get("error"))
        except Exception as e:
            note("模式切换失败（asr_service 没起来？）: %s" % e)

    def voice_ptt(self):
        """按键说一句：开始收音 -> 立刻提示 -> 由托盘菜单再点一次结束。
        这里做成"开始"，结束用「停止说话 / 取消收音」。"""
        try:
            j = self._voice_post("/api/voice/start", {"mode": "ptt"})
            note("开始听你说话…说完点「停止说话」" if j.get("ok") else "语音启动失败: %s" % j.get("error"))
        except Exception as e:
            note("语音启动失败（asr_service 没起来？）: %s" % e)

    def voice_toggle_vad(self):
        try:
            if not self.voice_vad_on:
                self._voice_post("/api/voice/mode", {"mode": "vad"})
                j = self._voice_post("/api/voice/start", {"mode": "vad"})
                if j.get("ok"):
                    self.voice_vad_on = True
                    note("连续对话已开启：直接说话，停顿一下她就会回你")
                else:
                    note("连续对话启动失败: %s" % j.get("error"))
            else:
                self._voice_post("/api/voice/cancel")
                self.voice_vad_on = False
                note("连续对话已关闭")
        except Exception as e:
            note("语音控制失败: %s" % e)

    def voice_cancel(self):
        try:
            self._voice_post("/api/voice/cancel")
            self.voice_vad_on = False
            note("已停止收音")
        except Exception as e:
            note("停止收音失败: %s" % e)

    # ---- 托盘 ----
    def rebuild_menu(self):
        """整份重建托盘菜单。pystray 的 update_menu() 只刷新文字/勾选/可见性，
        **不会重建子菜单里的子项**——新增人设/音色/模型后必须整份重建，
        否则菜单里永远是最初那几项（这就是"菜单里少东西"的常见成因）。"""
        if not self.tray:
            return
        try:
            self.tray.menu = self.build_menu()
            self.tray.update_menu()
        except Exception as e:
            diag("重建托盘菜单失败: %r" % (e,))

    def _safe_items(self, builder, name):
        """构建子菜单项；失败时留一个说明项并写诊断，避免整份托盘菜单建不出来。
        诊断行会记下"每个子菜单实际有几项"——这是定位"菜单里少了一项"的唯一依据。"""
        try:
            items = builder()
            diag("子菜单 | %-10s 项数=%d" % (name, len(items)))
            return items
        except Exception:
            import traceback
            diag("子菜单 | %-10s 构建异常: %s"
                 % (name, traceback.format_exc().replace("\n", " ⏎ ")))
            return [pystray.MenuItem("（%s 构建失败，详见 托盘菜单诊断.log）" % name,
                                     lambda i, it: None, enabled=False)]

    def build_menu(self):
        diag("build_menu | 人设=%d 音色=%d 对话模型=%d 人物模型=%d | 当前: 人设=%s 音色=%s 对话=%s(%s) 形象=%s"
             % (len(self.presets), len(self.voices), len(self.chat_models), len(self.models),
                self.preset_key or "-", self.voice_id or "-",
                self.chat_label or "-", self.chat_model or "-", self.model_key or "-"))
        return pystray.Menu(
            pystray.MenuItem(lambda item: "对话大脑: " + (self.chat_label or "检测中…")
                             + " · " + self.brain,
                             lambda i, it: None, enabled=False),
            pystray.MenuItem(lambda item: "人设: " + (self.preset_label or "未设置"),
                             lambda i, it: None, enabled=False),
            pystray.MenuItem(lambda item: "音色: " + (self.voice_label or "未设置"),
                             lambda i, it: None, enabled=False),
            pystray.MenuItem(lambda item: "动作: " + (self.motion_label or "内置待机"),
                             lambda i, it: None, enabled=False),
            pystray.MenuItem("重启本地大脑（不回话时点这个）",
                             lambda i, it: threading.Thread(
                                 target=self.restart_brain, daemon=True).start()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("显示 / 隐藏念念", lambda i, it: self.toggle_show(), default=True),
            pystray.MenuItem("打开聊天记录", lambda i, it: self.show_chat(), default=False),
            pystray.Menu.SEPARATOR,
            # ★ 人设 / 声线：一套人设 = 人格文本 + 声线，切换后热生效（不用重启任何服务）
            pystray.MenuItem("人设 / 声线（切换人格）",
                             pystray.Menu(*self._safe_items(self._preset_items, "人设/声线"))),
            # ★ 音色：只换"谁在说话"，不动人格；选择会持久化，重启后仍生效
            pystray.MenuItem("音色（切换声音）",
                             pystray.Menu(*self._safe_items(self._voice_items, "音色"))),
            # ★ 对话模型：切"谁在想事情"（or_proxy 上游）；点了先探活再落盘，下一句即生效
            pystray.MenuItem("对话模型（切换大脑）",
                             pystray.Menu(*self._safe_items(self._chat_items, "对话模型"))),
            # ★ 人物模型：桌面形象切换（和她身上点右键是同一份数据、同一个动作）
            pystray.MenuItem("人物模型（切换形象）",
                             pystray.Menu(*self._safe_items(self._model_items, "人物模型"))),
            # ★ 调参：只动观感（亮度/饱和度/对比度/伽马），拖完自动存进该形象的 model.json
            pystray.MenuItem("调参（亮度 / 饱和度）",
                             lambda i, it: threading.Thread(target=self.open_tuner, daemon=True).start()),
            # ★ 动作：VMD 舞蹈 / 内置待机（模之屋动作库，assets/motions/）
            pystray.MenuItem("动作（切换舞步）",
                             pystray.Menu(*self._safe_items(self._motion_items, "动作"))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🎤 语音对话：按键说一句（或按 Ctrl+Alt+V）",
                             lambda i, it: self.voice_toggle()),
            pystray.MenuItem("🎙 语音对话：连续对话（开关）",
                             lambda i, it: self.voice_toggle_vad(),
                             checked=lambda item: self.voice_vad_on),
            pystray.MenuItem("停止说话 / 取消收音",
                             lambda i, it: self.voice_cancel()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("互动模式（可拖动/可点击）",
                             lambda i, it: self.toggle_interactive(),
                             checked=lambda item: self.interactive),
            pystray.MenuItem("窗口置顶", lambda i, it: self.toggle_top(), checked=lambda item: self.top),
            pystray.MenuItem("回到右下角（找不到她时）", lambda i, it: self.reset_position()),
            pystray.MenuItem("清空对话记忆", lambda i, it: self.reset_memory()),
            pystray.MenuItem("打开日志文件夹（排查问题）",
                             lambda i, it: threading.Thread(target=open_log_dir, daemon=True).start()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda i, it: self.quit()),
        )

    # ---- 人设（人格 + 声线）切换 ----
    def _preset_items(self):
        """把后端的 人设/*.json 列表做成托盘子菜单项（radio 勾选当前生效的那套）。"""
        items = []
        for p in self.presets:
            k = p.get("key") or ""
            items.append(pystray.MenuItem(
                p.get("label") or k,
                (lambda kk: (lambda i, it: self.set_preset(kk)))(k),
                checked=(lambda kk: (lambda item: self.preset_key == kk))(k)))
        if not items:
            items.append(pystray.MenuItem("（还没取到人设列表，后端没起？）",
                                          lambda i, it: None, enabled=False))
        return items

    def refresh_presets(self):
        try:
            j = self._voice_get("/api/preset")
            self.presets = j.get("presets") or []
            self.preset_key = j.get("current") or ""
            self.preset_label = j.get("label") or ""
            return True
        except Exception as e:
            diag("刷新失败 | /api/preset: %r" % (e,))
            return False

    def set_preset(self, key):
        """点托盘菜单切换人设：人格与声线一起换，立即生效，不需要重启。"""
        try:
            j = self._voice_post("/api/preset", {"key": key})
            note("切换人设 -> %s（引擎 %s 音色 %s）"
                 % (j.get("label"), j.get("engine"), j.get("voice")))
        except Exception as e:
            note("切换人设失败: %r" % (e,))
        self.refresh_presets()
        self.rebuild_menu()          # 勾选立刻反映（子项也要重建）

    # ---- 音色（只换声音，不动人格）----
    def _voice_items(self):
        """后端 /api/voice-options 的清单做成 radio 子菜单，勾选当前音色。"""
        items = []
        for o in self.voices:
            vid = o.get("id") or ""
            if not vid:
                continue
            label = o.get("label") or vid
            note_ = o.get("note") or ""
            if note_:
                label = "%s —— %s" % (label, note_)
            items.append(pystray.MenuItem(
                label,
                (lambda vv: (lambda i, it: self.set_voice(vv)))(vid),
                checked=(lambda vv: (lambda item: self.voice_id == vv))(vid)))
        if not items:
            items.append(pystray.MenuItem("（没取到音色列表，后端没起？）",
                                          lambda i, it: None, enabled=False))
        return items

    def refresh_voices(self):
        try:
            j = self._voice_get("/api/voice-options")
            self.voices = j.get("options") or []
            self.voice_id = j.get("current") or ""
            self.voice_label = ""
            for o in self.voices:
                if o.get("id") == self.voice_id:
                    self.voice_label = o.get("label") or self.voice_id
                    break
            if not self.voice_label:
                self.voice_label = self.voice_id
            return True
        except Exception as e:
            diag("刷新失败 | /api/voice-options: %r" % (e,))
            return False

    def set_voice(self, voice_id):
        """点托盘菜单切音色：热生效（下一次合成就用新声音），并写入持久化文件。
        ★ GPT-SoVITS 是**动态加载**的（2026-10-19）：切到 gsv: 音色时后端才去拉起，
          首次加载模型 30~60s。这里只提示"正在加载"，不等它——界面不能卡住。
          加载期间她说话自动回退 edge-tts，听着正常，只是音色还没换过来。"""
        try:
            j = self._voice_post("/api/voice-options", {"voice": voice_id})
            if (j.get("engine") or "") == "gptsovits":
                st = j.get("gsv_state") or ""
                detail = ("GPT-SoVITS 已就绪" if st == "up" else
                          "GPT-SoVITS 加载中（首次约 30~60 秒，稍等她再开口就是本地音色）"
                          if st == "booting" else
                          "GPT-SoVITS 本地声线（加载中，先用 edge-tts 出声）")
            else:
                detail = "%s %s" % (j.get("rate"), j.get("pitch"))
            note("切换音色 -> %s（%s）" % (j.get("label"), detail))
        except Exception as e:
            note("切换音色失败: %r" % (e,))
        self.refresh_voices()
        self.rebuild_menu()

    # ---- 对话模型（切换大脑）----
    def _chat_items(self):
        """后端 /api/chat-model 的清单做成 radio 子菜单，勾选当前在用的模型。"""
        items = []
        for m in self.chat_models:
            mid = m.get("id") or ""
            items.append(pystray.MenuItem(
                m.get("label") or mid,
                (lambda mm: (lambda i, it: self.set_chat_model(mm)))(mid),
                checked=(lambda mm: (lambda item: self.chat_model == mm))(mid)))
        if not items:
            items.append(pystray.MenuItem("（还没取到模型列表，后端没起？）",
                                          lambda i, it: None, enabled=False))
        return items

    def refresh_chat_models(self):
        try:
            j = self._voice_get("/api/chat-model")
            self.chat_models = j.get("options") or []
            self.chat_model = j.get("current") or "auto"
            self.chat_label = j.get("label") or self.chat_model
            self.chat_best = j.get("best_now") or ""
            return True
        except Exception as e:
            diag("刷新失败 | /api/chat-model: %r" % (e,))
            return False

    def set_chat_model(self, mid):
        """点托盘切换对话模型：后端先真打一次最小请求验证（防止代理静默回退到
        别的模型却不告诉你），验证通过才写 config.json；下一句对话即生效，
        不用重启后端、不用换血代理。"""
        try:
            j = self._voice_post("/api/chat-model", {"model": mid})
        except Exception as e:
            detail = ""
            try:                       # HTTPError 的 body 里有后端给的中文原因
                detail = (json.loads(e.read().decode("utf-8")) or {}).get("error") or ""
            except Exception:
                detail = repr(e)
            note("切换对话模型失败: %s" % detail)
            diag("切换对话模型失败 | %s -> %s" % (mid, detail))
            return False
        if not j.get("ok"):
            note("切换对话模型失败: %s" % (j.get("error") or "未知原因"))
            diag("切换对话模型失败 | %s -> %s" % (mid, j.get("error")))
            return False
        diag("切换对话模型成功 | %s -> %s（实际应答 %s，%.2fs）"
             % (mid, j.get("label"), j.get("used") or "?", float(j.get("latency") or 0)))
        if j.get("warning"):
            note("对话模型 -> %s（%s）" % (j.get("label"), j["warning"]))
        else:
            note("对话模型 -> %s（实际应答 %s，%.2fs）"
                 % (j.get("label"), j.get("used") or "?", float(j.get("latency") or 0)))
        if self.refresh_chat_models():
            self.rebuild_menu()
        return True

    # ---- 人物模型（桌面形象切换）----
    def refresh_models(self):
        """从后端取人物模型清单和当前选择（右键菜单/托盘子菜单的数据源）。"""
        try:
            j = self._voice_get("/api/models")
            self.models = j.get("models") or []
            self.model_key = j.get("current") or ""
            self.model_label = ""
            for m in self.models:
                if m.get("key") == self.model_key:
                    self.model_label = m.get("label") or self.model_key
                    break
            if not self.model_label:
                self.model_label = self.model_key
            return True
        except Exception as e:
            diag("刷新失败 | /api/models: %r" % (e,))
            return False

    def set_model(self, key):
        """切换人物模型：后端写持久化并热生效，宠物页轮询到变化后自动重载。"""
        try:
            j = self._voice_post("/api/model", {"key": key})
            note("切换人物模型 -> %s（%s）" % (j.get("label"), key))
        except Exception as e:
            note("切换人物模型失败: %r" % (e,))
        self.refresh_models()
        self.rebuild_menu()          # 勾选立刻反映（子项也要重建）

    def _model_items(self):
        """人物模型清单做成 radio 子菜单（托盘用；右键菜单由 Catcher 原生绘制）。"""
        items = []
        for m in self.models:
            k = m.get("key") or ""
            items.append(pystray.MenuItem(
                m.get("label") or k,
                (lambda kk: (lambda i, it: self.set_model(kk)))(k),
                checked=(lambda kk: (lambda item: self.model_key == kk))(k)))
        if not items:
            items.append(pystray.MenuItem("（还没取到模型列表，后端没起？）",
                                          lambda i, it: None, enabled=False))
        return items

    # ---- 动作（VMD 舞蹈 / 内置待机）----
    def refresh_motions(self):
        """从后端取动作清单和当前选择（托盘/右键菜单的数据源）。
        后端返回的 current 可能是 'idle'（内置待机），把它并进列表第 0 项统一做 radio。"""
        try:
            j = self._voice_get("/api/motions")
            idle = {"key": "idle", "label": j.get("idle_label") or "内置待机（呼吸/挥手）"}
            self.motions = [idle] + (j.get("motions") or [])
            self.motion_key = j.get("current") or "idle"
            self.motion_label = idle["label"]
            for m in self.motions:
                if m.get("key") == self.motion_key:
                    self.motion_label = m.get("label") or self.motion_key
                    break
            return True
        except Exception as e:
            diag("刷新失败 | /api/motions: %r" % (e,))
            return False

    def set_motion(self, key):
        """切换动作：后端写持久化，宠物页 2 秒内跟随（不重载模型）。"""
        try:
            j = self._voice_post("/api/motion", {"key": key})
            note("切换动作 -> %s" % (j.get("label") or key))
        except Exception as e:
            note("切换动作失败: %r" % (e,))
        self.refresh_motions()
        self.rebuild_menu()          # 勾选立刻反映（子项也要重建）

    # ---- 眼神追随模式 ----
    def refresh_gaze(self):
        """从后端读当前眼神模式（/api/gaze 顺路带 mode）。失败时保持原值。"""
        try:
            j = self._voice_get("/api/gaze")
            m = j.get("mode") or "always"
            self.gaze_mode = m if m in ("off", "idle", "always") else "always"
            return True
        except Exception as e:
            diag("刷新失败 | /api/gaze: %r" % (e,))
            return False

    def set_gaze_mode(self, mode):
        """切眼神模式：后端持久化，页面 150ms 内跟随（不重载模型、不打断动作）。"""
        try:
            j = self._voice_post("/api/gaze/config", {"mode": mode})
            note("眼神追随 -> %s" % (j.get("label") or mode))
        except Exception as e:
            note("切换眼神失败: %r" % (e,))
        self.refresh_gaze()
        self.rebuild_menu()

    # ---- 点击行为（单击戳一下 / 直接开聊天窗）+ 手感档（soft / vivid）----
    def refresh_click(self):
        """从后端读当前点击行为与手感档（/api/click）。失败时保持原值。

        两件事合一个请求读：它们同源、同频变化，没必要开两条轮询。
        """
        try:
            j = self._voice_get("/api/click")
            m = j.get("mode") or "poke"
            self.click_mode = m if m in ("poke", "chat") else "poke"
            s = j.get("style") or "soft"
            self.poke_style = s if s in ("soft", "vivid") else "soft"
            return True
        except Exception as e:
            diag("刷新失败 | /api/click: %r" % (e,))
            return False

    def set_click_mode(self, mode):
        """切点击行为：后端持久化。只影响壳的点击分发，页面无需感知。"""
        try:
            j = self._voice_post("/api/click/config", {"mode": mode})
            note("点击行为 -> %s" % (j.get("label") or mode))
        except Exception as e:
            note("切换点击行为失败: %r" % (e,))
        self.refresh_click()
        self.rebuild_menu()

    def set_poke_style(self, style):
        """切点击手感：后端持久化 → 页面下次轮询 /api/gaze 自动跟随（不需要重载）。"""
        try:
            j = self._voice_post("/api/click/config", {"style": style})
            note("点击手感 -> %s" % (j.get("style_label") or style))
        except Exception as e:
            note("切换点击手感失败: %r" % (e,))
        self.refresh_click()
        self.rebuild_menu()

    def _motion_items(self):
        """动作清单做成 radio 子菜单（托盘用）。"""
        items = []
        for m in self.motions:
            k = m.get("key") or ""
            label = m.get("label") or k
            note_ = m.get("note") or ""
            if note_:
                label = "%s —— %s" % (label, note_)
            items.append(pystray.MenuItem(
                label,
                (lambda kk: (lambda i, it: self.set_motion(kk)))(k),
                checked=(lambda kk: (lambda item: self.motion_key == kk))(k)))
        if not items:
            items.append(pystray.MenuItem("（还没取到动作列表，后端没起？）",
                                          lambda i, it: None, enabled=False))
        return items

    def preset_watch(self):
        """定期刷新人设 / 音色无关的动态列表：人设 + 人物模型。
        新加的人设文件或模型 20 秒内自动出现在托盘菜单里。"""
        last = None
        while not self.quitting:
            try:
                self.refresh_models()          # 模型清单也一起刷（右键菜单同源）
                self.refresh_motions()         # 动作清单（新放进 motions 目录的 VMD 自动出现）
                self.refresh_chat_models()     # 对话模型可能被后端/curl 改过，跟着刷
                self.refresh_gaze()            # 眼神模式同理（curl 改过也要反映到勾选）
                self.refresh_click()           # 点击行为同理
                if self.refresh_presets():
                    cur = (self.preset_key,
                           tuple(p.get("key") for p in self.presets),
                           self.model_key,
                           tuple(m.get("key") for m in self.models),
                           self.motion_key,
                           tuple(m.get("key") for m in self.motions),
                           self.chat_model,
                           tuple(m.get("id") for m in self.chat_models),
                           self.gaze_mode,
                           self.click_mode)
                    if cur != last:
                        diag("列表变化 -> 人设=%s 形象=%s 动作=%s 对话=%s(%s) | 人设%d项 音色%d项 对话%d项 形象%d项 动作%d项"
                             % (self.preset_key or "-", self.model_key or "-",
                                self.motion_key or "-", self.chat_model or "-",
                                self.chat_label or "-",
                                len(self.presets), len(self.voices),
                                len(self.chat_models), len(self.models),
                                len(self.motions)))
                        last = cur
                        self.rebuild_menu()
            except Exception as e:
                diag("preset_watch 异常: %r" % (e,))
            time.sleep(20)

    # ---- 对话大脑（or_proxy/OpenRouter）状态与重启 ----
    def _probe_brain(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=3) as r:
                return "在线" if r.status == 200 else "异常响应"
        except Exception:
            return "未运行"

    def brain_watch(self):
        """定时探测本地大脑状态，显示在托盘菜单上。"""
        last = None
        while True:
            self.brain = self._probe_brain()
            if self.brain != last and self.tray:
                last = self.brain
                try:
                    self.tray.update_menu()
                except Exception:
                    pass
            time.sleep(8)

    def restart_brain(self):
        """重启对话大脑：拉起桌面「启动OpenRouter代理.bat」（or_proxy, 8787）。"""
        note("用户请求重启对话大脑（or_proxy）")
        try:
            bat = r"C:\Users\Administrator\Desktop\启动OpenRouter代理.bat"
            if os.path.exists(bat):
                os.startfile(bat)     # 打开独立控制台窗口运行，关窗即停止（与脚本设计一致）
            else:
                # 桌面脚本不在时兜底：直接后台拉起 or_proxy
                subprocess.Popen([sys.executable, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'or_proxy', 'or_proxy.py'))],
                                 cwd=os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)), creationflags=0x08000000)
            for _ in range(20):
                time.sleep(2)
                if "在线" in self._probe_brain():
                    break
            self.brain = self._probe_brain()
            if self.tray:
                self.tray.update_menu()
            return True
        except Exception as e:
            note("重启对话大脑失败: %r" % (e,))
            return False

    def reset_position(self):
        """兜底：万一被拖到看不见的地方，把窗口放回工作区右下角。
        这里只用于「主动找回」，不影响平时自由拖动。"""
        h = find_hwnd(PET_TITLE)
        if not h:
            return False
        wl, wt, wr, wb = get_work_area()
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        w, hh = r.right - r.left, r.bottom - r.top
        x = max(wl, wr - w - 16)
        y = max(wt, wb - hh - 16)
        user32.SetWindowPos(h, HWND_TOPMOST, x, y, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)
        if getattr(self, "catcher", None):
            self.catcher.sync()
        note("已把窗口放回右下角: (%d,%d)" % (x, y))
        return True

    def toggle_show(self):
        h = find_hwnd(PET_TITLE)
        if h:
            self.hide_pet()
        else:
            self.show_pet()

    def run_tray(self):
        try:
            icon_img = Image.open(ICON_PATH)
            self.tray = pystray.Icon("xiaoyun", icon_img, "念念 · 本地AI女友", self.build_menu())
            diag("托盘图标已创建（菜单构建完成）")
            self.tray.run()
        except Exception:
            # 托盘异常不影响桌面窗口本身继续运行；但必须留证据（pythonw 没有 stdout）
            import traceback
            diag("托盘创建/运行失败: %s" % traceback.format_exc().replace("\n", " ⏎ "))
            print("[tray error]", traceback.format_exc())

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        try:
            if getattr(self, "catcher", None) and self.catcher.hwnd:
                user32.DestroyWindow(self.catcher.hwnd)   # 撤掉隐形点击层
        except Exception:
            pass
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        for w in (self.chat, self.pet):
            try:
                w.destroy()
            except Exception:
                pass
        # 兜底：托盘消息循环/子线程偶尔会吊住进程，导致退出后残留一个没有窗口的僵尸 pythonw，
        # 下次启动脚本就会误判"已经在运行"。这里强制收尾。
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except Exception:
            pass
        threading.Timer(1.5, lambda: os._exit(0)).start()

    # ---- 启动 ----
    def start(self):
        print("=== 小芸桌面（Windows 原生窗口）===")
        # ★ 关掉 WebView2 的自动播放限制：.NET/WebView2 默认要求"用户手势"才允许
        #   Audio.play()，桌宠的语音是后端主动推来的、没有手势上下文，实测大量
        #   NotAllowedError（日志里 18 次），页面随后把"被拦截"误当"播完了"，
        #   导致后端提前恢复收音 -> 录到她自己 -> 自循环。必须在装 WebView2 环境前设置。
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                              "--autoplay-policy=no-user-gesture-required")
        # 先落地 PID，让启动脚本能尽早判定“已起来”
        # （后面 import webview 会初始化 .NET 运行时，可能要十几秒）
        try:
            with open(PID_PATH, "w") as f:
                f.write(str(os.getpid()))
        except Exception as e:
            print("[warn] 写 PID 文件失败:", e)

        if not wait_backend():
            # 不要静默退出：弹一个说明窗口，至少让用户知道发生了什么
            print("[错误] 后端 8090 未就绪")
            err = (
                "<html><head><meta charset='utf-8'><style>"
                "body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                "font-family:'Microsoft YaHei',sans-serif;background:#fdeef2;color:#2a2230;text-align:center;}"
                "div{padding:24px;line-height:1.9;font-size:15px;}"
                "b{color:#d97891;}"
                "</style></head><body><div>"
                "念念的大脑还没准备好<br><br>"
                "后端服务（8090）没有响应。<br>"
                "请确认 Ollama 已启动，然后重新运行<br>"
                "<b>启动小芸桌面.bat</b><br><br>"
                "排查可双击：<b>诊断小芸桌面.bat</b>"
                "</div></body></html>"
            )
            webview.create_window(PET_TITLE, html=err, width=420, height=300,
                                  frameless=False, on_top=True)
            webview.start(icon=ICON_PATH, debug=False)
            return 1

        make_icon(ICON_PATH)

        wl, wt, wr, wb = get_work_area()
        vw, vh = wr - wl, wb - wt
        # 贴工作区右下角，并强制钳制在可见范围内（多屏/DPI 虚拟化时尤其重要）
        pet_x = wl + max(0, min(vw - PET_W - 16, vw - PET_W - 16))
        pet_y = wt + max(0, min(vh - PET_H - 16, vh - PET_H - 16))
        chat_x = wl + max(0, min(pet_x - wl - CHAT_W - 12, vw - CHAT_W - 16))
        chat_y = wt + max(0, min(vh - CHAT_H - 16, vh - CHAT_H - 16))

        api = Api(self)
        self.pet = webview.create_window(
            PET_TITLE, BASE + PET_PAGE,
            width=PET_W, height=PET_H, x=pet_x, y=pet_y,
            frameless=True, easy_drag=True, transparent=True,
            on_top=True, resizable=False, shadow=False,
            background_color="#000000", js_api=api,
        )
        self.chat = webview.create_window(
            CHAT_TITLE, BASE + "/chat",
            width=CHAT_W, height=CHAT_H, x=chat_x, y=chat_y,
            frameless=False, resizable=True, hidden=True, js_api=api,
        )

        # 聊天窗关闭 = 隐藏，不真的退出
        def on_chat_closing():
            try:
                self.chat.hide()
            except Exception:
                pass
            return False

        # 宠物窗关闭 = 整个程序退出
        def on_pet_closing():
            try:
                self.chat.destroy()
            except Exception:
                pass
            return True

        self.chat.events.closing += on_chat_closing
        self.pet.events.closing += on_pet_closing

        # 人设列表要先取到，托盘菜单构建时才有内容（后面 preset_watch 还会持续刷新）
        diag("===== 桌面壳启动 pid=%d 源码目录=%s =====" % (os.getpid(), ROOT))
        self.refresh_presets()
        self.refresh_voices()       # 音色清单同理：菜单构建前先拿到，才有勾选项
        self.refresh_chat_models()  # 对话模型清单：托盘「对话模型」子菜单的勾选项
        self.refresh_models()       # 人物模型清单：右键菜单与托盘菜单共用的数据源
        self.refresh_motions()      # 动作清单：托盘「动作（切换舞步）」子菜单的数据源
        self.refresh_gaze()         # 眼神模式：勾选要跟后端持久化的值一致（不是写死 always）
        self.refresh_click()        # 点击行为同理：否则重启后勾选回落到 poke，与文件不符
        diag("启动自检 | 人设=%d项 音色=%d项 对话模型=%d项 人物模型=%d项 动作=%d项"
             % (len(self.presets), len(self.voices), len(self.chat_models),
                len(self.models), len(self.motions)))
        threading.Thread(target=self.run_tray, daemon=True).start()
        threading.Thread(target=self.preset_watch, daemon=True).start()
        # 注意：不要启用系统级鼠标钩子（MouseHook）。
        # 实测它会让整个系统的鼠标一起卡住：低频鼠标钩子的回调跑在 Python 线程里，
        # 一旦被 GIL 或其它长任务拖住，系统的鼠标输入就会被一起阻塞。
        # 交互改为「托盘菜单 + 全局热键」，不碰鼠标钩子。
        self.hook_thread = None
        self.catcher = Catcher(self)
        self.catcher.start()          # 隐形点击层：透明也能点、能拖
        # 控件坐标 + 心跳：走本地 HTTP 从后端取（绝不用 pywebview 的 JS 桥，见 RectPoller）
        RectPoller(self).start()
        # 全局光标 + 窗口位置：供页面做眼神追随（纯 GetCursorPos 轮询，不用鼠标钩子）
        CursorPoller(self).start()
        # 语音控件（麦克风按钮 / 模式标签）的点击已并入捕获层，见 Catcher：
        # 独立的点击层小窗存在"被系统拒绝置顶"的坑，合并后不再需要。
        threading.Thread(target=catcher_watchdog, args=(self,), daemon=True).start()
        threading.Thread(target=self.brain_watch, daemon=True).start()
        threading.Thread(target=pet_visibility_watch, args=(self,), daemon=True).start()
        HotkeyThread(self).start()    # Ctrl+Alt+X 打开聊天窗

        # 窗口真正创建后再补一次置顶，并把它完整推回可视区
        def after_show():
            try:
                # 用截断代替删除：某些环境（含沙箱/权限策略）下删除文件会抛错
                open(STATUS_PATH, "w", encoding="utf-8").close()
            except Exception:
                pass

            # ① 等窗口真正出现。透明窗口是"先隐藏、Navigating 事件里才 Show"，
            #    所以固定在 1.2s 时去 find_hwnd 必然扑空（历史上每次都打印
            #    "第1次: 透明已应用=False 句柄=None"）。
            if not wait_hwnd(PET_TITLE, timeout=8.0):
                note("等待宠物窗出现超时(8s)，跳过透明设置")
                return

            # ② 再等 Show 之后那阵 DPI 校正/首帧布局的 SetWindowPos 停下来。
            #    ★ 这一步就是本次"启动卡死"的修复核心：在窗口刚 Show、WebView2
            #      刚开始初始化的那几十毫秒里跨线程改 WinForms 属性，会和 UI 线程
            #      互等（详见 run_guarded 的注释）。让开这个窗口期即可。
            wait_size_stable(PET_TITLE, need_rounds=3, poll=0.25, timeout=5.0)
            note("窗口已就绪(句柄=%s)，开始设置透明" % find_hwnd(PET_TITLE))

            # ③ 跨线程窗体操作一律带超时：真卡住了也只废掉一个 daemon 线程，
            #    启动流程照常往下走，不会再被启动器判定成"未响应"然后 kill。
            note("第1次: 开始设置透明…")
            r = run_guarded(lambda: use_transparent(PET_TITLE), 2.5, "use_transparent")
            painted = bool(r)
            note("第1次: 透明已应用=%s 句柄=%s" % (painted, find_hwnd(PET_TITLE)))

            if r is None:
                # 超时 = 已经和 UI 线程互锁了。此时 clamp/set_topmost 同样是跨线程
                # SendMessage，只会把窗口状态搅得更乱，索性什么都不做。
                note("透明设置超时，跳过收尾操作（不再向 UI 线程发任何请求）")
                return

            # 仅启动时校正一次初始位置：pywebview 的 x/y 会被 DPI 缩放，
            # 直接给右下角坐标会落到屏幕外。这不是运行期的边界限制，
            # 拖动她不受任何限制，可以随意放到屏幕任何位置。
            clamp_into_view(PET_TITLE)
            set_topmost(PET_TITLE, True)

            if painted:
                note("DONE 透明模式就绪=True")
                return

            # ④ 兜底：透明没设上时，用纯 Win32 色键（ctypes 调用会释放 GIL，
            #    不会再卡）抠掉窗口底色，至少不让她变成一块不透明的方块。
            h = find_hwnd(PET_TITLE)
            if h:
                user32.SetLayeredWindowAttributes(h, 0, 0, 0x1)
            note("DONE 透明模式就绪=False（已降级为纯 Win32 色键）")

        threading.Timer(1.2, after_show).start()

        webview.start(icon=ICON_PATH, debug=False)
        return 0


def _install_crash_hooks():
    """pythonw 没有控制台，未捕获异常会让进程静默消失——用户只看到窗口"卡死"然后不见，
    日志里什么都没有。把主线程与子线程的未捕获异常一律落到托盘菜单诊断.log，
    下次再出问题能直接看出是哪一层炸的。"""
    import traceback

    def _hook(exc_type, exc, tb):
        try:
            diag("FATAL uncaught %s: %s" % (
                exc_type.__name__,
                "".join(traceback.format_exception(exc_type, exc, tb)).replace("\n", " ⏎ ")))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        try:
            diag("FATAL thread %s: %s: %s" % (
                getattr(args.thread, "name", "?"), args.exc_type.__name__, args.exc_value))
        except Exception:
            pass

    sys.excepthook = _hook
    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass


def main():
    _install_crash_hooks()
    app = App()
    code = app.start()
    try:
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)
    except Exception:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
