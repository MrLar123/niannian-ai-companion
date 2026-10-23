# -*- coding: utf-8 -*-
"""生成桌面上的「重启小芸桌面.bat」。

为什么要用脚本生成而不是手写：
  · 批处理必须是 CRLF 行尾。用 LF 存盘时 cmd 解析多行结构会出错，
    实测会直接落到失败分支、脚本根本跑不起来。
  · .bat 里出现中文（且文件是 UTF-8）时，cmd 用 GBK 解码会乱码，
    所以正文只写 ASCII，中文提示交给 PowerShell 脚本输出。
"""
import os

DESKTOP = os.path.join(os.environ["USERPROFILE"], "Desktop")
TARGET = os.path.join(DESKTOP, "重启小芸桌面.bat")

PS = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
SCRIPT = r"D:\LunaAI\xiaoyun\Restart-Pet.ps1"
LOG = r"D:\LunaAI\xiaoyun\restart_log.txt"

lines = [
    "@echo off",
    "title Xiaoyun Pet Restart",
    '"{ps}" -NoProfile -ExecutionPolicy Bypass -File "{script}"'.format(ps=PS, script=SCRIPT),
    "if errorlevel 1 goto failed",
    "timeout /t 4 /nobreak >nul",
    "exit /b 0",
    "",
    ":failed",
    "echo.",
    "echo [!] Restart failed - see messages above.",
    "echo     Detail log: {log}".format(log=LOG),
    "pause >nul",
    "exit /b 1",
    "",
]

with open(TARGET, "w", encoding="ascii", newline="") as f:
    f.write("\r\n".join(lines))

print("written:", TARGET)
print("bytes:", os.path.getsize(TARGET))
