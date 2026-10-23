# 小芸桌面 - Ollama 稳定化配置脚本（可重复执行，幂等）
#
# 解决三个问题：
#   1) 空闲 5 分钟后模型被卸载，重新加载时 llama-server 偶发崩溃 -> 后端 500
#      => OLLAMA_KEEP_ALIVE=-1，模型一次加载后常驻显存，永不卸载
#   2) Ollama 桌面端（托盘 app）用的是它自己设置库里的模型目录
#      （默认 C:\Users\...\.ollama\models，是空的），会忽略 OLLAMA_MODELS
#      => 把桌面端设置库里的模型目录统一改成 D:\OllamaModels
#   3) 桌面端开了自动更新，后台更新会导致服务重启（表现为「自己退掉」）
#      => 关闭自动更新
#
# 用法：右键「以管理员身份运行 PowerShell」后执行本脚本，或直接双击 配置Ollama常驻.bat

$ErrorActionPreference = "SilentlyContinue"

$py       = "D:\LunaAI\app\AI-Vtuber-20240229\Miniconda3\python.exe"
$helper   = "D:\LunaAI\xiaoyun\ollama_app_settings.py"
$cfgPath  = "D:\LunaAI\app\AI-Vtuber-20240229\config.json"
$models   = "D:\OllamaModels"
$api      = "http://127.0.0.1:11434"
$logFile  = "D:\LunaAI\xiaoyun\ollama_config.log"
$ollamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$ollamaApp = Join-Path $ollamaDir "ollama app.exe"
$ollamaExe = Join-Path $ollamaDir "ollama.exe"
$outLog   = "D:\LunaAI\xiaoyun\logs\ollama_out.log"
$errLog   = "D:\LunaAI\xiaoyun\logs\ollama_err.log"

"==== Ollama 稳定化配置 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Set-Content $logFile -Encoding UTF8

function Log($m, $color) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    if ($color) { Write-Host $line -ForegroundColor $color } else { Write-Host $line }
}

function Get-ModelName {
    try {
        $c = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($c.chatgpt.model) { return $c.chatgpt.model }
        if ($c.openai.model)  { return $c.openai.model }
    } catch {}
    return "qwen3.5-9b"
}

function Test-Ollama {
    try {
        $r = Invoke-WebRequest -Uri "$api/api/tags" -UseBasicParsing -TimeoutSec 4
        return ($r.StatusCode -eq 200 -and $r.Content -match [regex]::Escape($model))
    } catch { return $false }
}

$model = Get-ModelName
Log "模型 = $model    模型目录 = $models"

# ---------- 1) 写入用户级环境变量（永久生效，下次开机会被 Ollama 继承） ----------
$envs = [ordered]@{
    "OLLAMA_MODELS"           = $models
    "OLLAMA_KEEP_ALIVE"       = "-1"
    "OLLAMA_VULKAN"           = "false"
    "OLLAMA_NUM_PARALLEL"     = "1"
    "OLLAMA_MAX_LOADED_MODELS"= "1"
    "OLLAMA_LOAD_TIMEOUT"     = "10m"
}
foreach ($k in $envs.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envs[$k], "User")
    Set-Item -Path ("Env:" + $k) -Value $envs[$k]
    Log ("  环境变量 -> " + $k + "=" + $envs[$k])
}
Log "[OK] 用户环境变量已写入（OLLAMA_KEEP_ALIVE=-1 表示模型永不卸载）" "Green"

# ---------- 2) 退出 Ollama，让它重新读取配置 ----------
$running = @(Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Log ("正在退出 Ollama（进程数 " + $running.Count + "）…")
    Get-Process -Name "ollama app" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    Get-Process -Name "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 3
}

