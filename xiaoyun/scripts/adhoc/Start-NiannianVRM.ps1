# Niannian desktop-shell launcher (VRM / BJD page) -- ASCII ONLY, keep it that way.
# PowerShell 5.1 reads a BOM-less UTF-8 .ps1 as GBK, so any Chinese char here breaks syntax.
#
# What it does, in order:
#   1) export XIAOYUN_PET_PAGE=<page>  (default /desktopvrm) -- does NOT touch desktop_app.py default
#   2) close the running pet shell so the new page actually takes effect
#   3) make sure the 8090 backend is up (falls back to the full Start-Xiaoyun chain)
#   4) launch desktop_app.py alone and wait for the window
#
# Usage:
#   Start-NiannianVRM.ps1                  -> /desktopvrm  (Niannian VRM desktop page, default model)
#   Start-NiannianVRM.ps1 -Model /assets/bjd3d_v2.vrm
#                                          -> same page but a specific .vrm (A/B compare)
#   Start-NiannianVRM.ps1 -Page /desktoppmx -> switch back to the PMX page
#   Start-NiannianVRM.ps1 -Page /bjd3dvr    -> bare VRM test page (no chat / no gaze / no poke)
param(
    [string]$Page = "/desktopvrm",
    [string]$Model = "",
    [switch]$NoBackend
)

# A/B test hook: the page reads ?model= to override the .vrm, so we only need to
# append it to the page URL -- desktop_app.py default and assets/bjd3d.vrm stay untouched.
if ($Model) {
    if ($Page.Contains("?")) { $Page = $Page + "&model=" + $Model }
    else { $Page = $Page + "?model=" + $Model }
}
$PagePath = $Page.Split("?")[0]

$ErrorActionPreference = "SilentlyContinue"

$root    = "D:\LunaAI\project-nn\xiaoyun"
$deskPy  = "$root\.venv\Scripts\pythonw.exe"
$deskApp = "$root\desktop_app.py"
$deskPid = "$root\desktop.pid"
$log     = "$root\vrm_desktop.log"
$health  = "http://127.0.0.1:8090/api/health"
$pageUrl = "http://127.0.0.1:8090$Page"

