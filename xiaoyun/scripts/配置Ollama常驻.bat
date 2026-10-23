@echo off
chcp 936 >nul
title 配置 Ollama 常驻（小芸的大脑）
echo.
echo  即将把 Ollama 配置为：模型常驻显存、空闲不卸载、关闭自动更新重启。
echo.
echo  注意：请用【普通权限】运行本文件，不要选「以管理员身份运行」。
echo        提权环境下 Ollama 会探测不到显卡，反而会引发聊天 500。
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\LunaAI\project-nn\xiaoyun\scripts\Set-Ollama-Config.ps1"
echo.
echo  完成。详细日志：D:\LunaAI\project-nn\xiaoyun\ollama_config.log
echo  如需让新的服务参数立刻生效，请再双击一次 启动小芸桌面.bat。
pause >nul
