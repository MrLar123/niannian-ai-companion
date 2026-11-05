# -*- coding: utf-8 -*-
"""按窗口句柄抓桌面壳画面（分层透明窗口用 BitBlt 抓不到，必须 PrintWindow）。

背景：桌宠窗口是 WS_EX_LAYERED 的透明 pywebview 窗口。PIL.ImageGrab / BitBlt
只能拿到它下面的壁纸 —— 截图里"看不见桌宠"并不是窗口没出来，而是抓法不对。
这里用 PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)，对 WebView2 有效。

用法：
    python tools/pet_printwindow.py                  # 自动找窗口，存到 TEMP
    python tools/pet_printwindow.py out.png          # 指定输出
    python tools/pet_printwindow.py out.png 1380132  # 指定 hwnd
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

from PIL import Image

u = ctypes.windll.user32
g = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def find_pet_window():
    """找标题为「念念」的可见顶层窗口（与 pet_window_probe 同判据）。"""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if u.IsWindowVisible(hwnd) and ("念念" in buf.value or "小芸" in buf.value):
                found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else None


def grab(hwnd, path):
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise SystemExit("窗口尺寸为 0")

    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)          # PW_RENDERFULLCONTENT

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h                # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    got = g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)

    im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    im.save(path)
    return ok, got, (w, h), im


def main():
    args = sys.argv[1:]
    out = args[0] if args else os.path.join(os.environ["TEMP"], "xy_pet_win.png")
    hwnd = int(args[1]) if len(args) > 1 else find_pet_window()
    if not hwnd:
        raise SystemExit("找不到桌宠窗口（念念 / 小芸）")
    ok, got, size, im = grab(hwnd, out)
    # 判断画面是不是空的：全透明或几乎全同色
    ext = im.getextrema()
    print("hwnd=%s PrintWindow=%s GetDIBits=%s size=%sx%s" % (hwnd, ok, got, size[0], size[1]))
    print("alpha范围=%s  RGB极值=%s" % (ext[3], ext[:3]))
    print("->", out)


if __name__ == "__main__":
    main()
