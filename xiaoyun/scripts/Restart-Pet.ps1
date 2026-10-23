# ============================================================================
#  重启小芸桌宠 —— 智能重启 + 残留闸门
#
#  ★ 为什么要改（两次踩坑）：
#    (1) 旧版只重启桌面壳，"不动后端/ASR"。改完 xiaoyun_server.py / asr_service.py
#        之后点重启，8090/8091 跑的还是老进程，新代码压根没加载 —— 用户体感
#        "改了没用"。实测：后端 02:42:57 启动，源文件 02:49:27 才改完。
#    (2) 更要命的是**残留**：停止脚本发完 Stop-Process 就只 sleep 2 秒下结论，
#        不检查进程是否真的没了。紧接着启动脚本拉起新实例，与尚未退干净的旧实例
#        （含残留的 WebView2 渲染进程、隐形点击层 catcher）抢同一个「念念」窗口，
#        表现就是"窗口出来了但点不动、拖不动 = 卡死"，必须先再停一次才行。
#
#  现在的做法：
#     · 桌面壳：每次都重启（页面 HTML 是启动时从磁盘读的，必须重载）
#     · 后端 / ASR：比较"源文件修改时间"和"进程启动时间"，旧代码或没在跑 -> 完整重启
#     · 完整重启后**独立复核**（端口 / 命令行进程 / WebView2 / 窗口挂起 / catcher 数量），
#       不干净就补杀，最多 3 轮；仍然不干净 -> 拒绝启动并报错（绝不带病启动）
#     · 启动完成用 IsHungAppWindow 判定窗口是否真"活着"（不只是可见）
#     · -Full  无条件完整重启；-Check 只体检不动手
#
#  保留下来的既有设计（都是踩过坑换来的）：
#    1) 环境里同时存在 HTTP_PROXY / http_proxy 时，PowerShell 5.1 的 Start-Process
#       会抛"已添加项"直接失败，进程根本起不来 —— 先清干净。
#    2) 判据不能只看 desktop.pid：进程早期崩溃也会写这个文件。
#    3) 按命令行匹配进程，并显式排除 shell 自身，防止误杀服务或把自己杀掉。
#    4) 启动前先删 desktop.pid，否则旧的 pid 文件会让等待循环"假成功"。
# ============================================================================

param([switch]$Full, [switch]$Check)

$ErrorActionPreference = "Continue"

# ---- 0) 清掉大小写重复的代理变量（否则 Start-Process 直接失败） -------------
foreach ($n in @('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')) {
    [Environment]::SetEnvironmentVariable($n, $null, 'Process')
}

$workdir  = "D:\LunaAI\project-nn\xiaoyun"
$deskPy   = Join-Path $workdir ".venv\Scripts\pythonw.exe"
$deskApp  = Join-Path $workdir "scripts\desktop_app.py"
$pageHtml = Join-Path $workdir "pages\desktoppmx.html"
$srvApp   = Join-Path $workdir "scripts\xiaoyun_server.py"
$asrApp   = Join-Path $workdir "scripts\asr_service.py"
$stopPs   = Join-Path $workdir "scripts\Stop-Xiaoyun.ps1"
$startPs  = Join-Path $workdir "scripts\Start-Xiaoyun.ps1"
$deskPid  = Join-Path $workdir "scripts\desktop.pid"
$deskOut  = Join-Path $workdir "scripts\desktop_out.log"
$deskErr  = Join-Path $workdir "scripts\desktop_err.log"
$runLog   = Join-Path $workdir "scripts\启动日志.txt"
$selfLog  = Join-Path $workdir "restart_log.txt"
$probePy  = Join-Path $workdir ".venv\Scripts\python.exe"
$probeSc  = Join-Path $workdir "tools\pet_window_probe.py"

$t0 = Get-Date

function Write-Step($msg, $color = "Gray") {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Host $msg -ForegroundColor $color
    try {
        if ((Test-Path $selfLog) -and ((Get-Item $selfLog).Length -gt 512KB)) {
            Remove-Item $selfLog -Force -ErrorAction SilentlyContinue
        }
        Add-Content -Path $selfLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {}
}

function Test-Url($url) {
    # 不走系统代理，直连本机
    try {
        $req = [System.Net.WebRequest]::Create($url)
        $req.Proxy = $null
        $req.Timeout = 3000
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return ($code -eq 200)
    } catch { return $false }
}

function Test-Backend { return (Test-Url "http://127.0.0.1:8090/api/health") }
function Test-Asr     { return (Test-Url "http://127.0.0.1:8091/health") }

function Get-PortPids($port) {
    try {
        return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                 Select-Object -ExpandProperty OwningProcess -Unique)
    } catch { return @() }
}

