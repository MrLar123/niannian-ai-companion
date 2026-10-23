# 小芸桌面 - Ollama 保活守护
#
# 作用：确保「小芸的大脑」永不掉线。每 INTERVAL 秒巡检一次：
#   1) 11434 无响应      -> 重新拉起 ollama serve
#   2) 模型不在显存里    -> 用 keep_alive=-1 重新预热并钉住
#   3) 一切正常          -> 什么都不做（不刷日志）
#
# 用法：
#   常驻守护（隐藏窗口）： powershell -File Keep-Ollama.ps1
#   只检查一次（排障）：   powershell -File Keep-Ollama.ps1 -Once

param(
    [int]$Interval = 60,
    [switch]$Once
)

$ErrorActionPreference = "SilentlyContinue"

$cfgPath   = "D:\LunaAI\app\AI-Vtuber-20240229\config.json"
$models    = "D:\OllamaModels"
$api       = "http://127.0.0.1:11434"
$logFile   = "D:\LunaAI\xiaoyun\logs\ollama_keepalive.log"
$ollamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$ollamaExe = Join-Path $ollamaDir "ollama.exe"
$outLog    = "D:\LunaAI\xiaoyun\logs\ollama_out.log"
$errLog    = "D:\LunaAI\xiaoyun\logs\ollama_err.log"

$env:OLLAMA_MODELS = $models
$env:OLLAMA_KEEP_ALIVE = "-1"

function Get-ModelName {
    try {
        $c = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($c.chatgpt.model) { return $c.chatgpt.model }
    } catch {}
    return "qwen3.5-9b"
}

function Log($m) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    $txt = Get-Content $logFile -ErrorAction SilentlyContinue
    if ($txt -and $txt.Count -gt 2000) {
        $txt[-1000..-1] | Set-Content $logFile -Encoding UTF8
    }
}

function Start-Ollama {
    if (-not (Test-Path $ollamaExe)) { Log "[错误] 找不到 ollama.exe"; return $false }
    Log "11434 无响应，重新拉起 ollama serve"
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try {
            $null = Invoke-WebRequest -Uri "$api/api/version" -UseBasicParsing -TimeoutSec 3
            Log "ollama serve 已就绪"
            return $true
        } catch {}
    }
    Log "[错误] ollama serve 启动超时，见 ollama_err.log"
    return $false
}

function Warmup($model) {
    Log "模型未驻留，执行预热（keep_alive=-1）"
    $body = '{"model":"' + $model + '","prompt":"hi","stream":false,"keep_alive":-1,"options":{"num_predict":1}}'
    try {
        Invoke-RestMethod -Uri "$api/api/generate" -Method Post -Body $body `
            -ContentType "application/json" -TimeoutSec 600 | Out-Null
        Log "[OK] 预热完成，模型已钉在显存"
    } catch {
        Log ("[警告] 预热失败：" + $_.Exception.Message)
    }
}

$model = Get-ModelName
Log "保活守护启动（巡检间隔 ${Interval}s，模型 $model）"

$lastState = ""
while ($true) {
    $state = "ok"
    try {
        $null = Invoke-WebRequest -Uri "$api/api/version" -UseBasicParsing -TimeoutSec 4
    } catch {
        $state = "down"
    }

    if ($state -eq "down") {
        if (Start-Ollama) { Warmup $model }
    } else {
        try {
            $ps = Invoke-RestMethod -Uri "$api/api/ps" -TimeoutSec 10
            $hit = $false
            if ($ps.models) {
                foreach ($m in $ps.models) { if ($m.name -like ($model + "*")) { $hit = $true } }
            }
            if (-not $hit) { Warmup $model }
        } catch {
            Log ("[警告] /api/ps 查询失败：" + $_.Exception.Message)
        }
    }

    if ($state -ne $lastState) { Log ("状态：" + $lastState + " -> " + $state) ; $lastState = $state }
    if ($Once) { break }
    Start-Sleep -Seconds $Interval
}
