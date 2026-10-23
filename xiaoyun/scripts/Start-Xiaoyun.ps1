# 小芸桌面 - 一键启动器（Windows 原生桌面窗口）
# 依次：确保对话大脑 or_proxy(8787) 在线 -> 启动小芸后端(8090) -> 弹出桌面伴侣窗口
# 每一步都会写入 D:\LunaAI\xiaoyun\scripts\启动日志.txt，便于排查
$ErrorActionPreference = "SilentlyContinue"

# ---------- 0) 单实例锁：禁止并发启动（★ 防"重复启动"的关键，必须在最前面） ----------
# 为什么必须有：三个服务的"已在运行"判据都是"服务已可用 / 窗口已出现"，而**正在启动中**
# 的服务既不可用、也没窗口 —— 于是第二次运行会把它当成"没在运行"再起一个：
#   · 后端竞态（/api/health 可响应前那几秒）-> 两个后端并存。Windows 允许多进程绑同一
#     端口，结果是两个语音消费者抢同一条转写（重复回复、重复播放）；
#   · ASR 竞态（加载 whisper 模型 10~30s）-> 两个 ASR 抢麦克风（设备被独占，
#     日志里那条 "⚠输入是数字静默(设备休眠/被独占/流失效?)" 就有这个成因）；
#   · 桌面竞态（窗口出现要 15~40s）-> 第二次运行会走进清场，把**还在加载的第一个实例
#     杀掉**再重来，用户体感就是"启动特别久、好像起不来"。
# 用命名互斥量（与 Stop-Xiaoyun.ps1 同名 XiaoyunCtrl）把并发挡在门外：拿不到锁就说明
# 已有流程在进行，直接退出、不重复启动。锁由系统在进程退出时自动释放。
# 位置刻意放在"重置启动日志"之前，并用 Add-Content 追加：这样跳过的那次不会把
# 正在跑的那次的日志截断掉。
$mutex = New-Object System.Threading.Mutex($false, "XiaoyunCtrl")
$hasLock = $false
try{
    $hasLock = $mutex.WaitOne(0)
} catch {
    $hasLock = $true          # 上一个进程异常退出留下的"被遗弃"互斥量：此刻归我们所有
}
if(-not $hasLock){
    $skipMsg = "[{0}] [跳过] 已有一个小芸启动/停止流程正在进行，本次不重复启动。" -f (Get-Date -Format 'HH:mm:ss')
    Add-Content -Path "D:\LunaAI\xiaoyun\scripts\启动日志.txt" -Value $skipMsg -Encoding UTF8
    Write-Host "已有一个启动/停止流程在进行中，等它完成即可（本次不重复启动）。" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 0
}
$py        = "D:\LunaAI\app\AI-Vtuber-20240229\Miniconda3\python.exe"
$srv       = "D:\LunaAI\xiaoyun\scripts\xiaoyun_server.py"
$workdir   = "D:\LunaAI\xiaoyun"
$outLog    = "D:\LunaAI\xiaoyun\logs\srv_out.log"
$errLog    = "D:\LunaAI\xiaoyun\logs\srv_err.log"
$deskPy    = "D:\LunaAI\xiaoyun\.venv\Scripts\pythonw.exe"
$deskApp   = "D:\LunaAI\xiaoyun\scripts\desktop_app.py"
$deskPid   = "D:\LunaAI\xiaoyun\scripts\desktop.pid"
$deskOut   = "D:\LunaAI\xiaoyun\logs\desktop_out.log"
$deskErr   = "D:\LunaAI\xiaoyun\logs\desktop_err.log"
$runLog    = "D:\LunaAI\xiaoyun\scripts\启动日志.txt"

"==== 小芸桌面启动日志 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Set-Content $runLog -Encoding UTF8
function Log($m){
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"
    Add-Content -Path $runLog -Value $line -Encoding UTF8
    Write-Host $line
}
function Test-Url($u){
    try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 3; return ($r.StatusCode -eq 200) }
    catch { return $false }
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "用户: $([Security.Principal.WindowsIdentity]::GetCurrent().Name)   管理员: $isAdmin"
Log "PowerShell: $($PSVersionTable.PSVersion.ToString())"
Log "pythonw.exe 存在: $(Test-Path $deskPy)"
Log "单实例锁已获取（XiaoyunCtrl，进程退出时由系统自动释放）"