# 窗口状态：可见 / 未响应 / catcher 窗口数。探测脚本缺失时返回空值。
function Get-PetState {
    $empty = [pscustomobject]@{ visible = $null; hung = $null; catcher = -1; ghost = $false }
    if (-not (Test-Path $probePy) -or -not (Test-Path $probeSc)) { return $empty }
    try {
        $raw = & $probePy $probeSc --json 2>$null
        if (-not $raw) { return $empty }
        $o = ($raw | Out-String) | ConvertFrom-Json
        return [pscustomobject]@{
            visible = [bool]$o.visible
            hung    = [bool]$o.hung
            catcher = [int]$o.catcher_windows
            ghost   = [bool]$o.ghost
        }
    } catch { return $empty }
}

function Get-DesktopProcIds {
    $ids = @()
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cl = $_.CommandLine
        if (-not $cl) { return $false }
        if ($_.ProcessId -eq $PID) { return $false }
        if ($_.Name -in @('powershell.exe', 'pwsh.exe', 'cmd.exe', 'conhost.exe',
                          'WindowsTerminal.exe', 'explorer.exe', 'wscript.exe')) { return $false }
        if ($cl -like '*xiaoyun\scripts\xiaoyun_server.py*') { return $false }
        if ($cl -like '*xiaoyun\scripts\asr_service.py*')   { return $false }
        return ($cl -like '*xiaoyun\scripts\desktop_app.py*')
    })
    foreach ($p in $procs) { $ids += [int]$p.ProcessId }

    # desktop.pid 里记的进程也一并算上（需二次核对命令行，防 PID 被系统复用）
    if (Test-Path $deskPid) {
        $raw = (Get-Content $deskPid -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($raw -and $raw.Trim() -match '^\d+$') {
            $oldId = [int]$raw.Trim()
            $rec = Get-CimInstance Win32_Process -Filter "ProcessId=$oldId" -ErrorAction SilentlyContinue
            if ($rec -and $rec.CommandLine -and $rec.CommandLine -like '*xiaoyun\scripts\desktop_app.py*') {
                if ($ids -notcontains $oldId) { $ids += $oldId }
            }
        }
    }
    return @($ids | Sort-Object -Unique)
}

function Get-ListeningPid($port) {
    try {
        $c = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($c.Count -gt 0) { return (($c | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ',') }
    } catch {}
    return ''
}

function Get-XiaoyunProcIds {
    # 四种身份：后端 / ASR / 语义检索 embed / 桌面（按命令行）
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cl = $_.CommandLine
            if (-not $cl) { return $false }
            if ($_.ProcessId -eq $PID) { return $false }
            if ($_.Name -in @('powershell.exe', 'pwsh.exe', 'cmd.exe', 'conhost.exe',
                              'WindowsTerminal.exe', 'explorer.exe', 'wscript.exe')) { return $false }
            ($cl -like '*xiaoyun\scripts\xiaoyun_server.py*') -or
            ($cl -like '*xiaoyun\scripts\asr_service.py*')   -or
            ($cl -like '*xiaoyun\scripts\embed_service.py*') -or
            ($cl -like '*xiaoyun\scripts\desktop_app.py*')
        } | Select-Object -ExpandProperty ProcessId)
    } catch { return @() }
}

function Get-OrphanWebView2 {
    # 小芸自己的 WebView2：pywebview 启动的浏览器进程带 --webview-exe-name=pythonw.exe。
    # 只匹配这一条，绝不碰 Edge 浏览器或其它应用的 WebView2。
    try {
        $all = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" -ErrorAction SilentlyContinue)
        if ($all.Count -eq 0) { return @() }
        $ids = @()
        foreach ($p in $all) {
            if ($p.CommandLine -and ($p.CommandLine -like "*--webview-exe-name=pythonw.exe*")) {
                $ids += [int]$p.ProcessId
            }
        }
        if ($ids.Count -eq 0) { return @() }
        $changed = $true
        while ($changed) {
            $changed = $false
            foreach ($p in $all) {
                $ppid = [int]$p.ParentProcessId
                $id = [int]$p.ProcessId
                if (($ids -contains $ppid) -and (-not ($ids -contains $id))) {
                    $ids += $id
                    $changed = $true
                }
            }
        }
        return @($ids | Sort-Object -Unique)
    } catch { return @() }
}