function Say([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-Url([string]$u) {
    try {
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-PetProcs {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*desktop_app.py*" -and $_.CommandLine -like "*xiaoyun*"
    })
}

"" | Add-Content -Path $log -Encoding UTF8
"==== launcher $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  page=$Page ====" | Add-Content -Path $log -Encoding UTF8

$env:XIAOYUN_PET_PAGE = $Page
Say "XIAOYUN_PET_PAGE=$($env:XIAOYUN_PET_PAGE)"

# ---- 1) close any running pet shell (page is read at process start) ----
$old = Get-PetProcs
if ($old.Count -gt 0) {
    $ids = ($old | Select-Object -ExpandProperty ProcessId) -join ','
    Say "closing existing shell pid=$ids"
    foreach ($p in $old) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 300
        if ((Get-PetProcs).Count -eq 0) { break }
    }
    Remove-Item $deskPid -Force -ErrorAction SilentlyContinue
    Say "existing shell closed"
} else {
    Say "no running shell found"
}

# ---- 2) backend ----
if ($NoBackend) {
    Say "backend check skipped (-NoBackend)"
} elseif (-not (Test-Url $health)) {
    Say "backend 8090 is down -> running full Start-Xiaoyun chain (inherits the page env)"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$root\Start-Xiaoyun.ps1"
    if ((Get-PetProcs).Count -gt 0) {
        Say "shell was started by the full chain, done"
        exit 0
    }
    Say "full chain did not start the shell, launching it directly"
} else {
    Say "backend 8090 already up"
}

# ---- 3) page route sanity check (old backend builds lack /bjd3dvr) ----
if (-not (Test-Url $pageUrl)) {
    Say "WARN $pageUrl not reachable."
    Say "WARN the running backend may predate this page. Close it (Stop-Xiaoyun) and launch again,"
    Say "WARN or run the desktop 'restart Xiaoyun' bat once, then re-run this launcher."
    if ($PagePath -eq "/bjd3dvr") {
        Say "WARN aborting: a blank pet window would be worse than none"
        exit 2
    }
}

# ---- 4) launch the shell ----
if (-not (Test-Path $deskPy)) {
    Say "ERROR missing $deskPy"
    exit 1
}

# ---- 4) launch the shell (one automatic retry if it dies right after loading) ----
$srvLog = "$root\srv_err.log"
$launchCmd = "$root\_shell_launch.cmd"

# HARD RULE: never use Start-Process -RedirectStandardOutput/-RedirectStandardError here.
# That attaches the shell to an anonymous pipe owned by this script; once the launcher
# exits the read end closes, the shell errors on its next stdout write and vanishes
# silently. Symptom the user sees: the pet window freezes on its last frame, then is gone.
# Use a .cmd file with plain file redirection instead -- file handles outlive the parent.
$launchLines = @(
    '@echo off',
    "`"$deskPy`" `"$deskApp`" > `"$root\desktop_out.log`" 2>&1"
)
Set-Content -Path $launchCmd -Value $launchLines -Encoding ASCII

$winOk = $false
$aliveOk = $false
$shellPid = 0

for ($attempt = 1; $attempt -le 2; $attempt++) {
    if ($attempt -gt 1) { Say "retry #2: previous shell died right after loading the page" }

    Remove-Item $deskPid -Force -ErrorAction SilentlyContinue
    # mark the backend request log: a new "GET /<page>" line after this offset proves
    # the shell really loaded our page (window-title tricks are unreliable here:
    # the pet window reports an empty title while the click layer reports "catcher").
    $logMark = 0
    if (Test-Path $srvLog) { $logMark = (Get-Item $srvLog).Length }

    Say "starting desktop shell..."
    Start-Process -FilePath $launchCmd -WorkingDirectory $root -WindowStyle Hidden

    $pidOk = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $deskPid) { $pidOk = $true; break }
    }
    if ($pidOk) {
        $shellPid = 0
        [void][int]::TryParse(((Get-Content $deskPid -Raw) -replace '\s', ''), [ref]$shellPid)
        Say "pid file written: pid=$shellPid"
    } else {
        Say "WARN no desktop.pid after 90s (see desktop_out.log)"
    }

    $winOk = $false
    $needle = [regex]::Escape("GET $PagePath")
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (-not (Test-Path $srvLog)) { continue }
        $sr = $null
        $fs = $null
        try {
            $fs = [IO.File]::Open($srvLog, 'Open', 'Read', 'ReadWrite')
            [void]$fs.Seek($logMark, 'Begin')
            $sr = New-Object IO.StreamReader($fs)
            $tail = $sr.ReadToEnd()
            if ($tail -match $needle) {
                $winOk = $true
                break
            }
        } catch {
            # log file busy / rotated: just retry next second
        } finally {
            try { if ($sr) { $sr.Close() } } catch {}
            try { if ($fs) { $fs.Close() } } catch {}
        }
    }

    if (-not $winOk) {
        Say "WARN the shell never requested $Page within 40s; tail of desktop_out.log:"
        if (Test-Path "$root\desktop_out.log") {
            $t = @(Get-Content "$root\desktop_out.log" -Tail 6 -ErrorAction SilentlyContinue)
            if ($t.Count -gt 0) { Say ($t -join ' | ') }
        }
        break
    }
    Say "page $Page loaded by the shell"

    # A page request does NOT prove the shell stays alive: it can die right after load
    # (WebView2 device-removed / renderer crash). Watch it for another 10s and retry once,
    # instead of leaving the user staring at a frozen last frame.
    Say "checking the shell survives 10s..."
    Start-Sleep -Seconds 10
    $alive = ($shellPid -gt 0) -and ($null -ne (Get-Process -Id $shellPid -ErrorAction SilentlyContinue))
    if ($alive) {
        Say "alive after 10s (pid=$shellPid)"
        $aliveOk = $true
        break
    }
    Say "WARN shell pid=$shellPid disappeared within 10s of loading the page"
}

if (-not $winOk) {
    Say "ABORT: no pet window. See desktop_out.log and the tray diagnostic log in $root"
    exit 3
}
if (-not $aliveOk) {
    Say "WARN the pet window crashed twice. Check the tray diagnostic log for a FATAL line."
    exit 4
}

Say "done (page=$Page)"
