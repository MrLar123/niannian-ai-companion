# -*- coding: utf-8 -*-
"""判断念念的宠物窗口是否真的显示在屏幕上，以及它是否「未响应」（卡死）。

用法：
    python pet_window_probe.py            # 检查标题为「念念」的窗口是否可见
    python pet_window_probe.py --json     # 输出 JSON: 可见/挂起/捕获层数量（脚本用）
    python pet_window_probe.py --list     # 列出指定 PID 的所有顶层窗口（排障用）
退出码：0 = 窗口可见；1 = 不可见/不存在。
--json 模式下退出码同 --json 是否"可见且未挂起"：0 = 健康，1 = 不可见或挂起。
"""
import ctypes
import ctypes.wintypes as wt
import json
import sys

user32 = ctypes.windll.user32
TITLE = "念念"
CATCHER = "catcher"

user32.FindWindowW.restype = wt.HWND
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsHungAppWindow.argtypes = [wt.HWND]
user32.IsHungAppWindow.restype = wt.BOOL


def find(title=TITLE):
    return user32.FindWindowW(None, title)


def is_visible(title=TITLE):
    h = find(title)
    return bool(h) and bool(user32.IsWindowVisible(h))


def is_hung(title=TITLE):
    """窗口是否「未响应」。没有窗口时返回 None。"""
    h = find(title)
    if not h:
        return None
    return bool(user32.IsHungAppWindow(h))


def count_windows(title):
    """统计某标题的可见顶层窗口数量（>1 说明存在重复实例）。"""
    n = 0

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        nonlocal n
        if not user32.IsWindowVisible(hwnd):
            return True
        ln = user32.GetWindowTextLengthW(hwnd)
        if ln <= 0:
            return True
        buf = ctypes.create_unicode_buffer(ln + 1)
        user32.GetWindowTextW(hwnd, buf, ln + 1)
        if buf.value == title:
            n += 1
        return True

    user32.EnumWindows(cb, 0)
    return n


def list_windows(pid):
    """枚举某个进程的全部顶层窗口（含隐藏窗口），返回 [(hwnd, 标题, 是否可见)]。"""
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append((hwnd, buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return out


if __name__ == "__main__":
    if "--list" in sys.argv:
        for pid in [int(x) for x in sys.argv[2:] if x.isdigit()]:
            print("PID", pid)
            for hwnd, title, vis in list_windows(pid):
                print("   hwnd=%-8s visible=%-5s title=%r" % (hwnd, vis, title))
        sys.exit(0)
    if "--json" in sys.argv:
        vis = is_visible()
        hung = is_hung()
        info = {
            "visible": vis,
            "hung": hung,
            "hwnd": int(find() or 0),
            "catcher_windows": count_windows(CATCHER),
            "pet_windows": count_windows(TITLE),
            "ghost": bool(find(TITLE + " (未响应)")),
        }
        print(json.dumps(info, ensure_ascii=False))
        sys.exit(0 if (vis and not hung) else 1)
    print("1" if is_visible() else "0")
    sys.exit(0 if is_visible() else 1)