function Stop-PidList($ids, $label) {
    $killed = @()
    foreach ($id in @($ids)) {
        if (-not $id) { continue }
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            Write-Step ("      停止{0} PID={1}" -f $label, $id)
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
            $killed += [int]$id
        }
    }
    return @($killed)
}

function Wait-ProcGone($ids, $timeoutSec = 15) {
    $left = @()
    foreach ($id in @($ids)) { if ($id) { $left += [int]$id } }
    if ($left.Count -eq 0) { return @() }
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $still = @()
        foreach ($id in $left) {
            if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $still += $id }
        }
        $left = $still
        if ($left.Count -eq 0) { break }
        Start-Sleep -Milliseconds 300
    }
    return @($left)
}

# ---- 子脚本调用：绝对不用管道 -----------------------------------------------
# ★ 实测踩坑（2026-10-18 03:14）：原来写成
#       & powershell.exe -File $startPs | ForEach-Object { Write-Host ... }
#   启动脚本自己早在 03:15:24 就打印了「==== 启动流程结束 ====」并退出，可重启脚本
#   永远停在「-> 启动…」不动，看起来就是"卡死"。真正的原因不是脚本慢，是管道：
#   Start-Xiaoyun 用 Start-Process 拉起常驻 pythonw 时 bInheritHandles=TRUE，
#   pythonw 顺手把父进程的 stdout（也就是我们这条管道的写端）一起继承了；
#   它是常驻进程、永远不退出，写端就永远不关，管道读端等不到 EOF，
#   ForEach-Object 于是永久阻塞。Windows 上是经典坑。
#   现在改成：cmd 做重定向把子进程输出写文件（给的是文件句柄，不是管道），
#   父进程只等 cmd 进程退出（带超时），输出事后再读文件回放。
function Invoke-ChildScript($path, $label, $timeoutSec = 240) {
    $tag    = "{0}_{1}_{2}" -f $label, $PID, (Get-Random)
    $tmpOut = Join-Path $env:TEMP ("xy_{0}.out.log" -f $tag)
    $tmpErr = Join-Path $env:TEMP ("xy_{0}.err.log" -f $tag)
    $ok = $false
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $cmdLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $path + '" 1> "' + $tmpOut + '" 2> "' + $tmpErr + '"'
        # ★ 这里刻意**不用** -RedirectStandardOutput：Start-Process 一旦重定向，内部会建管道
        #   并靠异步泵把输出搬到文件；若常驻孙进程（pythonw / WebView2 / catcher）继承了
        #   管道写端，泵就等不到 EOF，cmdlet 本身会卡住 —— 等于把老问题换个地方复发。
        #   所以让 cmd 自己用 1>/2> 写文件（文件句柄），我们只等进程退出。
        $p = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $cmdLine) -WindowStyle Hidden -PassThru -ErrorAction Stop
        $ok = $p.WaitForExit($timeoutSec * 1000)
        if (-not $ok) {
            Write-Step ("      [超时] {0} 超过 {1}s 未结束，强制终止后继续判定" -f $label, $timeoutSec) "Yellow"
            & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
            Start-Sleep -Milliseconds 800
        }
    } catch {
        Write-Step ("      [警告] 调用 {0} 失败：{1}" -f $label, $_.Exception.Message) "Yellow"
    }
    $sw.Stop()
    foreach ($f in @($tmpOut, $tmpErr)) {
        if (Test-Path $f) {
            Get-Content $f -ErrorAction SilentlyContinue | ForEach-Object {
                $s = [string]$_
                if ($s.Trim().Length -gt 0) { Write-Host ("      " + $s) -ForegroundColor DarkGray }
            }
        }
    }
    Write-Step ("      <- {0} 已返回（用时 {1}s）" -f $label, [math]::Round($sw.Elapsed.TotalSeconds, 1)) "DarkGray"
    Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
    return $ok
}

