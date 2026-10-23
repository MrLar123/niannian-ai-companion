# -*- coding: utf-8 -*-
"""Generate a desktop .bat that opens the pet shell on any VRM in xiaoyun/assets.

Usage:
    python mk_vrm_bat.py mynini.vrm [label]

The generated .bat body stays pure ASCII (cmd.exe + GBK console would mangle
CJK inside the file); only the filename is CJK, which is safe.
Start-NiannianVRM.ps1 already takes -Page / -Model, so this only rewrites the
URL -- desktop_app.py's default page and assets/bjd3d.vrm are never touched.
"""
import os
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    name = sys.argv[1]
    if not name.lower().endswith(".vrm"):
        name += ".vrm"

    src = os.path.join(r"D:\LunaAI\xiaoyun\assets", name)
    if not os.path.exists(src):
        print("missing:", src)
        print("put the VRM exported by VRoid Studio into D:\\LunaAI\\xiaoyun\\assets\\ first")
        return 2

    if not name.isascii():
        print("ERROR: keep the exported filename ASCII (e.g. niannian_v4.vrm).")
        print("       cmd.exe cannot handle non-ASCII inside a .bat body.")
        return 3

    key = os.path.splitext(name)[0]
    label = sys.argv[2] if len(sys.argv) > 2 else key
    if not label.isascii():
        label = key

    launch = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        '"D:\\LunaAI\\xiaoyun\\Start-NiannianVRM.ps1" '
        "-Page /bjd3dvr -Model /assets/" + name
    )

    lines = [
        "@echo off",
        "rem Open the desktop pet window on " + name,
        "rem Default page / model in desktop_app.py are NOT modified.",
        launch,
        "echo.",
        "echo Done. Log: D:\\LunaAI\\xiaoyun\\vrm_desktop.log",
        "pause >nul",
    ]

    desk = os.path.join(os.environ["USERPROFILE"], "Desktop")
    out = os.path.join(desk, "\u5ff5\u5ff5VRM_" + label + "\u684c\u9762.bat")
    with open(out, "wb") as f:
        f.write(("\r\n".join(lines) + "\r\n").encode("ascii"))

    print("VRM  :", src, "(%.2f MB)" % (os.path.getsize(src) / 1048576))
    print("bat  :", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