# 按命令行识别小芸进程（后端 / ASR / 桌面 三种身份）—— 清场与"防重复"判据都用它
$stalePatterns = @("*xiaoyun\scripts\xiaoyun_server.py*",
                   "*xiaoyun\scripts\asr_service.py*",
                   "*xiaoyun\scripts\embed_service.py*",
                   "*xiaoyun\scripts\desktop_app.py*",
                   "*tts\GPT-SoVITS\api_v2.py*")
function Get-XiaoyunProc($onlyPattern){
    $pats = $stalePatterns
    if($onlyPattern){ $pats = @($onlyPattern) }
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cl = $_.CommandLine
        if(-not $cl){ return $false }
        # ★ 绝不匹配 shell 自身：诊断命令/包装脚本的命令行里常常"字面包含"这些路径，
        #   一旦匹配到就会把正在执行本脚本的 shell 杀掉（实测踩过：日志里出现
        #   "停止残留进程 pid=" 空列表，就是匹配到了一个非目标进程）。
        if($_.Name -in @("powershell.exe","pwsh.exe","cmd.exe","conhost.exe","WindowsTerminal.exe")){ return $false }
        foreach($p in $pats){ if($cl -like $p){ return $true } }
        return $false })
}
function Stop-XiaoyunProc($list, $why){
    # 注意过滤 $null：函数返回空集合时调用方拿到的是 $null，而 @($null).Count = 1，
    # 会导致"明明没找到也打印一行空 pid 的日志"。
    $arr = @($list | Where-Object { $null -ne $_ -and $_.ProcessId })
    if($arr.Count -gt 0){
        Log ("{0}：停止残留进程 pid={1}" -f $why, (($arr | ForEach-Object { $_.ProcessId }) -join ','))
        $arr | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
    return $arr.Count
}

# Win32 助手：判断"窗口是否真的在屏幕上"，这是唯一可信的运行判据
try{
    Add-Type @"
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
public class Wx {
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(IntPtr a, string b);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SystemParametersInfo(int a, int b, out RECT r, int f);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
}
"@ -ErrorAction Stop
} catch { Log "Win32 助手加载失败: $($_.Exception.Message)" }

function Test-PetWindow(){
    try{
        $h = [Wx]::FindWindow([IntPtr]::Zero, "念念")
        if($h -ne 0 -and [Wx]::IsWindowVisible($h)){ return $true }
    } catch {}
    return $false
}

Write-Host "=== 小芸桌面启动中 ===" -ForegroundColor Magenta

# ---------- 1) 对话大脑：OpenRouter 本地代理（or_proxy.py, 8787） ----------
# 2026-10-18：大脑从本地 Ollama(qwen3.5-9b) 切换到 or_proxy（OpenRouter 免费模型池）。
# 原因：8GB 显存要让给 Hunyuan3D 3D 形象生成；Ollama 钉驻显存/预热/保活全部移除。
# 手动方式：双击桌面「启动OpenRouter代理.bat」。
$orPy  = "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
$orApp = "D:\LunaAI\or_proxy.py"
$orOut = "D:\LunaAI\or_proxy_stdout.log"
$orErr = "D:\LunaAI\or_proxy_stderr.log"

function Test-Brain(){
    try{
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8787/health" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if(Test-Brain){
    Log "[OK] 对话大脑 or_proxy 已在线 (8787)"
} elseif(Test-Path $orApp){
    Log "正在启动对话大脑 or_proxy (8787)..."
    Start-Process -FilePath $orPy -ArgumentList "`"$orApp`"" -WorkingDirectory "D:\LunaAI" `
        -RedirectStandardOutput $orOut -RedirectStandardError $orErr -WindowStyle Hidden
    for($i=0; $i -lt 15; $i++){
        Start-Sleep -Seconds 1
        if(Test-Brain){ break }
    }
    if(Test-Brain){ Log "[OK] 对话大脑 or_proxy 已启动" }
    else { Log "[警告] or_proxy 未就绪（可手动双击桌面「启动OpenRouter代理.bat」），文字聊天暂不可用" }
} else {
    Log "[错误] 找不到 $orApp —— 对话大脑不可用"
}

# ---------- 1.5) 清场：干掉残留的"僵尸实例"（★ 重要）----------
# 僵尸实例会留下 Ghost(未响应) 替身窗口，压在隐形点击层之上，把鼠标点击**全部吃掉** ——
# 用户体感就是"点了没反应"，甚至"桌宠起不来"（看到的是那个动弹不得的替身窗口）。
# 按命令行匹配三种身份（后端 / ASR / 桌面），只要当前没有宠物窗口就一律清掉。
# 注意条件：宠物窗口在 = 整套服务大概率是活的，此时**不要**清，免得把正常运行的服务杀掉。
if(-not (Test-PetWindow)){
    [void](Stop-XiaoyunProc (Get-XiaoyunProc $null) "清场")
}

# ---------- 1.8) 本地 embedding：Ollama bge-m3（记忆语义检索） ----------
# 2026-10-19：向量侧从自建 embed_service.py(8092, bge-small/512维/torch)
# 迁到 Ollama bge-m3(1024维)。理由：Ollama 本机已在用，模型统一托管，
# 不再维护一套 torch + sentence-transformers（那套依赖升级时把 transformers
# 顶到 5.x，差点带坏 Hunyuan3D-2）。
# 约束（都是刻意的）：
#   · GPU 推理（后端请求里带 num_gpu=99）：实测只占 664MB 显存、预热后 26ms/次
#     （CPU 是 80ms）。Hunyuan3D 是低频使用，需要腾卡时手动
#     `ollama stop bge-m3` 即可立刻释放，不必改配置；
#     抢不到卡时后端会自己回落 CPU（并在 10 分钟后自动重试 GPU）；
#   · 起不来不影响任何主链路 —— 后端自动退回 FTS5 关键词检索
#     （/api/health 的 memory_semantic 会是 false）；
#   · Ollama 是**共享服务**，不由小芸独占：本脚本只负责"没起就拉起"，
#     Stop 脚本不会杀 11434，避免把用户其它用途的 Ollama 一并停掉。
$ollamaExe = "C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe"
$embModel  = "bge-m3"
function Test-Ollama(){
    try{
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
if(Test-Ollama){
    Log "[OK] Ollama 已在线 (11434)"
} elseif(Test-Path $ollamaExe){
    Log "正在启动 Ollama (11434)..."
    # 不做输出重定向：ollama 自己写 %LOCALAPPDATA%\Ollama\server.log，
    # 一旦重定向，它派生的 runner 子进程会继承管道写端，把父进程卡在等 EOF。
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WorkingDirectory "D:\LunaAI" `
        -WindowStyle Hidden
    for($i=0; $i -lt 20; $i++){
        Start-Sleep -Seconds 1
        if(Test-Ollama){ break }
    }
    if(Test-Ollama){ Log "[OK] Ollama 已启动" }
    else { Log "[警告] Ollama 未就绪，记忆检索退化为关键词（不影响对话）" }
} else {
    Log "[警告] 找不到 ollama.exe，记忆检索退化为关键词（不影响对话）"
}
if(Test-Ollama){
    # 只检查不自动拉取：1.2GB 的模型不该在开机流程里静默下载。
    $tags = ""
    try {
        $tags = (Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 3).Content
    } catch {}
    if($tags -and $tags -notmatch [regex]::Escape($embModel)){
        Log "[警告] Ollama 里没有 $embModel，语义检索不可用；先执行: ollama pull $embModel"
    } else {
        Log "[OK] 语义检索模型就绪: $embModel（GPU 推理，约 664MB 显存）"
    }
}

# ---------- 2) 后端 8090 ----------
if(-not (Test-Url "http://127.0.0.1:8090/api/health")){
    # ★ 健康检查失败**不等于**没有后端进程：可能是"启动到一半没绑上端口"或"卡死"的残留。
    #   不清掉就直接再起一个，就会出现两个后端同时绑 8090（Windows 允许多进程绑同一端口），
    #   两个语音消费者抢同一条转写 -> 重复回复/重复播放。所以先按命令行清干净。
    [void](Stop-XiaoyunProc (Get-XiaoyunProc "*xiaoyun\scripts\xiaoyun_server.py*") "后端健康检查失败")
    Log "正在启动后端 8090..."
    Start-Process -FilePath $py -ArgumentList "`"$srv`"" -WorkingDirectory $workdir `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden
    for($i=0; $i -lt 20; $i++){
        Start-Sleep -Seconds 1
        if(Test-Url "http://127.0.0.1:8090/api/health"){ break }
    }
}
if(Test-Url "http://127.0.0.1:8090/api/health"){ Log "[OK] 后端已启动 (8090)" }
else { Log "[错误] 后端启动失败，见 srv_err.log" }
# ★ 复核"只有一个后端"：8090 上若出现多个监听进程，就是重复实例（会互相抢转写）
$lis = @(Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
         Select-Object -ExpandProperty OwningProcess -Unique)
if($lis.Count -gt 1){
    Log "[警告] 8090 上有多个监听进程 pid=$($lis -join ',') —— 疑似重复后端，建议先运行 Stop-Xiaoyun.ps1 再启动"
}

# ---------- 2.5) 语音识别 8091（本地 ASR，独立 venv，不吃显存） ----------
# 只负责"麦克风 -> 文字"，CPU + int8 推理；没起来也不影响文字聊天，
# 桌宠右下角会提示"语音服务未启动"。
$asrPy  = "D:\LunaAI\xiaoyun\.venv-asr\Scripts\pythonw.exe"
$asrApp = "D:\LunaAI\xiaoyun\scripts\asr_service.py"
$asrLog = "D:\LunaAI\xiaoyun\logs\asr_out.log"
$asrErr = "D:\LunaAI\xiaoyun\logs\asr_err.log"
$asrEnv = @{
    "XIAOYUN_ASR_MODEL" = "D:\LunaAI\xiaoyun\models\faster-whisper-small"
    "HF_HUB_DISABLE_XET" = "1"
    "HF_HUB_OFFLINE" = "1"
    # 用户明确要求用 USB 麦克风（板载 Realtek 收不到他说话）。该设备底噪较高
    # （约 0.0007），故把阈值下限同步抬高到 0.003，减少误触发。
    # ⚠️ 这只 USB 音响的采集端点会拾到它自己的播放声。自听由两道闸挡住：
    #   1) 后端等她**真正播完**（页面上报 audio.duration）才恢复收音；
    #   2) 页面播完 onended 精确上报 /api/voice/resume。
    #   若仍出现自问自答死循环，先把 XIAOYUN_ASR_INPUT 改回 "Realtek"。
    "XIAOYUN_ASR_INPUT" = "USB Audio Device"
    # 触发线 = max(下限, 噪声底 × XIAOYUN_ASR_NOISE_K)。
    # 实测：环境峰值 ~0.0001，用户说话起点 ~0.0023，而**她自己经音箱回授的声音
    # 是 0.0023~0.0112** —— 比任何"还能让用户正常说话"的阈值都高。
    # 所以自听循环只能靠"播放期间不收音 + 播完才恢复"解决，抬阈值救不了它。
    # 这里按用户要求把下限抬到 0.0010（用户说话仍有约 2 倍余量）。
    "XIAOYUN_ASR_THR"      = "0.0010"
    "XIAOYUN_ASR_NOISE_K"  = "1.3"
}
if(-not (Test-Url "http://127.0.0.1:8091/health")){
    if(Test-Path $asrPy){
        # ★ 同上：/health 不通也可能是"加载模型时卡住"的残留 ASR。先清掉再起，
        #   否则两个 ASR 会同时抢麦克风（设备被独占 -> 语音时好时坏）。
        [void](Stop-XiaoyunProc (Get-XiaoyunProc "*xiaoyun\scripts\asr_service.py*") "ASR 健康检查失败")
        Log "正在启动本地语音识别服务 (8091, faster-whisper small / CPU int8)..."
        foreach($k in $asrEnv.Keys){ [Environment]::SetEnvironmentVariable($k, $asrEnv[$k], "Process") }
        Start-Process -FilePath $asrPy -ArgumentList "`"$asrApp`"" -WorkingDirectory $workdir `
            -RedirectStandardOutput $asrLog -RedirectStandardError $asrErr -WindowStyle Hidden
        for($i=0; $i -lt 40; $i++){
            Start-Sleep -Seconds 1
            if(Test-Url "http://127.0.0.1:8091/health"){ break }
        }
    } else {
        Log "[警告] 缺少 .venv-asr\Scripts\pythonw.exe，语音对话不可用（文字聊天不受影响）"
    }
}
if(Test-Url "http://127.0.0.1:8091/health"){ Log "[OK] 语音识别服务已启动 (8091)" }
else { Log "[警告] 语音识别服务未就绪，见 asr_err.log（文字聊天仍可用）" }
$lisAsr = @(Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
if($lisAsr.Count -gt 1){
    Log "[警告] 8091 上有多个监听进程 pid=$($lisAsr -join ',') —— 疑似重复 ASR（会抢麦克风），建议先停止再启动"
}

# ---------- 2.6) 本地声线 GPT-SoVITS (9880) ----------
# ★ 2026-10-19 起**默认不再启动**。GPT-SoVITS 常驻 Private 约 5.3GB，是本机
#   Commit 触顶（曾达 96.4%）的最大单一元凶。改为**动态加载**：后端在用户从托盘
#   菜单切到 gsv: 音色时才按需拉起（见 xiaoyun_server.py 的 gsv_ensure_running），
#   空闲 XIAOYUN_GSV_IDLE_TTL（默认 900s）后自动释放。
# 这里只做"清理残留"，不再负责拉起 —— 否则又变回常驻。
# 需要开机就预热（不介意占内存）时，设 XIAOYUN_GSV_EAGER=1 或改 $EagerGsv = $true。
$EagerGsv = ($env:XIAOYUN_GSV_EAGER -eq "1")

$lisGsv = @(Get-NetTCPConnection -LocalPort 9880 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
if($lisGsv.Count -gt 0){
    if($EagerGsv){
        Log "[OK] 本地声线合成 GPT-SoVITS 已在线 (9880) pid=$($lisGsv -join ',')"
    } else {
        # 按需模式下已有实例 = 上次用完没到空闲期（或别处手工起的），保留它，不重复拉起。
        Log "[OK]检测到已在运行的 GPT-SoVITS (9880) pid=$($lisGsv -join ',')，沿用（按需模式不重复启动）"
    }
} elseif($EagerGsv){
    $gsvPy  = "D:\LunaAI\tts\gsv-venv\Scripts\pythonw.exe"
    $gsvApp = "D:\LunaAI\tts\GPT-SoVITS\api_v2.py"
    $gsvCfg = "D:\LunaAI\tts\gsv_config.yaml"
    $gsvOut = "D:\LunaAI\tts\gsv_out.log"
    $gsvErr = "D:\LunaAI\tts\gsv_err.log"
    function Test-Gsv(){
        try{
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:9880/docs" -UseBasicParsing -TimeoutSec 3
            return ($r.StatusCode -eq 200)
        } catch { return $false }
    }
    if((Test-Path $gsvPy) -and (Test-Path $gsvApp)){
        Log "正在预热本地声线合成 GPT-SoVITS (9880)..."
        Start-Process -FilePath $gsvPy -ArgumentList "`"$gsvApp`" -c `"$gsvCfg`" -a 127.0.0.1 -p 9880" `
            -WorkingDirectory "D:\LunaAI\tts\GPT-SoVITS" `
            -RedirectStandardOutput $gsvOut -RedirectStandardError $gsvErr -WindowStyle Hidden
        for($i=0; $i -lt 90; $i++){
            Start-Sleep -Seconds 1
            if(Test-Gsv){ break }
        }
        if(Test-Gsv){ Log "[OK] GPT-SoVITS 已预热（托盘可切本地声线）" }
        else { Log "[警告] GPT-SoVITS 未就绪，见 gsv_err.log" }
    } else {
        Log "[警告] 缺少 gsv-venv，本地声线不可用（自动用 edge-tts 顶上）"
    }
} else {
    Log "GPT-SoVITS 按需加载：托盘切到本地音色时才启动（省约 5.3GB 内存）"
}

# ---------- 3) 桌面窗口 ----------
# 判据只用"窗口是否真的在屏幕上"：
# 右键退出后常残留一个没有窗口的僵尸 pythonw，若按进程/pi d 判断会被骗，永远起不来。
$already = Test-PetWindow
$procs = @(Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*xiaoyun\.venv\Scripts*" })
Log "小芸窗口存在=$already   残留 pythonw 进程数=$($procs.Count) pid=$($procs.Id -join ',')"

if(-not $already -and $procs.Count -gt 0){
    Log "发现僵尸进程（有进程没窗口），先清理再启动"
    $procs | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Remove-Item $deskPid -ErrorAction SilentlyContinue
}

if($already){
    Log "[OK] 小芸桌面窗口已经在运行了"
} elseif(Test-Path $deskPy){
    Log "正在加载桌面窗口组件（首次 15~40 秒）..."
    # ★ 必须先删掉可能残留的 desktop.pid：下面的等待循环以"pid 文件出现"为成功判据，
    #   若旧文件还在，会立刻"假成功"——明明没起来也报 [OK]，用户看到的就是"起不来"。
    Remove-Item $deskPid -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $deskPy -ArgumentList "`"$deskApp`"" -WorkingDirectory $workdir `
        -RedirectStandardOutput $deskOut -RedirectStandardError $deskErr -WindowStyle Hidden
    $started = $false
    for($i=0; $i -lt 90; $i++){
        Start-Sleep -Seconds 1
        if(Test-Path $deskPid){ $started = $true; break }
    }
    if($started){
        Log "桌面进程已写出 pid 文件：pid=$(Get-Content $deskPid)，继续等窗口真正出现…"
        # ★ pid 文件只说明"进程写了文件"，不等于"窗口真的出来了"（进程早期崩溃也会写）。
        #   唯一可信的判据是宠物窗口可见，所以这里再等一轮窗口。
        $winOk = $false
        for($i=0; $i -lt 60; $i++){
            Start-Sleep -Seconds 1
            if(Test-PetWindow){ $winOk = $true; break }
        }
        if($winOk){ Log "[OK] 桌面窗口已出现" }
        else { Log "[警告] 进程在但没有窗口，见 desktop_err.log（下面的 [复核] 会给结论）" }
    }
    else {
        Log "[错误] 桌面程序未生成 pid 文件，见 desktop_err.log"
        if(Test-Path $deskErr){ Log "[诊断] desktop_err.log 尾部: $((Get-Content $deskErr -Tail 8) -join ' | ')" }
    }
} else {
    Log "[错误] 缺少 .venv\Scripts\pythonw.exe，改为浏览器打开"
    Start-Process "http://127.0.0.1:8090/"
}

# ---------- 4) 复核窗口是否真的出现在屏幕上 ----------
Start-Sleep -Seconds 20
try{
    $h = [Wx]::FindWindow([IntPtr]::Zero, "念念")
    $wa = New-Object RECT
    [void][Wx]::SystemParametersInfo(0x0030, 0, [ref]$wa, 0)
    if($h -ne 0){
        $wr = New-Object RECT
        [void][Wx]::GetWindowRect($h, [ref]$wr)
        $vis = [Wx]::IsWindowVisible($h)
        Log "[复核] 找到窗口 句柄=$h 可见=$vis 位置=($($wr.L),$($wr.T))-($($wr.R),$($wr.B))"
        Log "[复核] 工作区=($($wa.L),$($wa.T))-($($wa.R),$($wa.B))  GetSystemMetrics=$( [Wx]::GetSystemMetrics(0) )x$( [Wx]::GetSystemMetrics(1) )"
        $off = ($wr.R -le $wa.L) -or ($wr.L -ge $wa.R) -or ($wr.B -le $wa.T) -or ($wr.T -ge $wa.B)
        if($off){ Log "[复核][问题] 窗口在可视区之外！" } else { Log "[复核][OK] 窗口在可视区内" }
    } else {
        Log "[复核][问题] 没找到标题为「念念」的窗口"
        $procs = @(Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*xiaoyun\.venv\Scripts*" })
        Log "[复核] pythonw 进程数=$($procs.Count) pid=$($procs.Id -join ',')"
        if(Test-Path $deskErr){ Log "[复核] desktop_err.log 尾部: $((Get-Content $deskErr -Tail 6) -join ' | ')" }
    }
} catch {
    Log "[复核] 窗口复核跳过: $($_.Exception.Message)"
}

Log "==== 启动流程结束 ===="
Write-Host ""
Write-Host "念念会贴在桌面上，可直接拖动；右键她可以看到菜单。" -ForegroundColor Cyan
