# 小芸桌面 - 停止桌面窗口、后端与语音识别（对话大脑 or_proxy 不受影响）
#
# ★ 2026-10-18 修订：解决"重启时总有残留 -> 新窗口起来就卡死"的问题。
#   旧版在杀完进程后只 Start-Sleep 2 秒就下结论，且**不等进程真正消失**；
#   紧接着的启动脚本会与尚未退干净的旧实例抢「念念」窗口和隐形点击层(catcher)，
#   用户看到的就是：窗口出来了但点不动、拖不动 —— 所谓"卡死"。
#   现在的做法：
#     1) 三类进程（桌面/后端/ASR）全部按「端口 + 命令行 + venv 路径」三路匹配；
#     2) 额外清掉**孤儿 WebView2**（--webview-exe-name=pythonw.exe），它被杀宿主后
#        常常还能活几十秒，占着渲染与 GPU 资源，新实例因此启动极慢/无响应；
#     3) 杀完后**轮询等待进程真正消失**（最多 15 秒），不再拍脑袋 sleep；
#     4) 复核改为「窗口 + 挂起状态 + catcher 数量」，报告具体残留，退出码 4 表示仍有残留。
$ErrorActionPreference = "SilentlyContinue"
$deskPid = "D:\LunaAI\project-nn\xiaoyun\scripts\desktop.pid"
$probePy = "D:\LunaAI\project-nn\xiaoyun\.venv\Scripts\python.exe"
$probeSc = "D:\LunaAI\project-nn\xiaoyun\tools\pet_window_probe.py"

# 小芸进程的三种身份（与 Start-Xiaoyun.ps1 启动的三个服务一一对应）：
#   后端 xiaoyun_server.py (Miniconda python.exe, 8090)
#   ASR  asr_service.py    (.venv-asr pythonw,      8091)
#   桌面 desktop_app.py    (.venv pythonw,          两个窗口)
# ★ 后端曾经被漏掉：它只在"正在监听 8090"时才被杀，若它没绑上端口/卡死/启动失败，
#   就永远留着一个僵尸后端 —— 下次启动会再起一个，Windows 又允许多进程绑同一端口，
#   于是两个后端并存、两个语音消费者抢同一条转写（重复回复）。必须一起兜底。
$stalePatterns = @("*xiaoyun\scripts\xiaoyun_server.py*",
                   "*xiaoyun\scripts\asr_service.py*",
                   "*xiaoyun\scripts\desktop_app.py*",
                   "*tts\GPT-SoVITS\api_v2.py*")

function Get-XiaoyunStale(){
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cl = $_.CommandLine
        if(-not $cl){ return $false }
        # ★ 绝不错杀 shell 自身：诊断/包装脚本的命令行里常常"字面包含"这些路径。
        if($_.Name -in @("powershell.exe","pwsh.exe","cmd.exe","conhost.exe","WindowsTerminal.exe")){ return $false }
        foreach($p in $stalePatterns){ if($cl -like $p){ return $true } }
        return $false })
}

function Get-PortPids($port){
    @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique)
}

# 小芸自己的 WebView2：pywebview 启动的浏览器进程带 --webview-exe-name=pythonw.exe。
# 只匹配这一条，绝不碰 Edge 浏览器或其它应用的 WebView2（它们不是这个参数）。
function Get-OrphanWebView2(){
    $all = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" -ErrorAction SilentlyContinue)
    if($all.Count -eq 0){ return @() }
    $ids = @()
    foreach($p in $all){
        if($p.CommandLine -and ($p.CommandLine -like "*--webview-exe-name=pythonw.exe*")){
            $ids += [int]$p.ProcessId
        }
    }
    if($ids.Count -eq 0){ return @() }
    # 递归收子进程（renderer/gpu/utility 通常不带那些参数）
    $changed = $true
    while($changed){
        $changed = $false
        foreach($p in $all){
            $ppid = [int]$p.ParentProcessId
            $id = [int]$p.ProcessId
            if(($ids -contains $ppid) -and (-not ($ids -contains $id))){
                $ids += $id
                $changed = $true
            }
        }
    }
    return @($ids | Sort-Object -Unique)
}

function Stop-PidList($ids, $label){
    $killed = @()
    foreach($id in @($ids)){
        if(-not $id){ continue }
        $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
        if($proc){
            Write-Host ("  停止{0}，PID = {1}" -f $label, $id)
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
            $killed += [int]$id
        }
    }
    return @($killed)
}

