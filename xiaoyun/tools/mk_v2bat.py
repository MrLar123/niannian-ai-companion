# -*- coding: utf-8 -*-
"""Generate the desktop shortcut bat that opens the pet shell on the rebuilt v2 VRM.

Kept as a script because the .bat content contains a launcher command line that
must stay pure ASCII while the filename itself is Chinese (CJK), and CRLF endings
are required for cmd.exe to parse the file reliably.
"""
import os

DESK = os.path.join(os.environ["USERPROFILE"], "Desktop")
OUT = os.path.join(DESK, "\u5ff5\u5ff5VRM_v2\u684c\u9762.bat")

LAUNCH = (
    r'powershell -NoProfile -ExecutionPolicy Bypass -File '
    r'"D:\LunaAI\xiaoyun\Start-NiannianVRM.ps1" '
    r"-Page /bjd3dvr -Model /assets/bjd3d_v2.vrm"
)

LINES = [
    "@echo off",
    "rem Launch Niannian pet window on the rebuilt v2 VRM (face P0 pass).",
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
