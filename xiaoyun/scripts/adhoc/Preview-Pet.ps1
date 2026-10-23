# =====================================================================
#  Preview-Pet.ps1  --  single-model preview window for a PMX pet
#
#  Same idea as the Niannian VRM launcher on the desktop:
#    - make sure the tiny read-only static server (stub_server.py) is up
#    - open ONE model in a Chrome/Edge app window with its own profile
#    - optionally show the colour tuner panel (?tune=1)
#
#  It never writes assets/motions and never touches current.txt, so the
#  motion library and the live pet stay exactly as they are.
#
#  NOTE: keep this file ASCII-only (PS 5.1 parses .ps1 as GBK).
#  Usage:
#    powershell -File Preview-Pet.ps1 -Model gengchen
#    powershell -File Preview-Pet.ps1 -Model gengchen -Tune
# =====================================================================
param(
    [string] $Model = '',
    [switch] $Tune,
    [int] $Width = 900,
    [int] $Height = 980,
    [int] $X = 300,
    [int] $Y = 20
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'   # keep Invoke-WebRequest quiet
$Root = 'D:\LunaAI\project-nn\xiaoyun'
$Port = 8765
$Health = "http://127.0.0.1:$Port/api/models"
$Pyw    = 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe'

function Test-Up {
    try {
        Invoke-WebRequest -Uri $Health -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Up)) {
    if (-not (Test-Path $Pyw)) { $Pyw = 'pythonw.exe' }
    try {
        Start-Process -FilePath $Pyw -ArgumentList ('"' + $Root + '\stub_server.py"') -WindowStyle Hidden
    } catch {
        Write-Host "[warn] cannot start stub_server.py: $($_.Exception.Message)"
    }
    for ($i = 0; $i -lt 16; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Up) { break }
    }
}

if (-not (Test-Up)) {
    Write-Host '[error] preview server did not come up on 127.0.0.1:8765'
    exit 1
}

$Url = "http://127.0.0.1:$Port/desktoppmx.html"
if ($Model -ne '') { $Url = $Url + '?model=' + [uri]::EscapeDataString($Model) }
if ($Tune) {
    if ($Url -like '*?*') { $Url = $Url + '&tune=1' } else { $Url = $Url + '?tune=1' }
}

$Chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (-not (Test-Path $Chrome)) { $Chrome = 'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe' }
if (-not (Test-Path $Chrome)) { $Chrome = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' }
if (-not (Test-Path $Chrome)) { $Chrome = 'C:\Program Files\Microsoft\Edge\Application\msedge.exe' }

$Prof = Join-Path $env:LOCALAPPDATA 'PetPreview'
$ChromeArgs = @(
    "--app=$Url",
    "--window-size=$Width,$Height",
    "--window-position=$X,$Y",
    "--user-data-dir=$Prof",
    '--no-first-run',
    '--no-default-browser-check'
)

if (Test-Path $Chrome) {
    Start-Process -FilePath $Chrome -ArgumentList $ChromeArgs | Out-Null
    Write-Host "[ok] $Url"
} else {
    # last resort: default browser (no app chrome, but at least it opens)
    Start-Process $Url
    Write-Host "[ok] $Url (default browser)"
}
