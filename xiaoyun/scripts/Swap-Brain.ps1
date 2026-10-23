# Swap-Brain: restart or_proxy (port 8787) with the latest code.
# ASCII-only on purpose: PS 5.1 parses BOM-less UTF-8 as GBK and Chinese breaks it.
# Invoked by explorer-launched _swap_brain.bat; log -> D:\LunaAI\swap_brain.log
$ErrorActionPreference = "SilentlyContinue"
$log = "D:\LunaAI\swap_brain.log"
function L($m){ $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Add-Content -Path $log -Value $line -Encoding UTF8; Write-Host $line }

"==== SWAP BRAIN $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Set-Content $log -Encoding UTF8

$orPy  = "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
$orApp = "D:\LunaAI\or_proxy.py"
$orOut = "D:\LunaAI\or_proxy_stdout.log"
$orErr = "D:\LunaAI\or_proxy_stderr.log"

function Test-Brain(){
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:8787/health" -UseBasicParsing -TimeoutSec 3; return ($r.StatusCode -eq 200) } catch { return $false }
}

# 1) find current 8787 listener (the running or_proxy instance)
$old = @(Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
         Select-Object -ExpandProperty OwningProcess -Unique)
L "current 8787 listener pid = $($old -join ',')"

foreach($pid_ in $old){
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_" -ErrorAction SilentlyContinue
    L "pid=$pid_ cmd=$($p.CommandLine)"
    if($p -and $p.CommandLine -like "*or_proxy.py*"){
        L "stopping old or_proxy pid=$pid_"
        Stop-Process -Id $pid_ -Force
    } else {
        L "pid=$pid_ is not or_proxy, skip"
    }
}
Start-Sleep -Seconds 2

if(Test-Brain){
    L "[warn] 8787 still responds after stop, waiting 3s and re-check..."
    Start-Sleep -Seconds 3
    if(Test-Brain){ L "[error] 8787 still occupied, aborting (check manually)"; exit 1 }
}

# 2) start new or_proxy (same launch shape as Start-Xiaoyun.ps1)
if(Test-Path $orApp){
    L "starting new or_proxy ..."
    Start-Process -FilePath $orPy -ArgumentList "`"$orApp`"" -WorkingDirectory "D:\LunaAI" `
        -RedirectStandardOutput $orOut -RedirectStandardError $orErr -WindowStyle Hidden
    for($i=0; $i -lt 15; $i++){
        Start-Sleep -Seconds 1
        if(Test-Brain){ break }
    }
    if(Test-Brain){
        $h = (Invoke-WebRequest -Uri "http://127.0.0.1:8787/health" -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json
        L "[OK] new brain online: default_model=$($h.default_model) priority=$($h.upstream_priority -join '>')"
    } else {
        L "[error] or_proxy not ready after start, see or_proxy_stderr.log"
    }
} else {
    L "[error] $orApp not found"
}
