# -*- coding: utf-8 -*-
"""把右键菜单真的建出来，再把 Win32 结构读回来打印成树（不弹窗、不点鼠标）。

为什么要有这个：popup_menu_layout() 只产出数据，「两级子菜单真的挂上去了吗、
勾选位写对了吗、文字有没有被截断」是 Win32 那一层的责任，肉眼验收成本很高。
这里走同一条 build_popup_hmenu()，建完用 GetMenuItemCount / GetMenuStringW /
GetMenuState 读回来——菜单层级错、勾选错、文字丢了，这里立刻看得出来。

用法：
    cd D:\\LunaAI\\project-nn\\xiaoyun
    .venv\\Scripts\\python.exe tools\\dump_menu_tree.py
    .venv\\Scripts\\python.exe tools\\dump_menu_tree.py 人物形象   # 只看某一支
"""
import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

MF_BYPOSITION = 0x0400
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_GRAYED = 0x0001


def walk(D, hmenu, depth, out, maxdepth=3):
    u = D.user32
    n = u.GetMenuItemCount(hmenu)
    for i in range(n):
        st = u.GetMenuState(hmenu, i, MF_BYPOSITION)
        if st == 0xFFFFFFFF:
            out.append((depth, "?", "", False, True))
            continue
        # 低字节是标志位
        flags = st & 0xFF
        buf = ctypes.create_unicode_buffer(512)
        u.GetMenuStringW(hmenu, i, buf, 512, MF_BYPOSITION)
        text = buf.value
        if flags & MF_SEPARATOR or (not text and not (flags & MF_POPUP)):
            out.append((depth, "----", "", False, True))
            continue
        is_pop = bool(flags & MF_POPUP)
        out.append((depth, text, "sub" if is_pop else "", bool(flags & MF_CHECKED),
                    bool(flags & MF_GRAYED)))
        if is_pop and depth < maxdepth:
            walk(D, u.GetSubMenu(hmenu, i), depth + 1, out, maxdepth)


def bind():
    import desktop_app as D
    u = D.user32
    u.GetMenuItemCount.argtypes = [ctypes.c_void_p]
    u.GetMenuItemCount.restype = ctypes.c_int
    u.GetSubMenu.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetSubMenu.restype = ctypes.c_void_p
    u.GetMenuStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.POINTER(ctypes.c_wchar),
                                 ctypes.c_int, ctypes.c_uint]
    u.GetMenuStringW.restype = ctypes.c_int
    u.GetMenuState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    u.GetMenuState.restype = ctypes.c_uint
    u.DestroyMenu.argtypes = [ctypes.c_void_p]
    return D


def main():
    D = bind()
    from test_menu_labels import build_app       # 同一份取数逻辑，别写两遍
    app = build_app(D)
    rows = D.popup_menu_layout(app)
    hmenu = D.build_popup_hmenu(rows)
    out = []
    walk(D, hmenu, 0, out)

    only = sys.argv[1] if len(sys.argv) > 1 else ""
    print_rows = []
    if only:
        # 只看某一支：把顶层里标题包含关键字的那个子菜单整段摘出来
        for i, (depth, text, kind, checked, grayed) in enumerate(out):
            if depth == 0 and only in text:
                j = i + 1
                while j < len(out) and out[j][0] > 0:
                    j += 1
                print_rows = out[i:j]
                break
        if not print_rows:
            print("菜单里没有标题包含「%s」的顶层项" % only)
    else:
        print_rows = out
    for depth, text, kind, checked, grayed in print_rows:
        mark = "√" if checked else ("-" if grayed else " ")
        tag = "▸" if kind == "sub" else " "
        print("%s%s%s %s" % ("   " * depth, tag, mark, text))
    n_sub = sum(1 for r in out if r[2] == "sub")
    n_item = sum(1 for r in out if r[2] != "sub" and r[1] != "----")
    print("\n合计：子菜单 %d 个，菜单项 %d 行（含只读提示），实际层级最深 %d 级"
          % (n_sub, n_item, max(d for d, *_ in out)))
    D.user32.DestroyMenu(hmenu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