# 等窗口"可见且未挂起"：返回 ready / visible / hung / catcher
function Wait-PetReady($timeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $st = Get-PetState
        if ($st.visible -eq $true) {
            if ($st.hung -ne $true) {
                return [pscustomobject]@{ ready = $true; visible = $true; hung = $false; catcher = $st.catcher }
            }
            # 刚起来时短暂"未响应"属正常，给它最多 10 秒缓一下
            for ($k = 0; $k -lt 10; $k++) {
                Start-Sleep -Seconds 1
                $st2 = Get-PetState
                if ($st2.visible -eq $true -and $st2.hung -ne $true) {
                    return [pscustomobject]@{ ready = $true; visible = $true; hung = $false; catcher = $st2.catcher }
                }
            }
            return [pscustomobject]@{ ready = $false; visible = $true; hung = $true; catcher = $st.catcher }
        }
        Start-Sleep -Milliseconds 600
    }
    $st = Get-PetState
    return [pscustomobject]@{ ready = $false
                              visible = ($st.visible -eq $true)
                              hung = ($st.hung -eq $true)
                              catcher = $st.catcher }
}

# 残留体检：端口 + 命令行进程 + 孤儿 WebView2 + 窗口/挂起/catcher
function Get-Residual {
    $desc = @()
    $ports = @()
    foreach ($port in 8090, 8091, 8092) {
        $pids = @(Get-PortPids $port)
        if ($pids.Count -gt 0) { $ports += ("{0}:{1}" -f $port, ($pids -join ',')) }
    }
    $procs = @(Get-XiaoyunProcIds)
    $wv = @(Get-OrphanWebView2)
    $pet = Get-PetState

    if ($ports.Count -gt 0) { $desc += ('端口占用 ' + ($ports -join ' ')) }
    if ($procs.Count -gt 0) { $desc += ('进程 ' + ($procs -join ',')) }
    if ($wv.Count -gt 0)    { $desc += ('WebView2 ' + ($wv -join ',')) }
    if ($pet.visible -eq $true) { $desc += '桌面窗口仍可见' }
    if ($pet.hung -eq $true)    { $desc += '桌面窗口未响应' }
    if ($pet.catcher -gt 1)     { $desc += ('catcher 窗口 x' + $pet.catcher) }

    return [pscustomobject]@{
        Clean = ($desc.Count -eq 0)
        Desc  = $desc
    }
}

function Invoke-ForceCleanup {
    $killed = @()
    $killed += Stop-PidList @(Get-XiaoyunProcIds) "残留小芸进程"
    $killed += Stop-PidList @(Get-OrphanWebView2) "残留 WebView2"
    $killed += Stop-PidList @(Get-PortPids 8090)  "端口 8090 占用者"
    $killed += Stop-PidList @(Get-PortPids 8091)  "端口 8091 占用者"
    $killed += Stop-PidList @(Get-PortPids 8092)  "端口 8092 占用者"
    $killed = @($killed | Sort-Object -Unique)
    if ($killed.Count -gt 0) {
        $left = @(Wait-ProcGone $killed 10)
        if ($left.Count -gt 0) {
            Write-Step ("      [补刀] 仍未退出：PID = {0}" -f ($left -join ',')) "Yellow"
            [void](Stop-PidList $left "滞留进程")
            [void](Wait-ProcGone $left 5)
        }
    }
    Remove-Item $deskPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700
}

function Get-WebView2Roots {
    # 只取 WebView2 的「浏览器主进程」（带 --webview-exe-name=pythonw.exe 的那个）
    try {
        return @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -and ($_.CommandLine -like "*--webview-exe-name=pythonw.exe*") } |
                 Select-Object -ExpandProperty ProcessId)
    } catch { return @() }
}

# 启动后的健康检查：判据与"启动前必须全空"完全不同 ——
#   这里要求"每个服务恰好一份、窗口可用、没有孤儿 WebView2"，
#   而不是"什么都不许有"（否则会把正常运行的进程误报成残留）。
function Get-Health {
    $issues = @()
    foreach ($port in 8090, 8091) {
        $pids = @(Get-PortPids $port)
        if ($pids.Count -eq 0) { $issues += ("端口 {0} 无监听" -f $port) }
        elseif ($pids.Count -gt 1) { $issues += ("端口 {0} 有 {1} 个监听进程（重复实例）" -f $port, $pids.Count) }
    }
    $pet = Get-PetState
    if ($pet.visible -ne $true) { $issues += '窗口不可见' }
    elseif ($pet.hung -eq $true) { $issues += '窗口未响应' }
    if ($pet.catcher -gt 1) { $issues += ('隐形点击层窗口 x' + $pet.catcher + '（应为 1）') }
    # WebView2 只有"父进程已死"的才算残留（正常的那组父进程是当前桌面壳）
    foreach ($root in @(Get-WebView2Roots)) {
        $rec = Get-CimInstance Win32_Process -Filter "ProcessId=$root" -ErrorAction SilentlyContinue
        if (-not $rec) { continue }
        $ppid = [int]$rec.ParentProcessId
        if (-not (Get-Process -Id $ppid -ErrorAction SilentlyContinue)) {
            $issues += ("孤儿 WebView2 PID={0}（父进程 {1} 已退出）" -f $root, $ppid)
        }
    }
    return [pscustomobject]@{ Healthy = ($issues.Count -eq 0); Issues = $issues }
}

