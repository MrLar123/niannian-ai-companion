# -*- coding: utf-8 -*-
"""让桌宠跳指定动作，然后连拍真实窗口像素。
Usage: python tools/pet_dance_shot.py <motion_key> [frames] [gap] [outdir]
"""
import ctypes, zlib, struct, os, sys, time, json, urllib.request
from ctypes import wintypes

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
    ok = user32.PrintWindow(hwnd, mem, 2)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32
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


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else 'cinderella'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    gap = float(sys.argv[3]) if len(sys.argv) > 3 else 0.7
    outdir = sys.argv[4] if len(sys.argv) > 4 else r'D:/LunaAI/hunyuan3d/work/motion_nn/pet_live'
    os.makedirs(outdir, exist_ok=True)

    hwnd = user32.FindWindowW(None, '念念')
    if not hwnd:
        print('FAIL no window')
        return 1
    print('hwnd=%d' % hwnd)

    try:
        req = urllib.request.Request('http://127.0.0.1:8090/api/motion',
                                     data=json.dumps({'key': key}).encode(),
                                     headers={'Content-Type': 'application/json'})
        print('motion ->', urllib.request.urlopen(req, timeout=6).read().decode('utf-8')[:120])
    except Exception as e:
        print('WARN api %r' % e)

    time.sleep(4.0)
    ok = 0
    for i in range(n):
        g = grab(hwnd)
        if g:
            w, h, d = g
            write_png(os.path.join(outdir, '%s_%02d.png' % (key, i)), w, h, d)
            ok += 1
            if i == 0:
                print('size %dx%d' % (w, h))
        time.sleep(gap)
    print('saved %d frames to %s' % (ok, outdir))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