# 等这些 PID 真正消失（不是"发了信号就算完"）
function Wait-ProcGone($ids, $timeoutSec = 15){
    $left = @()
    foreach($id in @($ids)){ if($id){ $left += [int]$id } }
    if($left.Count -eq 0){ return @() }
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while((Get-Date) -lt $deadline){
        $still = @()
        foreach($id in $left){
            if(Get-Process -Id $id -ErrorAction SilentlyContinue){ $still += $id }
        }
        $left = $still
        if($left.Count -eq 0){ break }
        Start-Sleep -Milliseconds 300
    }
    return @($left)
}

function Get-PetState(){
    # 返回 @{ visible; hung; catcher; petcount; ghost }；探测脚本缺失时返回空值
    if(Test-Path $probePy -PathType Leaf){
        try{
            $raw = & $probePy $probeSc --json 2>$null
            if($raw){
                $o = $raw | ConvertFrom-Json
                return @{ visible = [bool]$o.visible; hung = [bool]$o.hung
                          catcher = [int]$o.catcher_windows; petcount = [int]$o.pet_windows
                          ghost = [bool]$o.ghost }
            }
        } catch {}
    }
    return @{ visible = $null; hung = $null; catcher = -1; petcount = -1; ghost = $false }
}

# 0) 与启动脚本互斥：禁止"一边启动一边停止"并行，否则会打出重复实例/半死状态。
#    两个脚本用的是同一个命名互斥量 XiaoyunCtrl。
$mutex = $null
$locked = $false
try{
    $mutex = New-Object System.Threading.Mutex($false, "XiaoyunCtrl")
    $locked = $mutex.WaitOne(10000)      # 停止是"急停"：最多等 10 秒，不能无限等
    if(-not $locked){ Write-Host "[警告] 启动流程正在进行中，仍继续执行停止。" -ForegroundColor Yellow }
} catch {
    $locked = $false                      # 上一个进程异常退出留下的"被遗弃"互斥量
    Write-Host "[提示] 互斥量状态异常，按无锁继续。"
}

$allKilled = @()

# 1) 关闭 Windows 桌面窗口
if(Test-Path $deskPid){
    $p = (Get-Content $deskPid -ErrorAction SilentlyContinue | Select-Object -First 1)
    if($p -and $p.Trim() -match '^\d+$'){
        $allKilled += Stop-PidList @([int]$p.Trim()) "小芸桌面窗口"
    }
    Remove-Item $deskPid -Force -ErrorAction SilentlyContinue
}
$deskProcs = @(Get-Process pythonw -ErrorAction SilentlyContinue |
               Where-Object { $_.Path -like "*xiaoyun\.venv\Scripts*" } |
               Select-Object -ExpandProperty Id)
if($deskProcs.Count -gt 0){
    Write-Host ("停止残留桌面进程（.venv），PID = {0}" -f ($deskProcs -join ','))
    $allKilled += Stop-PidList $deskProcs "桌面"
}

# 2) 关闭后端（8090）：端口 + 命令行双路（防"没绑上端口的僵尸后端"）
$srvIds = @(Get-PortPids 8090)
$srvCmd = @(Get-XiaoyunStale | Where-Object { $_.CommandLine -like "*xiaoyun\scripts\xiaoyun_server.py*" } |
            Select-Object -ExpandProperty ProcessId)
$srvIds = @(@($srvIds) + @($srvCmd) | Sort-Object -Unique)
if($srvIds.Count -gt 0){
    $allKilled += Stop-PidList $srvIds "小芸后端"
} else {
    Write-Host "小芸后端当前没有在运行。"
}

# 3) 关闭语音识别服务（8091：asr_service.py，独立 venv）
$asrIds = @(Get-PortPids 8091)
$asrCmd = @(Get-XiaoyunStale | Where-Object { $_.CommandLine -like "*xiaoyun\scripts\asr_service.py*" } |
            Select-Object -ExpandProperty ProcessId)
$asrIds = @(@($asrIds) + @($asrCmd) | Sort-Object -Unique)
if($asrIds.Count -gt 0){
    $allKilled += Stop-PidList $asrIds "语音识别服务"
} else {
    Write-Host "语音识别服务当前没有在运行。"
}

# 4) 兜底：按命令行清掉任何残留的小芸进程（★ 含后端 xiaoyun_server.py）
#    这一层专门对付"已经没在监听端口、也没有窗口"的实例：
#      · 卡死但没窗口的僵尸实例 -> 会留下 Ghost(未响应) 替身窗口，把鼠标点击全部吃掉
#      · 启动到一半没绑上端口的实例 -> 端口杀法看不见它
#      · pywebview / venv 的"引导父进程"（命令行与子进程完全相同，必须一并清掉）
$stale = Get-XiaoyunStale
if($stale.Count -gt 0){
    $allKilled += Stop-PidList @($stale | Select-Object -ExpandProperty ProcessId) "残留小芸进程"
}
# 桌面还可能留着一个"没窗口但活着"的 pythonw（右键退出后常见），单独按路径再扫一遍
$pw = @(Get-Process pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*xiaoyun\.venv\Scripts*" -or $_.Path -like "*xiaoyun\.venv-asr\Scripts*" } |
        Select-Object -ExpandProperty Id)
