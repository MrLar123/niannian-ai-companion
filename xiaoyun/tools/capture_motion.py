# -*- coding: utf-8 -*-
"""
抓念念桌面窗的真实像素（DPI 感知），连拍多帧并做：
  1) 轮廓质心漂移  -> 检查"幽灵感"（整体乱飘）
  2) 分区运动能量  -> 检查手臂 / 裙摆 / 躯干到底动没动、动多少
输出 PNG 到 tools/shots/，并把分析结果打印出来。
"""
import ctypes, zlib, struct, os, sys, time
from ctypes import wintypes

# 必须声明 DPI 感知，否则 125% 缩放下拿到的是虚拟坐标，抓出来的图会错位
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [('L', wintypes.LONG), ('T', wintypes.LONG), ('R', wintypes.LONG), ('B', wintypes.LONG)]


def grab(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.R - r.L, r.B - r.T
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32  # 负高 = 自上而下
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return w, h, buf.raw


def write_png(path, w, h, data):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw += data[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', comp) + chunk(b'IEND', b''))


def stats(w, h, data):
    """返回 (轮廓质心, 非空像素数)"""
    sx = sy = n = 0
    for y in range(0, h, 2):
        row = y * w * 4
        for x in range(0, w, 2):
            if data[row + x * 4 + 3] > 8:
                sx += x; sy += y; n += 1
    if not n:
        return None, 0
    return (sx / n, sy / n), n


def diff_grid(w, h, a, b, gy=6, gx=4):
    """两帧差分，按 gy×gx 网格统计运动像素占比"""
    grid = [[0] * gx for _ in range(gy)]
    total = 0
    for y in range(0, h, 2):
        gy_i = min(gy - 1, y * gy // h)
        row = y * w * 4
        for x in range(0, w, 2):
            i = row + x * 4
            aa, ba = a[i + 3], b[i + 3]
            if aa <= 8 and ba <= 8:
                continue
            if abs(aa - ba) > 40 or (abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2])) > 45:
                grid[gy_i][min(gx - 1, x * gx // w)] += 1
                total += 1
    return grid, total


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shots')
    os.makedirs(outdir, exist_ok=True)

    hwnd = user32.FindWindowW(None, '念念')
    if not hwnd:
        print('[失败] 没找到标题为「念念」的窗口')
        return 1
    print('[OK] 找到窗口 hwnd=%d' % hwnd)

    frames = []
    for i in range(n):
        g = grab(hwnd)
        if g:
            w, h, data = g
            frames.append((w, h, data))
            write_png(os.path.join(outdir, 'f%02d.png' % i), w, h, data)
        time.sleep(gap)
    if len(frames) < 2:
        print('[失败] 有效帧不足：%d' % len(frames))
        return 1

    w, h, _ = frames[0]
    print('窗口尺寸 %dx%d  有效帧 %d' % (w, h, len(frames)))

    # 1) 质心漂移
    cs = []
    for (fw, fh, d) in frames:
        c, cnt = stats(fw, fh, d)
        if c:
            cs.append(c)
    if cs:
        xs = [c[0] for c in cs]; ys = [c[1] for c in cs]
        print('轮廓质心漂移: x 幅度=%.2fpx  y 幅度=%.2fpx  (窗口宽 %.0f)'
              % (max(xs) - min(xs), max(ys) - min(ys), w))

    # 2) 分区运动能量（逐对相邻帧）
    rows = ['头/肩', '胸/上臂', '腰/手', '裙摆上', '裙摆下', '腿/脚']
    acc = [[0] * 4 for _ in range(6)]
    for i in range(len(frames) - 1):
        g, tot = diff_grid(w, h, frames[i][2], frames[i + 1][2])
        for r in range(6):
            for c in range(4):
                acc[r][c] += g[r][c]
    print('\n相邻帧差分：每行的运动像素数（左→右 = 画面左→右）')
    for r in range(6):
        print('  %-6s %s  合计=%d' % (rows[r], ' '.join('%5d' % v for v in acc[r]), sum(acc[r])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