function Get-NewestMtime($paths) {
    $newest = [datetime]::MinValue
    foreach ($p in $paths) {
        if (Test-Path $p) {
            $m = (Get-Item $p).LastWriteTime
            if ($m -gt $newest) { $newest = $m }
        }
    }
    return $newest
}

function Get-ProcStartTime($procId) {
    $rec = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if (-not $rec) { return $null }
    try { return [datetime]$rec.CreationDate } catch { return $null }
}

# 组件体检：活着吗、跑的是不是最新代码
function Get-CompInfo($name, $files, $port) {
    $newest = Get-NewestMtime $files
    if ($port -gt 0) { $pids = @(Get-PortPids $port) } else { $pids = @(Get-DesktopProcIds) }
    $alive = ($pids.Count -gt 0)
    $start = $null
    foreach ($p in $pids) {
        $st = Get-ProcStartTime $p
        if ($st -and (($null -eq $start) -or ($st -lt $start))) { $start = $st }
    }
    # 容差 60 秒：只有"源文件比进程晚一分钟以上"才算真跑旧代码。
    # 原来的 1 秒太敏感——实测 03:27:21 刚启动的后端，源文件 mtime 在 03:27:32
    # 被外部（索引器/杀毒/编辑器保存）碰了一下，就被误判成"跑旧代码"，
    # 于是每次正常重启都提示"请再执行一次本脚本"，纯属狼来了。
    # 注意方向：先改代码再重启 -> 进程比文件新 -> 不会误报；
    #           改完代码不重启（过了几分钟）-> 文件明显更新 -> 正确报警。
    $stale = $false
    if ($alive -and $start -and ($newest -gt $start.AddSeconds(60))) { $stale = $true }
    return [pscustomobject]@{
        Name = $name; Alive = $alive; Pids = ($pids -join ',')
        Start = $start; Newest = $newest; Stale = $stale
    }
}

function Format-Time($t) {
    if ($null -eq $t) { return '-' }
    return $t.ToString('MM-dd HH:mm:ss')
}

# ---- 与启动/停止脚本互斥，避免"一边启动一边重启" --------------------------
$mutex = $null
$locked = $false
try {
    $mutex = New-Object System.Threading.Mutex($false, "XiaoyunCtrl")
    $locked = $mutex.WaitOne(30000)
    if (-not $locked) { Write-Step "[提示] 启动/停止流程正在进行，仍继续执行。" "Yellow" }
} catch { $locked = $false }

function Release-CtrlMutex {
    # 完整重启要调 Stop/Start 两个脚本，它们也要拿同一个互斥量；先放手，
    # 否则子脚本会各自白等 10 秒才继续。
    if ($script:locked -and $script:mutex) {
        try { $script:mutex.ReleaseMutex() } catch {}
        $script:locked = $false
    }
}

