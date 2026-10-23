# 小芸桌面 - 设置开机自启（把启动器快捷方式放入 Windows 启动文件夹）
$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "小芸桌面.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = "powershell.exe"
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"D:\LunaAI\project-nn\xiaoyun\scripts\Start-Xiaoyun.ps1`""
$sc.WorkingDirectory = "D:\LunaAI\project-nn\xiaoyun"
$sc.WindowStyle = 7
$sc.Save()
Write-Host "[OK] 已设置开机自启：" $lnk -ForegroundColor Green
Write-Host "下次开机会自动启动小芸桌面并打开浏览器。" -ForegroundColor Cyan
Start-Sleep -Seconds 4
