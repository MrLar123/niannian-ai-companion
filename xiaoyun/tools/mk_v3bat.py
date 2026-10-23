# -*- coding: utf-8 -*-
"""Generate the desktop shortcut bat that opens the pet shell on the v3 VRM.

Same contract as mk_v2bat.py: the .bat body must stay pure ASCII while the
filename is CJK, and CRLF endings are required for cmd.exe.
"""
import os

DESK = os.path.join(os.environ["USERPROFILE"], "Desktop")
OUT = os.path.join(DESK, "\u5ff5\u5ff5VRM_v3\u684c\u9762.bat")

LAUNCH = (
    r'powershell -NoProfile -ExecutionPolicy Bypass -File '
    r'"D:\LunaAI\xiaoyun\Start-NiannianVRM.ps1" '
    r"-Page /bjd3dvr -Model /assets/bjd3d_v3.vrm"
)

LINES = [
    "@echo off",
    "rem Launch Niannian pet window on the v3 VRM (4-material projection).",
    "rem Overrides the page URL only -- desktop_app.py default page and",
    "rem assets/bjd3d.vrm are NOT touched, so the normal desktop stays as-is.",
    LAUNCH,
    "echo.",
    r"echo Done. Log: D:\LunaAI\xiaoyun\vrm_desktop.log",
    "pause >nul",
]

with open(OUT, "w", encoding="ascii", newline="\r\n") as f:
    f.write("\n".join(LINES) + "\n")

print("written:", OUT, os.path.getsize(OUT), "bytes")