try {
    Write-Host ""
    Write-Host "=== 重启小芸（自动判断范围 + 残留闸门）===" -ForegroundColor Magenta

    if (-not (Test-Path $deskPy)) {
        Write-Step "[错误] 找不到 $deskPy，请确认项目完整。" "Red"
        exit 2
    }

    # ---- 1) 体检 ---------------------------------------------------------
    $srv  = Get-CompInfo "后端 8090"  @($srvApp)             8090
    $asr  = Get-CompInfo "ASR  8091"  @($asrApp)             8091
    $desk = Get-CompInfo "桌面壳"     @($deskApp, $pageHtml) 0
    $pet0 = Get-PetState

    Write-Host "[1/4] 组件体检（源文件修改时间 vs 进程启动时间）" -ForegroundColor Gray
    foreach ($c in @($srv, $asr, $desk)) {
        if (-not $c.Alive) { $stateTxt = '未运行'; $color = 'Yellow' }
        elseif ($c.Stale)  { $stateTxt = '跑的是旧代码'; $color = 'Red' }
        else               { $stateTxt = '最新'; $color = 'DarkGreen' }
        $pidTxt = '-'
        if ($c.Pids) { $pidTxt = $c.Pids }
        Write-Step ("      {0} | PID={1} | 启动={2} | 源码={3} -> {4}" -f `
                    $c.Name, $pidTxt, (Format-Time $c.Start), (Format-Time $c.Newest), $stateTxt) $color
    }
    if ($pet0.visible -eq $true) {
        $hungTxt = '正常'
        $hangColor = 'DarkGray'
        if ($pet0.hung -eq $true) { $hungTxt = '未响应（卡死）'; $hangColor = 'Red' }
        Write-Step ("      窗口 | 可见=True | 状态={0} | catcher={1}" -f $hungTxt, $pet0.catcher) $hangColor
    }

    # embedding 走 Ollama（8092 自建服务已停用），它是共享服务、不归小芸管，
    # 所以既不参与体检也不触发重启；它没起时后端自动退回关键词检索。
    $needFull = ($Full -or $srv.Stale -or $asr.Stale -or (-not $srv.Alive) -or (-not $asr.Alive) -or ($pet0.hung -eq $true))

    if ($Check) {
        if ($needFull) {
            Write-Step "[仅检查] 会执行【完整重启】（后端 + ASR + 桌面）" "Yellow"
        } else {
            Write-Step "[仅检查] 会执行【仅重启桌面壳】" "DarkGreen"
        }
        exit 0
    }

    if ($needFull) {
        # ---- 2A) 完整重启（Stop -> 复核 -> 补杀 -> Start）------------------
        $why = @()
        if ($Full) { $why += '指定 -Full' }
        if ($srv.Stale) { $why += '后端代码已更新' }
        if ($asr.Stale) { $why += 'ASR 代码已更新' }
        if (-not $srv.Alive) { $why += '后端未运行' }
        if (-not $asr.Alive) { $why += 'ASR 未运行' }
        if ($pet0.hung -eq $true) { $why += '窗口已卡死' }
        Write-Host ("[2/4] 完整重启（{0}）" -f ($why -join '、')) -ForegroundColor Yellow

        foreach ($f in @($stopPs, $startPs)) {
            if (-not (Test-Path $f)) {
                Write-Step ("[失败] 缺少 {0}，无法完整重启。请手动「停止小芸桌面」再「启动小芸桌面」。" -f $f) "Red"
                exit 2
            }
        }

        Release-CtrlMutex
        Write-Step "      -> 停止（后端 + ASR + 桌面；会等进程真正退出）…"
        [void](Invoke-ChildScript $stopPs "stop" 120)

        # ★ 独立复核：不信停止脚本的自报，自己再查一遍端口/进程/WebView2/窗口
        $clean = $false
        for ($round = 1; $round -le 3; $round++) {
            $res = Get-Residual
            if ($res.Clean) { $clean = $true; break }
            Write-Step ("      [复核{0}/3] 仍有残留：{1}" -f $round, ($res.Desc -join '；')) "Yellow"
            Invoke-ForceCleanup
        }
        if (-not $clean) {
            $res = Get-Residual
            Write-Step ("[失败] 残留清不干净（{0}），已中止启动。" -f ($res.Desc -join '；')) "Red"
            Write-Step "       带着旧实例启动必然卡死，所以宁可不启动。建议注销或重启系统后再试。" "Yellow"
            exit 4
        }
        Write-Step "      复核通过：端口 / 进程 / WebView2 / 窗口 均已清空" "DarkGreen"

        Write-Step "      -> 启动（or_proxy -> 后端 -> ASR -> 桌面，约 30~60 秒）…"
        [void](Invoke-ChildScript $startPs "start" 240)

        $srvOk = Test-Backend
        if (-not $srvOk) {
            for ($i = 0; $i -lt 20; $i++) { Start-Sleep -Seconds 1; if (Test-Backend) { $srvOk = $true; break } }
        }
        $asrOk = Test-Asr
        if (-not $asrOk) {
            for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Seconds 1; if (Test-Asr) { $asrOk = $true; break } }
        }
    } else {
        # ---- 2B) 只重启桌面壳（快路径）------------------------------------
        Write-Host "[2/4] 后端与 ASR 都是最新代码，只重启桌面壳" -ForegroundColor DarkGreen

        $srvPidBefore = Get-ListeningPid 8090
        $asrPidBefore = Get-ListeningPid 8091

        $oldIds = Get-DesktopProcIds
        if ($oldIds.Count -eq 0) {
            Write-Step "      当前没有桌面进程在跑，直接启动"
        } else {
            Write-Step ("      关闭桌面进程 PID={0}" -f ($oldIds -join ','))
            [void](Stop-PidList $oldIds "桌面")
            $left = @(Wait-ProcGone $oldIds 12)
            if ($left.Count -gt 0) {
                Write-Step ("      [补刀] 仍未退出：PID = {0}" -f ($left -join ',')) "Yellow"
                [void](Stop-PidList $left "桌面")
                [void](Wait-ProcGone $left 5)
            }
        }

        # 顺带清掉上一轮遗留的 WebView2（它活得更久，会把新实例拖到卡死）
        $wv = @(Get-OrphanWebView2)
        if ($wv.Count -gt 0) {
            [void](Stop-PidList $wv "残留 WebView2")
            [void](Wait-ProcGone $wv 10)
        }

        # 旧 pid 文件必须清掉，否则下面会「假成功」
        Remove-Item $deskPid -Force -ErrorAction SilentlyContinue
        Remove-Item $deskOut, $deskErr -Force -ErrorAction SilentlyContinue

        # 等窗口与 catcher 真的消失，再起新实例（否则新窗口会被残留点击层压住 = 点不动）
        for ($i = 0; $i -lt 20; $i++) {
            $st = Get-PetState
            if (($st.visible -ne $true) -and ($st.catcher -le 0)) { break }
            Start-Sleep -Milliseconds 500
        }

        Write-Step "      启动桌面窗口（首次加载约 10~40 秒）…"
        Start-Process -FilePath $deskPy -ArgumentList "`"$deskApp`"" -WorkingDirectory $workdir `
            -RedirectStandardOutput $deskOut -RedirectStandardError $deskErr -WindowStyle Hidden

        $pidOk = $false
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Path $deskPid) { $pidOk = $true; break }
        }
        $newPid = ''
        if ($pidOk) {
            $newPid = (Get-Content $deskPid -ErrorAction SilentlyContinue | Select-Object -First 1)
        }
        $srvOk = $true
        $asrOk = $true
        if (-not $pidOk) {
            Write-Step "[失败] 90 秒内没有拉起桌面进程" "Red"
            if (Test-Path $deskErr) {
                Get-Content $deskErr -Tail 15 -ErrorAction SilentlyContinue |
                    ForEach-Object { Write-Host ("       " + $_) -ForegroundColor DarkGray }
            }
            try {
                $line = "[{0}] 重启桌宠 失败（桌面进程未起），后端 {1}/{2}，ASR {3}/{4}" -f `
                        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $srvPidBefore, (Get-ListeningPid 8090), `
                        $asrPidBefore, (Get-ListeningPid 8091)
                Add-Content -Path $runLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
            } catch {}
            exit 3
        }
        Write-Step ("      进程已就绪 PID={0}，等窗口出现并可用…" -f $newPid)

        if ($srvPidBefore -and $srvPidBefore -eq (Get-ListeningPid 8090)) {
            Write-Step ("      后端 8090 未受影响（PID 仍是 {0}）" -f $srvPidBefore) "DarkGray"
        }
        if ($asrPidBefore -and $asrPidBefore -eq (Get-ListeningPid 8091)) {
            Write-Step ("      语音识别 8091 未受影响（PID 仍是 {0}）" -f $asrPidBefore) "DarkGray"
        }
    }

    # ---- 3) 等窗口真正可用（可见 + 未挂起 + 只有一个 catcher）-------------
    # 完整重启前面已经花了不少时间，给 90s；只换桌面壳给 60s。
    $waitSec = 60
    if ($needFull) { $waitSec = 90 }
    $ready    = Wait-PetReady $waitSec
    $winOk    = $ready.ready
    $winHung  = ($ready.visible -and $ready.hung)
    $catcherN = $ready.catcher

    # ★ 自动重试：以前这里只能提示用户"再执行一次脚本"（先停再起）才行 ——
    #   原因是旧 WebView2 / 隐形点击层(catcher)残留把新窗口压住。现在脚本自己做完这一轮。
    if (-not $winOk) {
        Write-Step ("      窗口不可用（可见={0} 挂起={1}），自动清残留并重新启动一轮…" -f $ready.visible, $ready.hung) "Yellow"
        Invoke-ForceCleanup
        [void](Invoke-ChildScript $startPs "start2" 240)
        $ready2   = Wait-PetReady 90
        $winOk    = $ready2.ready
        $winHung  = ($ready2.visible -and $ready2.hung)
        $catcherN = $ready2.catcher
        if ($winOk) { Write-Step "      [自动重试] 成功：窗口已可用" "DarkGreen" }
        else        { Write-Step "      [自动重试] 仍未成功" "Red" }
        # 这一轮把后端/ASR 也一起重启了，重新探活
        $srvOk = Test-Backend
        $asrOk = Test-Asr
    }

    # ---- 4) 结果 ----------------------------------------------------------
    $secs = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    Write-Host ""
    Write-Host ("[3/4] 用时 {0} 秒" -f $secs) -ForegroundColor Gray

    if ($srvOk) { Write-Step "      后端 8090  : 正常" "DarkGreen" }
    else { Write-Step "      后端 8090  : 未响应（见 srv_err.log）" "Red" }
    if ($asrOk) { Write-Step "      语音 8091  : 正常" "DarkGreen" }
    else { Write-Step "      语音 8091  : 未响应（见 asr_err.log）" "Red" }

    if ($winOk) {
        Write-Step ("      桌面窗口   : 已出现且响应正常（catcher={0}）" -f $catcherN) "DarkGreen"
    } elseif ($winHung) {
        Write-Step "      桌面窗口   : [卡死] 窗口存在但「未响应」" "Red"
        Write-Step "       原因通常是旧实例/旧 WebView2 没退干净。请再执行一次本脚本" "Yellow"
        Write-Step "       （它会先做残留清理），仍不行则注销系统后再启动。" "Yellow"
    } else {
        Write-Step "      桌面窗口   : 未出现（见 desktop_err.log）" "Yellow"
        if (Test-Path $deskErr) {
            Get-Content $deskErr -Tail 10 -ErrorAction SilentlyContinue |
                ForEach-Object { Write-Host ("       " + $_) -ForegroundColor DarkGray }
        }
    }

    # 复核"真的加载了新代码"：进程启动时间必须晚于源文件修改时间
    $srvNow  = Get-CompInfo "后端 8090" @($srvApp) 8090
    $asrNow  = Get-CompInfo "ASR  8091" @($asrApp) 8091
    $deskNow = Get-CompInfo "桌面壳"    @($deskApp, $pageHtml) 0
    $allFresh = ((-not $srvNow.Stale) -and (-not $asrNow.Stale) -and (-not $deskNow.Stale))
    if ($allFresh) {
        Write-Step "      代码版本   : 三个组件都已是修改后的最新代码" "Green"
    } else {
        $bad = @()
        if ($srvNow.Stale)  { $bad += '后端' }
        if ($asrNow.Stale)  { $bad += 'ASR' }
        if ($deskNow.Stale) { $bad += '桌面' }
        Write-Step ("      代码版本   : 仍有组件跑旧代码 -> {0}（请再执行一次本脚本）" -f ($bad -join '、')) "Red"
    }

    # 收尾复核：每个服务恰好一份、窗口可用、没有孤儿 WebView2
    $health = Get-Health
    if ($health.Healthy) {
        Write-Host "[4/4] 收尾复核：全部唯一、无孤儿 WebView2、窗口响应正常" -ForegroundColor DarkGreen
    } else {
        Write-Step ("[4/4] 收尾复核发现问题：{0}" -f ($health.Issues -join '；')) "Yellow"
    }

    if ($winOk -and $srvOk) { Write-Step "[OK] 念念已就绪" "Green" }

    try {
        $resultTxt = if ($winOk -and $srvOk) { '成功' } else { '异常' }
        $scopeTxt  = if ($needFull) { '（完整重启）' } else { '（仅桌面壳）' }
        $line = "[{0}] 重启桌宠{1} {2}，用时 {3}s，后端={4} ASR={5} 窗口={6} 挂起={7} 代码最新={8} 唯一实例={9}" -f `
                (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $scopeTxt, $resultTxt, $secs, `
                $srvOk, $asrOk, $winOk, $winHung, $allFresh, $health.Healthy
        Add-Content -Path $runLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {}

    if ($winOk -and $srvOk) { exit 0 } else { exit 3 }
}
finally {
    if ($script:locked -and $script:mutex) { try { $script:mutex.ReleaseMutex() } catch {} }
    if ($script:mutex) { try { $script:mutex.Dispose() } catch {} }
}
