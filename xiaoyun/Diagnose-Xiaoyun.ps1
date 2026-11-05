# 小芸桌面 - 一键诊断：逐项检查并给出结论
$ErrorActionPreference = "SilentlyContinue"

function Test-Url($u){
    try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 4; return $r.StatusCode }
    catch { return 0 }
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
public class W {
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(IntPtr a, string b);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SystemParametersInfo(int a, int b, out RECT r, int f);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
}
"@

Write-Host "================ 小芸桌面 诊断 ================" -ForegroundColor Cyan

# 1) Ollama 本地大脑
$oll = Test-Url "http://127.0.0.1:11434/api/tags"
if($oll -eq 200){
    $tags = (Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing).Content
    if($tags -match "qwen3.5"){
        Write-Host "[OK]   本地大脑 Ollama 在线，模型 qwen3.5-9b 已加载" -ForegroundColor Green
    } else {
        Write-Host "[问题] Ollama 在线，但没找到模型（OLLAMA_MODELS 不对）" -ForegroundColor Red
        Write-Host "       当前 OLLAMA_MODELS = $env:OLLAMA_MODELS" -ForegroundColor Yellow
    }
} else {
    Write-Host "[问题] Ollama 未运行（11434 无响应）" -ForegroundColor Red
}

# 1b) 模型是否常驻显存：这是「放一会儿再聊天就 500」的直接判据
if($oll -eq 200){
    try{
        $ps = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/ps" -TimeoutSec 5
        if($ps.models -and $ps.models.Count -gt 0){
            foreach($m in $ps.models){
                Write-Host ("[OK]   模型常驻显存：" + $m.name + "  显存 " + [int]($m.size_vram/1MB) + " MB  到期 " + $m.expires_at) -ForegroundColor Green
            }
        } else {
            Write-Host "[问题] 没有模型常驻显存（空闲会被卸载，重载可能失败 -> 聊天 500）" -ForegroundColor Red
            Write-Host "       处理：双击 配置Ollama常驻.bat，再重开一次 启动小芸桌面.bat" -ForegroundColor Yellow
        }
    } catch {}
}

# 2) 后端 8090
$hb = Test-Url "http://127.0.0.1:8090/api/health"
if($hb -eq 200){
    Write-Host "[OK]   小芸后端已运行（8090）" -ForegroundColor Green
    $dp = Test-Url "http://127.0.0.1:8090/desktop"
    if($dp -eq 200){ Write-Host "[OK]   桌面页面 /desktop 可访问" -ForegroundColor Green }
    else { Write-Host "[问题] /desktop 页面取不到：后端是旧版本，需要重启后端" -ForegroundColor Red }
} else {
    Write-Host "[问题] 后端 8090 未运行" -ForegroundColor Red
}

# 3) 桌面窗口环境
$deskPy = "D:\LunaAI\project-nn\xiaoyun\.venv\Scripts\pythonw.exe"
if(Test-Path $deskPy){ Write-Host "[OK]   桌面窗口环境存在（.venv）" -ForegroundColor Green }
else { Write-Host "[问题] 缺少 .venv\Scripts\pythonw.exe，桌面窗口起不来" -ForegroundColor Red }

# 4) 桌面进程
$procs = @(Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*xiaoyun\.venv*" })
if($procs.Count -gt 0){
    Write-Host "[OK]   桌面程序正在运行，PID = $($procs.Id -join ',')" -ForegroundColor Green
} else {
    Write-Host "[提示] 桌面程序当前没有运行" -ForegroundColor Yellow
}

# 5) 窗口是否真的显示出来
$hw = [W]::FindWindow([IntPtr]::Zero, "念念")
$wa = New-Object RECT
[void][W]::SystemParametersInfo(0x0030, 0, [ref]$wa, 0)
$wr = New-Object RECT
if($hw -ne 0 -and [W]::IsWindowVisible($hw)){
    [void][W]::GetWindowRect($hw, [ref]$wr)
    $waW = $wa.R - $wa.L; $waH = $wa.B - $wa.T
        $out = ($wr.R -le $wa.L) -or ($wr.L -ge $wa.R) -or ($wr.B -le $wa.T) -or ($wr.T -ge $wa.B)
        $partial = ($wr.L -lt $wa.L) -or ($wr.T -lt $wa.T) -or ($wr.R -gt $wa.R) -or ($wr.B -gt $wa.B)
        if($out){
            Write-Host "[问题] 念念窗口完全跑到屏幕可视区外面了！" -ForegroundColor Red
            Write-Host ("       窗口位置: (" + $wr.L + "," + $wr.T + ")-(" + $wr.R + "," + $wr.B + ")") -ForegroundColor Yellow
            Write-Host ("       可用工作区: (" + $wa.L + "," + $wa.T + ")-(" + $wa.R + "," + $wa.B + ")  尺寸 " + $waW + "x" + $waH) -ForegroundColor Yellow
        } elseif($partial){
            Write-Host "[问题] 念念窗口有一部分超出屏幕可视区（会被切掉）" -ForegroundColor Red
            Write-Host ("       窗口位置: (" + $wr.L + "," + $wr.T + ")-(" + $wr.R + "," + $wr.B + ")") -ForegroundColor Yellow
            Write-Host ("       可用工作区: (" + $wa.L + "," + $wa.T + ")-(" + $wa.R + "," + $wa.B + ")  尺寸 " + $waW + "x" + $waH) -ForegroundColor Yellow
        } else {
            Write-Host ("[OK]   念念窗口已完整显示，位置 (" + $wr.L + "," + $wr.T + ") 尺寸 " + ($wr.R-$wr.L) + "x" + ($wr.B-$wr.T)) -ForegroundColor Green
            Write-Host ("       屏幕工作区 " + $waW + "x" + $waH + "（GetSystemMetrics 报告 " + [W]::GetSystemMetrics(0) + "x" + [W]::GetSystemMetrics(1) + "）")
        }
} elseif($hw -ne 0){
    Write-Host "[提示] 念念窗口存在但被隐藏了：从托盘点「显示 / 隐藏念念」" -ForegroundColor Yellow
} else {
    Write-Host "[提示] 屏幕上没有念念窗口" -ForegroundColor Yellow
}

# 6) 错误日志
$errLog = "D:\LunaAI\project-nn\xiaoyun\logs\desktop_err.log"
if(Test-Path $errLog){
    $c = Get-Content $errLog -Raw
    if($c -and $c.Trim().Length -gt 0){
        Write-Host "`n--- desktop_err.log 最后 15 行 ---" -ForegroundColor Magenta
        Get-Content $errLog -Tail 15
    } else {
        Write-Host "[OK]   桌面程序错误日志为空" -ForegroundColor Green
    }
}

Write-Host "`n================ 结论 ================" -ForegroundColor Cyan
if($oll -eq 200 -and $hb -eq 200 -and $procs.Count -gt 0){
    Write-Host "念念应该已经在你的桌面上了（屏幕右下角）。" -ForegroundColor Green
} else {
    Write-Host "还有未就绪的环节，请按上面的 [问题] 项处理，然后重开「启动小芸桌面.bat」。" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "（本窗口 25 秒后自动关闭，也可直接关闭）"
Start-Sleep -Seconds 25