if($pw.Count -gt 0){
    $allKilled += Stop-PidList $pw "残留 pythonw"
}

# 5) 孤儿 WebView2：宿主被杀后它常常还能活几十秒，占着渲染/GPU，
#    新实例就会"起来但卡死"。这是本次修订新增的关键一步。
$wv = Get-OrphanWebView2
if($wv.Count -gt 0){
    Write-Host ("清理小芸的 WebView2 渲染进程，PID = {0}" -f ($wv -join ','))
    $allKilled += Stop-PidList $wv "WebView2"
}

# 6) 等它们真正消失（最多 15 秒），而不是"发了信号就当没了"
$allKilled = @($allKilled | Sort-Object -Unique)
$leftProcs = @()
if($allKilled.Count -gt 0){
    Write-Host ("等待 {0} 个进程真正退出（最多 15 秒）…" -f $allKilled.Count)
    $leftProcs = Wait-ProcGone $allKilled 15
    if($leftProcs.Count -gt 0){
        Write-Host ("[补刀] 仍未退出，强制再杀一次：PID = {0}" -f ($leftProcs -join ',')) -ForegroundColor Yellow
        [void](Stop-PidList $leftProcs "滞留进程")
        $leftProcs = Wait-ProcGone $leftProcs 5
    }
}

# 等端口真正释放（监听必须消失）
for($i = 0; $i -lt 20; $i++){
    $busy = @()
    foreach($port in 8090,8091){ $busy += @(Get-PortPids $port) }
    if($busy.Count -eq 0){ break }
    Start-Sleep -Milliseconds 300
}

# 7) 复核：真的都停了吗？
Start-Sleep -Milliseconds 500
$leftPorts = @()
foreach($port in 8090,8091){
    $cc = @(Get-PortPids $port)
    if($cc.Count -gt 0){
        $leftPorts += ("{0}(pid={1})" -f $port, ($cc -join ','))
    }
}
$leftProc = @(Get-XiaoyunStale | Select-Object -ExpandProperty ProcessId)
$leftWv = @(Get-OrphanWebView2)
$pet = Get-PetState

$clean = ($leftPorts.Count -eq 0) -and ($leftProc.Count -eq 0) -and ($leftWv.Count -eq 0) -and ($pet.visible -ne $true)

if($mutex){ try { if($locked){ $mutex.ReleaseMutex() } } catch {}; try { $mutex.Dispose() } catch {} }

if($clean){
    Write-Host "[复核][OK] 端口 8090/8091 已释放，进程与 WebView2 均已退出，桌面窗口已关闭。" -ForegroundColor Green
    Write-Host "[OK] 小芸桌面已停止。" -ForegroundColor Green
    Write-Host "（对话大脑 or_proxy 8787 仍在运行，如需关闭请用桌面「停止OpenRouter代理.bat」）" -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
    exit 0
}

Write-Host "[复核][警告] 仍有残留，请再执行一次本脚本：" -ForegroundColor Yellow
if($leftPorts.Count -gt 0){ Write-Host ("  · 端口仍被占用: " + ($leftPorts -join ' ')) -ForegroundColor Yellow }
if($leftProc.Count -gt 0){ Write-Host ("  · 残留小芸进程 pid: " + ($leftProc -join ',')) -ForegroundColor Yellow }
if($leftWv.Count -gt 0){ Write-Host ("  · 残留 WebView2 pid: " + ($leftWv -join ',')) -ForegroundColor Yellow }
if($leftProcs.Count -gt 0){ Write-Host ("  · 杀不掉的进程 pid: " + ($leftProcs -join ',')) -ForegroundColor Yellow }
if($pet.visible -eq $true){ Write-Host "  · 桌面窗口仍然可见" -ForegroundColor Yellow }
if($pet.hung -eq $true){ Write-Host "  · 桌面窗口处于「未响应」状态（这就是下次启动卡死的直接来源）" -ForegroundColor Yellow }
if($pet.catcher -gt 1){ Write-Host ("  · 存在 {0} 个隐形点击层窗口（应<=1，多出来的会吞掉鼠标点击）" -f $pet.catcher) -ForegroundColor Yellow }
if($pet.ghost){ Write-Host "  · 检测到「念念 (未响应)」Ghost 替身窗口：它由卡死进程留下，通常几秒后自行消失" -ForegroundColor Yellow }

Write-Host "[OK] 已执行停止，但未完全清干净（退出码 4）。" -ForegroundColor Yellow
Start-Sleep -Seconds 3
exit 4