# ---------- 3) 修正桌面端设置库里的模型目录 + 关闭自动更新 ----------
if ((Test-Path $py) -and (Test-Path $helper)) {
    Log "--- 修正 Ollama 桌面端设置库 ---"
    $s = & $py $helper show 2>&1
    $s | ForEach-Object { Log ("  " + $_) }
    $r = & $py $helper set-models $models 2>&1
    $r | ForEach-Object { Log ("  " + $_) }
    $r = & $py $helper set-auto-update 0 2>&1
    $r | ForEach-Object { Log ("  " + $_) }
    Log "[OK] 桌面端模型目录已统一，自动更新已关闭" "Green"
} else {
    Log "[跳过] 缺少 python 或 ollama_app_settings.py" "Yellow"
}

# ---------- 4) 重新启动 Ollama ----------
# 重要：Ollama 在「提权(管理员)」环境下启动时，llama-server 子进程会探测不到显卡，
# 表现为降级成纯 CPU、随后聊天 500。所以这里不在提权环境下重启它。
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Log "[跳过] 当前是管理员权限，已跳过重启 Ollama。" "Yellow"
    Log "       原因：提权环境下 Ollama 可能探测不到显卡，会退化成纯 CPU 并导致聊天 500。" "Yellow"
    Log "       配置已经写好，请直接用普通权限双击 启动小芸桌面.bat，由它把大脑拉起来。" "Yellow"
    Log "==== 配置阶段完成（未重启服务）===="
    if ($Host.Name -eq "ConsoleHost" -and -not $env:XIAOYUN_NOPAUSE) { Start-Sleep -Seconds 8 }
    exit 0
}

if (Test-Path $ollamaExe) {
    Log "启动 ollama.exe serve（日志 -> ollama_out.log / ollama_err.log）"
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
} elseif (Test-Path $ollamaApp) {
    Log "启动 ollama app.exe（托盘）"
    Start-Process -FilePath $ollamaApp
} else {
    Log "[错误] 找不到 ollama.exe，请确认已安装 Ollama" "Red"
}

$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Ollama) { $ready = $true; break }
}
if ($ready) { Log "[OK] Ollama 在线且模型 $model 可识别" "Green" }
else { Log "[警告] Ollama 未就绪，请查看 ollama_err.log" "Yellow" }

# ---------- 5) 预热：把模型加载进显存并钉住（keep_alive=-1） ----------
if ($ready) {
    Log "预热模型（首次加载较慢，请等待）…"
    $body = '{"model":"' + $model + '","prompt":"hi","stream":false,"keep_alive":-1,"options":{"num_predict":1}}'
    $t0 = Get-Date
    try {
        Invoke-RestMethod -Uri "$api/api/generate" -Method Post -Body $body `
            -ContentType "application/json" -TimeoutSec 600 | Out-Null
        $sec = [int]((Get-Date) - $t0).TotalSeconds
        Log ("[OK] 预热完成，耗时 " + $sec + " 秒") "Green"
    } catch {
        Log ("[警告] 预热失败：" + $_.Exception.Message) "Yellow"
        Log "       详见 ollama_err.log 中的 llama-server 输出" "Yellow"
    }
    try {
        $ps = Invoke-RestMethod -Uri "$api/api/ps" -TimeoutSec 10
        if ($ps.models -and $ps.models.Count -gt 0) {
            foreach ($m in $ps.models) {
                Log ("[驻留] " + $m.name + "  显存 " + [int]($m.size_vram/1MB) + " MB  到期 " + $m.expires_at) "Green"
            }
        } else {
            Log "[驻留] 当前没有模型驻留（预热可能失败）" "Yellow"
        }
    } catch {}
}

Log "==== 配置完成 ===="
Write-Host ""
Write-Host "完成。以后模型会一直待在显存里，不会因为没聊天而被卸载。" -ForegroundColor Cyan
Write-Host "日志：$logFile" -ForegroundColor DarkGray
if ($Host.Name -eq "ConsoleHost" -and -not $env:XIAOYUN_NOPAUSE) { Start-Sleep -Seconds 6 }
