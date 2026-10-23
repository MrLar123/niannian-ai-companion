<div align="center">

# 🌙 念念桌宠 project-nn

**一只住在你桌面、会说话、会看你、记得住你的 VRM/PMX 桌宠女友。**

*Windows 原生桌宠：pywebview 透明窗口 + 云端大脑（豆包/DeepSeek/GLM 三上游自动路由）
+ 豆包流式语音（首响 ~0.45s）+ 语音识别三通道 + 屏幕感知 + 长期记忆。*

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)](#快速开始)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.13-3776AB.svg)](#技术栈与结构)
[![Models](https://img.shields.io/badge/%E5%BD%A2%E8%B1%A1-15%20PMX%20%2B%2013%20VRM-9B59B6.svg)](#人物模型库)
[![Persona](https://img.shields.io/badge/%E4%BA%BA%E8%AE%BE-19%20%E5%A5%97-E91E63.svg)](#人设系统)

</div>

## 这是什么

**念念 = VRM/PMX 3D 形象的「陪伴」 × 云端多模态模型的「大脑」 × 本地管线的「隐私兜底」。**

她是一个 Windows 原生桌面应用（不是网页）：一只透明置顶、可拖拽、可点击、可说话的 3D 桌宠待在屏幕角落。
背后由四组进程组成：

```
桌面壳(透明窗口+托盘) ──┐
语音识别 ASR(8091) ────┼──→ 后端 Flask(8090) ──→ or_proxy(8787) ──→ 魔搭/方舟/OpenRouter
屏幕感知(mss+dhash) ───┘         │
                              记忆 SQLite + Ollama embed(11434, 可选)
```

她能陪你聊天（文字+语音全双工）、念台词带情绪（语速/音调随内容变化）、
眼神跟着你的鼠标走、被戳会瘪嘴、跳舞有 15 套动作、还记得你们聊过什么。

## 她能做什么

- 🎭 **形象**：15 只 PMX 模型（模之屋姵儿、甘雨、雷电将军、卡芙卡、千咲…）+ 13 个 VRM/GLB（小芸、小芸-jk、Avaturn 照片重建版）；托盘/右键菜单一键切换，2 秒热生效。
- 💃 **动作**：15 套 VMD 舞蹈（MMD 原生骨名），VRM 页做了骨骼重定向（MMD 左手系 → VRM humanoid），舞蹈期间眼神自动让位。
- 💬 **对话**：or_proxy 自动路由三大上游（详见[对话大脑](#对话大脑-or_proxy)），按实时延迟择优、额度熔断、401 跳档；设置页 `http://127.0.0.1:8090/settings` 可看清单/延迟/用量条形图。
- 🎙️ **语音全双工**：你说 → 她听（VAD 连续对话模式）→ 云端转写 → 她回答 → 流式 TTS 边收边播 → 口型同步 → 播完自动恢复收音。自听死循环有五层防护（见[语音链路](#语音时序铁律)）。
- 😊 **情绪表达**：回复文本命中情绪词库自动调语速/音调（开心 +8%/+2Hz、傲娇 +5%/+3Hz、吃醋 +6%/+1Hz、心疼 0%/-2Hz）；VRM 脸上还有眨眼（0.18s、2~4.5s 随机、30% 连眨）+ 口型五元音。
- 👀 **眼神追随**：壳层 20Hz 轮询光标 → 眼骨最大 YAW 0.26 / PITCH 0.20，死区 46px、参考距离 260px、`1-exp(-dt/τ)` 指数平滑；头部跟随系数 1.6/2.0。
- 👆 **戳一下**：rig.scale 等体积挤压（禁碰 position，三级延迟 躯干 0 / +40ms / +80ms），easeOutBack 过冲曲线；连戳 <800ms 切嫌弃表情。
- 🧠 **记忆**：SQLite FTS5（中文 bigram 索引）+ bge-m3 向量语义检索，RRF 融合排序；情境引擎空闲 900s 后主动搭话。
- 🖥️ **屏幕感知**：每 60s mss 截屏 → dhash 汉明距离 ≤4 去重 → 豆包视觉模型理解你在干嘛（截图只活在内存，不落盘）。
- 🔧 **运维**：托盘五组子菜单（人设/音色/对话模型/人物模型/动作）、`/settings` 网页面板、诊断脚本逐项体检、Swap-Brain 一键换脑。

## 快速开始

```powershell
# 全部依赖已打进包内（runtime/Miniconda3 + 两个 venv），无需安装任何东西
D:\LunaAI\project-nn\xiaoyun\启动小芸桌面.bat     # 双击即全链拉起
D:\LunaAI\project-nn\xiaoyun\停止小芸桌面.bat     # 全链停止
D:\LunaAI\project-nn\xiaoyun\scripts\诊断小芸桌面.bat     # 逐项体检
```

启动链顺序（`Start-Xiaoyun.ps1`）：

1. **or_proxy 8787** 在线检查，不在线自动拉起；
2. **Ollama 11434**（仅记忆语义检索用，不可用自动降级关键词，不影响对话）；
3. **后端 8090**（Miniconda python 跑 xiaoyun_server.py）；
4. **ASR 8091**（.venv-asr 跑 faster-whisper small，CPU int8 不占显存）；
5. **Keep-Ollama** 保活守护（60s 巡检）；
6. **桌面壳**（.venv pythonw 跑 desktop_app.py，透明窗口 + 托盘 + 隐形点击层）。

单实例锁（命名互斥量 `XiaoyunCtrl`）防止重复启动；Stop 脚本按命令行清理全部残留并复核端口释放。

### 前置条件（仅两项）

- **API Key**（写在用户级环境变量，脚本自动继承）：

  | 环境变量 | 用途 | 必需 |
  |---|---|---|
  | `ARK_API_KEY` / `ARK_STANDARD_API_KEY` | 方舟标准通道（豆包/DeepSeek/GLM 对话） | 至少一个上游 |
  | `ARK_PLAN_API_KEY`（或复用 `VOLC_SPEECH_API_KEY`） | 方舟 Agent Plan 通道（视觉理解/生图/embedding） | 屏幕感知需要 |
  | `VOLC_SPEECH_API_KEY` | 豆包 TTS + 豆包流式 ASR | 语音需要 |
  | `MODELSCOPE_API_KEY` | 魔搭 Qwen3.5-27B（免费档） | 可选 |
  | `XIAOYUN_BAIDU_*` | 百度短语音识别兜底 | 可选 |

  > ⚠️ `ARK_API_KEY` 若与 Plan Key 同值，or_proxy 会自动告警并改用内置标准 Key
  > （2026-10-23 串道事故的防御，见 `or_proxy.py` `_resolve_ark_key()`）。

- **屏幕分辨率 ≥ 1080p**：窗口 670×1010（等比可调 `PET_W/PET_H`）。

## 配置云端模型

### 对话大脑（or_proxy）

`or_proxy/or_proxy.py`（单文件、零第三方依赖、Python 3.8+）暴露 OpenAI 兼容端点：

| 端点 | 说明 |
|---|---|
| `POST /v1/chat/completions` | 对话（流式 SSE + 工具调用 + usage 记账） |
| `POST /v1/images/generations` | 生图（AgentPlan seedream-5.0 pro/flash，支持图生图） |
| `GET /v1/models` / `GET /v1/registry` | 模型清单（后者纯本地快照，不做外网探测） |
| `GET /health` | 上游状态、额度熔断、token 用量、EMA 延迟 |

**上游优先级**：`ark（主力额度池）→ arkplan（Plan Key 备援）→ openrouter（兜底）→ modelscope`。

**模型池（2026-10 实测）**：

| 模型 | 实测延迟（中位） | 说明 |
|---|---|---|
| `deepseek-v4-1-flash-economy-261017` | **359ms** | 最快，auto 默认首选 |
| `doubao-seed-character-260628` | 420ms | **人设贴合度最佳**（带动作描写+隐含关心） |
| `doubao-seed-2-0-mini-260428` | 437ms | |
| `doubao-seed-2-1-pro-260915` | 940ms | |
| `deepseek-v4-1-flash-261017` | 1045ms | |
| `deepseek-v4-pro-ga-260813` | 1364ms | |
| `glm-5-3-flash-260828` | 1704ms | |
| `doubao-seed-2-1-lite-260915` | 995ms | 额度告急，末位兜底 |

合计 ≈850 万 tokens/轮转（方舟每模型独立 50 万免费额度），额度耗尽自动熔断跳下一档
（日额度类熔断 1800s、RPM 限流 60s、`record(ok)` 自愈解除）。

**路由机制**：
- `model=auto`：按 EMA（α=0.35）实测延迟择优，不发 model 字段也走此路；
- `stream_options.include_usage` 自动注入（客户端无感拿到 token 记账）；
- reasoning 模型 `content=null` 时自动回填 `reasoning` 字段（响应头 `X-Reasoning-Fallback: 1`）；
- `_should_stop` 只在账户欠费级故障终止整链，401/429/404 均跳档继续。

**改完 or_proxy 后**：双击 `xiaoyun\Swap-Brain.ps1`（或 `_swap_brain.bat`）热换血，其余组件不动。

### TTS（她说）

| 引擎 | 说明 | 切换 |
|---|---|---|
| **豆包 volc**（默认） | seed-tts-2.0，默认 speaker=`zh_female_vv_uranus_bigtts`（当前人设「元气」实际用 `zh_female_xiaohe_uranus_bigtts`），`speech_rate=0` 起步、人设/情绪各自叠加偏移；流式 PCM 24kHz，首响 0.38~0.45s（暖连接） | `XIAOYUN_TTS_ENGINE=volc` |
| **edge-tts** | 保底（豆包挂了她也不哑），音色 `zh-CN-XiaoyiNeural` 等 8 个中文声线 | 自动回退 |
| **GPT-SoVITS** | 本地克隆声线（9880，需另装，本包未含） | `XIAOYUN_TTS_ENGINE=gptsovits` |
| **MiniMax** | 云端御姐系统音色（需充值） | `XIAOYUN_TTS_ENGINE=minimax` |

**流式细节**（`tts_stream.py`）：句号级切段（`max_len=60`，日常一两条回复 = 一次请求，语调完整）；
豆包首包与文本长度几乎无关（连接复用才是关键），共享 `requests.Session` 连接池；
等待大脑期间后台预热 TLS 连接（再省 ~0.2s）。

### ASR（她听）

三通道自动路由（`XIAOYUN_ASR_PROVIDER=auto`）：

1. **豆包流式 ASR**（volc.seedasr.sauc，WebSocket）— 最快；
2. **百度短语音**（`server_api` + `dev_pid=1537`）— 失败自动回落；
3. **faster-whisper small**（本地 CPU int8，2.47s 加载，四道质量门 + 幻觉黑名单 + 回声过滤）。

VAD 阈值自适应：`max(下限, 噪声底×K)`，默认下限 0.0010、K=1.3（`XIAOYUN_ASR_THR` / `XIAOYUN_ASR_NOISE_K`）；
换麦克风用 `XIAOYUN_ASR_INPUT="设备名子串"`（注意 USB 音响的采集端点会回环系统播放，**不能当输入**）。

### 视觉（她看）

`perception.py`：mss 截屏 → 9x8 dhash（汉明距离 ≤4 视为同画面）→ 变化才发
AgentPlan `doubao-seed-2.1-lite`（OpenAI 兼容 `/api/plan/v3`，原生多模态）。
截图压到最长边 960、**只活在内存**（算完 dhash 与 JPEG 即弃）；前景窗用 ctypes 读系统 API，零第三方。

## 人设系统

19 套人格 JSON（`xiaoyun/人设/`），每套 = 人设文本 + 引擎 + 声线 + 语速/音调/情绪：

| key | 标签 | key | 标签 |
|---|---|---|---|
| `yuanqi`（当前） | 元气 | `gaoleng-youhuo` | 高冷诱惑御姐 |
| `aojiao` | 傲娇 | `dushe` | 毒舌 |
| `wenrou` | 温柔 | `nvwang` | 女王 |
| `sanwu` | 三无 | `xiaotianshi` | 小天使 |
| `xuejie` | 学姐 | `linjiajiejie` | 邻家姐姐 |
| `mensao` | 闷骚 | `fuhei` | 腹黑 |
| `jingtai` | 静态 | `nianren` | 年上 |
| `tiaopi` | 调皮 | `xiaoemo` | 小恶魔 |
| `yisheng` | 医生 | `yonglan` | 慵懒 |
| `gaogongdifang` | 高攻低防 | | |

切换：托盘「人设（切换人格）」子菜单 radio，或 `POST /api/preset {"key":"..."}`，热生效 + 持久化到 `current.txt`。
音色独立于人设（`voice_override.json`），可混搭。

**舞台指令禁令**：模型爱输出「（歪头）」这类括号动作，TTS 念出来非常出戏。
修法是双层——`STAGE_HINT` 注入 messages 末尾（离生成最近约束力最强）+
`_strip_stage_text()` 在 `_publish_reply()` 唯一出口统一清洗
（剥 ≤40 字的中英圆/方/六角括号 + `<>` + `*..*`；整条全是动作时返回原文，宁念不哑）。

## 人物模型库

`xiaoyun/assets/`：

- **PMX（15 只）**：`assets/models/<key>/`，MMD 原生格式，MMDLoader 加载。
  每只带 `model.json`（yaw/margin/nudge/arm_drop + gamma/contrast/saturate/bright 调色，`/pmxtune` 面板实时调）。
  键名：elysia / feixue / feixue2 / ganyu / gengchen / huangquan / kafka / kafka_yoga / luotianyi / qianxi / raiden / rover / wuluo / xiaoyu / yowane。
- **VRM（13 个）**：`assets/*.vrm`，three-vrm 加载。当前主力 `小芸.vrm`（54 人形骨全手指、14 表情、SpringBone）。
  菜单白名单 `VRM_MENU_KEYS = {"小芸","小芸-jk"}`，新模型写 `assets/<名>.json` 的 `{"in_menu":true}` 放行。
- **Avaturn 照片重建版**：`avaturn_nn.vrm`（187 morph = ARKit52 + 16 viseme + jaw，54 骨含眼球骨）。
- **动作库**：`assets/motions/*.vmd` 15 套，MMD 日文骨名；VRM 页做重定向
  （每帧先全骨铺基准姿态再叠轨道 + ARM_DROP 预烘 + post-multiply 叠加，防"没轨道的骨停在 T-pose"）。

**VRM 两条铁律**（违反即坏）：
1. `vrm.update(dt)` 必须传 deltaTime 而非绝对秒数——弹簧骨逐帧叠 `stiffness*delta`，喂绝对时间会把发丝顶飞；
2. **禁止缩放模型本体**——stiffness/gravityPower/collider hitRadius 都是世界单位，缩小模型 = 相对刚度和碰撞半径放大 1/s 倍（1.62m 缩 0.616 → 刘海被顶离头部）。要适配尺度调相机距离。

**皮肤色调**：`SKIN_TINT_TABLE` 按模型文件名标定（线性空间值，`?tint=`/`?facetint=`/`?skin=0` 可调），
新模型务必用 `?tint=` 标一次再登记。

## 后端 API 一览（66 个端点）

完整路由见 `xiaoyun_server.py`。高频端点：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/chat` | POST | 一轮对话（大脑→清洗→入库→TTS），返回 `{reply, seq, audio}` |
| `/api/chat_stream` | POST | 流式版（`/streamdemo` 试听台） |
| `/api/last?since=N` | GET | 轮询新回复（seq 广播 + 播放权认领，防双页同播） |
| `/api/history` | GET | 最近 24 条 |
| `/api/health` | GET | 大脑/TTS/GSV/记忆全量状态 |
| `/api/preset` | GET/POST | 人设列表/切换 |
| `/api/models` `/api/model` | GET/POST | 形象清单/切换（PMX+VRM 混合发现） |
| `/api/motions` `/api/motion` | GET/POST | 动作库/切换 |
| `/api/chat-model` | GET/POST | 对话模型（顺序表 ∩ or_proxy 实况，防静默回退） |
| `/api/voice/start\|stop\|playing\|resume\|state` | POST/GET | 语音时序（见下） |
| `/api/usage` | GET | 各模型 token 用量条形图数据 |
| `/api/gaze` `/api/gaze/cursor` | GET/POST | 眼神追随（壳 20Hz 推光标） |
| `/api/poke` | POST | 戳一下（归一化 xy） |
| `/api/grade` | GET/POST | 模型调色（实时生效，save 落盘） |
| `/api/ui/rects` | POST/GET | 页面控件坐标（壳轮询，**不走 JS 桥**——同步回环会堵死 WebView2 UI 线程） |
| `/api/memory` `/api/memory/clear` `/api/reset` | GET/POST | 记忆查询/清空/软重置（reset 不删长期记忆） |
| `/settings` `/streamdemo` `/pmxtune` | GET | 设置页 / 流式试听台 / 调色面板 |

### 语音时序铁律

这是本项目踩了五轮自听死循环换来的硬约束，**改语音相关代码前必读**：

1. 页面必须周期 `POST /api/voice/playing {seq, dur}` 上报真实播放时长（`audio.duration`），
   漏报 = 后端退字数估算 → 卡「准备开口」；
2. `/api/voice/resume` 在 busy 下必须委派（返回 `delegated`），开麦由收尾闸 `start+dur+0.8s` 决定；
3. 主动开口必须走 `_publish_reply()` 共用函数（SEQ/GUARD/PLAYBACK 三件套），禁止复制发布逻辑；
4. 播放期间**真的关麦**（`_pause_stream() + _drop_queue()`），不是改状态；
5. `PLAYBACK` 每轮 `do_chat()` 清零——读上一条记录 = 兜底瞬间成立 = 自听。

## 功能细节说明

### 眼神追随（鼠标跟随）

她始终用余光注意你的光标——这是"活着"最重要的一个信号。

**链路**：壳层 `CursorPoller`（20Hz）→ `GetCursorPos` 读全局光标 → `POST /api/gaze/cursor` → 页面 150ms 轮询 `/api/gaze` → `GazeController` 写眼骨 + 头骨。

**为什么是 20Hz 轮询而不是鼠标钩子**：WH_MOUSE_LL 系统钩子回调跑在 Python 线程里，
一旦被 GIL 拖住整个系统鼠标都会卡死（实测复现过，已永久禁用）；
`GetCursorPos` 只是普通 API 读取，零风险。

**为什么坐标走后端不走 pywebview JS 桥**：JS 桥是同步回环调用，要在 WebView2 UI 线程执行；
高频推坐标实测把 UI 线程堵死 → 桌宠整体"未响应" → DWM 生成 Ghost 窗吞掉所有点击。
所以坐标、控件位置、光标三类高频数据全部走本地 HTTP。

**参数**（`desktopvrm.html` 顶部可调）：

| 参数 | 值 | 说明 |
|---|---|---|
| `GAZE_POLL_MS` | 150 | 页面拉坐标周期 |
| `GAZE_YAW_MAX / PITCH_MAX` | 0.26 / 0.20 | 眼骨极限转角（弧度） |
| `GAZE_DEAD_PX` | 46 | 死区：光标离眼中心 <46px 不动（防抖） |
| `GAZE_REF_PX` | 260 | 参考距离：光标移 260px 眼睛转满 |
| `GAZE_TAU` | 0.10 | 指数平滑时间常数（`1-exp(-dt/τ)`，快而稳） |
| `GAZE_HEAD_YAW / PITCH` | 1.60 / 2.00 | 头部跟随系数（眼动 > 头动，自然层次） |

**三档模式**（托盘「眼神追随」菜单切换，`data/gaze_mode.txt` 持久化）：
`always`（默认，永远看）/ `idle`（只在待机时看，跳舞时让位）/ `off`。
舞蹈期间强制让位——否则头部跟踪会跟 VMD 头部轨道打架，头乱抖。

### 屏幕自动扫描（感知链路）

她每 60 秒看一眼你的屏幕，知道你在忙什么，并据此反应。

**管线**（`perception.py`，设计借鉴 whalepal 并在头注署名）：

```
mss 截屏 → 最长边压到 960 → dhash(9x8=64bit)
   ├─ 汉明距离 ≤4（同画面）→ 不重发，只累加当前活动 duration
   └─ 画面变了 → JPEG base64 + 前景窗标题/进程名 → 豆包 doubao-seed-2.1-lite
        → 结构化 JSON：{activity, category, isWorking, signal, speak, detail, note}
        → 入 events_store（事件流）→ 驱动反应/主动搭话
```

**13 类活动分类**：coding / office / writing / design / meeting / reading / video /
chat / browsing / gaming / terminal / idle / other——云端失败时退化为
`classify_window()` 纯关键词分类（标题+进程名），状态联动永不空转。

**信号反应**：屏幕出现报错 → 她「沮丧/心疼」开口安慰（`signal=error`）；
任务完成 → 庆祝。开口权由模型 `speak` 字段决定——
"日常重复的操作一律 false，沉默也是一种陪伴"。

**隐私**：截图只活在内存（算完 dhash 与 JPEG 即弃，不落盘）；
只有画面变化时才发给你配置的视觉端点；`isWorking` 判定新鲜度 180s。

**回顾**：`/api/context/today` 返回今日活动聚合（category 占比 + 时长），
`/settings` 设置页可视化；事件流单段封顶 600s、保留 7 天自动裁剪。

### 语音控制桌宠动作

不用手点托盘——直接说话就能换装、跳舞、换声音。实现在 `voice_command_router.py`（纯函数规则层）。

**指令优先级**：`stop > motion > voice > model`（一句话含多意图时，取说得靠前的半句）。

| 说什么 | 触发 | 例子 |
|---|---|---|
| 停动作 | `停下/别跳了/休息一下/待机/站好` | "停下来休息吧" → 回待机 |
| 换舞 | 动词 + 动作槽位 | "跳个小蛋糕" → 播 xiaodangao |
| 换音色 | 动词 + 音色槽位 | "换成御姐的声音" → 高冷御姐 |
| 换形象 | 动词 + 模型槽位 | "换成甘雨"/"想看佩儿"/"变成5号" → 切模型 |
| 反问 | 槽位歧义/缺失 | "卡芙卡"有两个形态 → 她"你要哪一个呀？" |

**ASR 友好化设计**（踩坑换来的原则：指令词按"ASR 会转出什么"设计，不按本名设计）：
- 音色别名：`御姐/高冷→高冷御姐`、`甜妹/萝莉/少女→活泼少女`、`东北→东北口音`…
- 模型别名：`雷神/将军→raiden`、`爱丽丝→elysia`、`瑜伽裤→kafka_yoga`；
- **编号方案**：生僻名（庚辰/武罗/千咲）ASR 转不准 → 说"变成5号"、"十二号"按菜单序取，
  中文数字一~十九都支持。数字是 ASR 最稳的词。
- 槽位是闸门：光说"换成现金"不会误伤——动词命中但槽位点不中就不算指令，照常走对话大脑。
- 40 字以内才算指令，长句一律当对话。

### 连续对话（节奏与间隔）

**对话模式**：默认 VAD 连续对话——点一次麦克风，说完停顿自动识别、她自动回答、答完自动继续听，全程免按键。
PTT（按键说话）保留可选。

**间隔参数**（`XIAOYUN_*` 环境变量）：

| 参数 | 默认 | 说明 |
|---|---|---|
| 开麦等待 | `start+dur+0.8s` | 她说完再等 0.8s 收尾缓冲才恢复收音（防录到尾音） |
| 上报静默判据 | 2s | 页面 dur 上报停止 2s 才认播完（消除"最后一帧偏短"竞态） |
| 自听守卫 | 字数/4.5+3.5s | 兜底硬闸 + 页面 resume 精确解除双层 |
| `XIAOYUN_IDLE_SECS` | 900s | 空闲多久后情境引擎可主动搭话 |
| `XIAOYUN_MIN_GAP` | 600s | 两次主动搭话最小间隔（不刷屏） |
| `XIAOYUN_GREET_AFTER` | 180s | 你回来了，多久内算"久别重逢"问候 |

**深夜关怀**：凌晨档主动搭话只有一个意图池——催睡（"我陪着你但你得睡了"）。
这是"关怀"不是闲聊，深夜反而比白天更容易开口，其余闲聊全部静默。

**轮次管理**：对话历史保留最近 24 条（12 轮）；`/api/chat` 全链
（人设注入→大脑→舞台指令清洗→入库→seq 广播→TTS）串行，busy 时新语音输入直接拒绝（425），绝不插话。

### 收音与播放细节优化

**收音侧（她听你）**：
- **VAD 双向自适应阈值**：`max(下限, 噪声底×K)`，环境安静时更灵敏、吵闹时自动收紧；
  实测本机静音 RMS≈0.00015 → 阈值收敛 0.0010~0.0015；
- **换设备**：`XIAOYUN_ASR_INPUT="USB"` 按名字子串选麦（带 15 设备枚举工具 `mic_probe.py`）；
- **数字静默告警**：心跳里 peak<1e-5 标注——区分"环境极安静"与"采集端点根本没出数据"；
- **会话代际机制**：每次 start 都 `gen+=1` 开新 worker，旧 worker 发现代际不符自动退出（防双实例抢队列）；
- **单实例端口独占**：Windows 允许多进程绑同一端口被轮流应答（幽灵行为），ASR 强制 `allow_reuse_address=False`。

**播放侧（她说话）**：
- **流式首响 0.38~0.45s**（暖连接）：整段播放要等 2s，流式边收边播；
  连接复用是关键（共享 Session 连接池），首包与文本长度无关——所以不按逗号切句，只按句号；
- **播放权认领**（`LAST_CLAIM`）：同一 seq 只给一个页面播，双开窗口不会两个声音重叠；
- **WebAudio 自动播放策略**：壳启动参数带 `--autoplay-policy=no-user-gesture-required`，
  且 AudioContext 必须在用户点击的同步栈内 resume，否则静音无报错；
- **口型双通道**：非流式用 `audio.currentTime` + 拼音字典逐字对齐；流式用**真实 PCM 包络**
  （每块音频到达按 20ms 帧算 RMS，播放头落在第几帧取第几帧，与实际发声逐帧对齐不靠预测），
  元音按 0.22s/字节拍推进（等总时长出来再等比映射会口型回跳，更假）；
- **语音报时长**：页面 `audio.duration` 上报真实时长，MP3 字节数÷常数估算被证实偏短 40%+
  （"别拿字节数拍脑袋"教训的直接产物）。

## 目录结构

```
project-nn/
├─ runtime/
│  ├─ Miniconda3/          后端运行时 Python 3.10.11（flask/flask_cors/requests 已装）
│  └─ app/                 上游 Luna 最小依赖（config.json 人设/音色/模型 + Live2D 静态资源 175M）
├─ xiaoyun/                桌宠主目录
│  ├─ xiaoyun_server.py    后端（4174 行：66 端点 + 语音时序 + 记忆 + 感知 + 托盘数据源）
│  ├─ desktop_app.py       桌面壳（2488 行：pywebview 透明窗 + Catcher 隐形点击层 + 托盘）
│  ├─ asr_service.py       语音识别服务（8091，三通道路由）
│  ├─ tts_stream.py        流式 TTS（句级切段 + 豆包裸 PCM）
│  ├─ perception.py        屏幕感知（mss + dhash + 前景窗分类 + 云端视觉）
│  ├─ memory_store.py      长期记忆（SQLite FTS5 bigram + 向量 RRF 融合）
│  ├─ memory_brain.py      记忆四操作消解（mem0 式 ADD/UPDATE/DELETE/NOOP）
│  ├─ context_engine.py    情境引擎（空闲 900s 主动搭话）
│  ├─ voice_command_router.py  语音指令（stop>motion>voice>model 四分支 + ASR 友好别名/编号）
│  ├─ events_store.py      事件流（单段封顶 600s、保留 7 天）
│  ├─ embed_service.py     本地 embedding 兜底（bge-small-zh-v1.5）
│  ├─ desktoppmx.html      PMX 桌宠页（当前默认，XIAOYUN_PET_PAGE 切换）
│  ├─ desktopvrm.html      VRM 桌宠页（three-vrm + 完整交互链）
│  ├─ desktop.html/2d/3d/fbx、chat.html、settings.html、streamdemo.html、pmxtune.html …共 17 页
│  ├─ assets/              模型（15 PMX + 13 VRM/GLB）、15 VMD 动作、three.js 本地库、lipsync
│  ├─ models/              本地权重（faster-whisper-small/large-v3、bge-small-zh-v1.5，3.6G）
│  ├─ 人设/                19 套人格 JSON + current.txt + voice_override.json
│  ├─ data/                memory.db（54 条事实）、events.jsonl、gaze_mode.txt、poke_style.txt
│  ├─ agentplan/           方舟 Plan Key 链（零依赖）
│  ├─ tools/               65+ 诊断/测试/抓屏/无头验证脚本
│  ├─ docs/                实现说明（口型同步 / 记忆语义检索 / whalepal 对比优化清单）
│  ├─ pages/               11 个有路由的宠物页面（desktoppmx/desktopvrm/chat/settings/…）
│  ├─ pages_archive/       无路由引用的实验页面（glbview/avaturn_pet/bjdvr/legacy 等留档）
│  ├─ scripts/             全部启停/运维脚本（启动/停止/重启/诊断 bat + Start/Stop/Restart/Swap/Diagnose ps1）
│  │  └─ adhoc/            非主链辅助脚本（Start-NiannianVRM / Preview-Pet）
│  ├─ logs/                运行日志（srv/desktop/asr/embed/gsv/ollama 各组件 out/err 分文件）
│  ├─ _archive/            历史归档（截图存档 1687 张 / 历史形象图 / 解包原件 22 套 /
│  │                        3d模型原始包 / 素材 FBX / 3d实验 / 测试脚本 / 备份文件 / 预览服务）
│  ├─ xiaoyun_server.py    后端（核心 py 留根目录：XIAOYUN_DIR 是全部相对路径的锚点）
│  ├─ desktop_app.py / asr_service.py / tts_stream.py / perception.py / …（12 个核心模块同上）
│  ├─ xiaoyun.ico          图标（desktop_app 按 ROOT 引用，留根）
│  ├─ 前端事件日志.txt / 桌面状态.txt / 小芸桌面-使用说明.txt   排查命脉（保留根目录）
│  ├─ .venv/               桌面壳 venv（py3.13：pywebview/pystray/PIL）
│  └─ .venv-asr/           ASR venv（py3.13：faster-whisper/sounddevice/websocket-client/numpy）
├─ or_proxy/               对话大脑代理（单文件零依赖）+ README_OR_PROXY.md
├─ nn_pet/                 class 封装重构版（见下）
├─ logs/                   or_proxy 日志输出
├─ README.md               本文件
└─ 迁移历程总结_2026-10-16至10-24.md   九天改造全记录（六阶段）
```

## nn_pet：class 封装重构版

`nn_pet/` 是把过程式 `xiaoyun_server.py`（171 个函数）按职责重组的 class 等价实现：

| 类 | 职责 | 对应原版 |
|---|---|---|
| `AppConfig` | 配置三层合并（env > 人设 JSON > Luna config.json） | `load_cfg()` + 全局常量 |
| `BrainClient` | 大脑客户端（流式/非流式/探活/防静默回退/连接池） | `brain_chat()` |
| `TTSEngine` | 豆包 volc + edge 保底 + 情绪调度 | `tts_volc()/tts_edge()/_detect_emotion()` |
| `HistoryStore` | seq 广播历史 + 播放权认领（防双页同播） | `LAST/LAST_CLAIM/HISTORY` |
| `VoiceSession` | 语音时序状态机（playing/busy 闸/resume 委派） | `VOICE/PLAYBACK` |
| `ReplyPipeline` | 发布管线（人设注入→大脑→舞台指令清洗→入库广播） | `_brain_turn()/_publish_reply()` |
| `PetServer` | Flask 总装（路由薄层，业务在类方法） | 全部 `@app.*` |

**设计约定**：类只依赖显式传入的配置不读全局；跨线程状态集中带锁容器；
路由层薄；舞台清洗唯一出口；TTS 失败自动回退 edge。

```powershell
# 运行 32 项单元测试（无头，test_client 驱动）
runtime\Miniconda3\python.exe nn_pet\tools\test_nn_pet.py
# 启动 class 版后端（默认 8090，XIAOYUN_PORT 可覆盖）
runtime\Miniconda3\python.exe nn_pet\nn_pet\server.py
```

桌面壳 pywebview 部分未重写（原 Catcher/MicButton/HotkeyThread 本就是 class，可整类搬入）；
`nn_pet\shell_main.py` 提供 `NN_SHELL_MODE=legacy` 直跑原壳过渡。

## 技术栈与结构

- **后端** Python 3.10（Miniconda 内嵌）：Flask 3 + flask-cors + requests，全部业务零重依赖；
- **桌面壳** Python 3.13 venv：pywebview 6.2（WebView2/WinForms）+ pystray + Pillow + ctypes Win32；
- **ASR** Python 3.13 venv：faster-whisper（CTranslate2）+ sounddevice + websocket-client；
- **前端**：原生 JS（无构建链），three.js 0.160 + @pixiv/three-vrm 3.5.5 + MMDLoader（全部本地化，不依赖 CDN）；
- **大脑代理**：纯标准库（http.server + urllib）；
- **存储**：SQLite FTS5 + JSONL；**口型字典**：20924 汉字 → 双口型码（pypinyin 生成）。

## 开发与测试

```powershell
# 单元测试（重构包，32 项）
runtime\Miniconda3\python.exe nn_pet\tools\test_nn_pet.py

# 诊断（7 项全 OK 为健康：模型/后端/页面/venv/进程/窗口/日志尾部）
xiaoyun\scripts\诊断小芸桌面.bat

# 语音链路独立验证
xiaoyun\tools\mic_probe.py            # 找麦 + 量电平（RMS/峰值）
xiaoyun\tools\asr_ab_test.py          # 云端 vs 本地转写对比
xiaoyun\tools\test_lipsync.js         # 口型纯逻辑测试

# 无头页面验证（CDP）
node xiaoyun\tools\check_pet_page.mjs   # morph 识别/JS 异常
node xiaoyun\tools\vrm2_interact.mjs    # gaze/poke 断言 + 截图
```

**无头测试铁律**：Chrome `--headless` 必须带 `--disable-gpu`；截图与断言分离
（captureScreenshot 崩 SwiftShader → 页面重载丢 fetch 垫片）；CDP 采样抖动 200ms，禁精确 dt 断言；
有 VMD 在播别断言眼骨绝对值。

### 环境变量（49 个 `XIAOYUN_*`，高频项）

| 变量 | 默认 | 说明 |
|---|---|---|
| `XIAOYUN_PORT` | 8090 | 后端端口（壳 BASE 跟随此值） |
| `XIAOYUN_PET_PAGE` | /desktoppmx | 宠物页（/desktopvrm 切 VRM） |
| `XIAOYUN_TTS_ENGINE` | volc | volc/edge/gptsovits/minimax |
| `XIAOYUN_ASR_PROVIDER` | auto | auto/volc/baidu/whisper |
| `XIAOYUN_ASR_THR` / `_NOISE_K` | 0.0010 / 1.3 | VAD 阈值下限 / 噪声倍率 |
| `XIAOYUN_ASR_INPUT` | （系统默认） | 输入设备名子串 |
| `XIAOYUN_MEMORY` / `XIAOYUN_CONTEXT` / `XIAOYUN_PERCEPTION` | 开 | 记忆/情境/感知开关 |
| `XIAOYUN_PERC_INTERVAL` | 60s | 感知周期 |
| `XIAOYUN_GSV_IDLE_TTL` | 900s | GSV 空闲自动释放 |
| `XIAOYUN_EMBED_*` | bge-m3@ollama | 记忆向量（换模型必须 purge 不匹配维度） |

## 常见问题（都是实际踩过的坑）

<details>
<summary><b>双击 bat 没反应 / 中文乱码</b></summary>

bat 必须纯 ASCII（0 非 ASCII 字节），中文提示放带 BOM 的 ps1；
UTF-8 中文被 cmd 按 GBK 解码后字节会切断命令行。
LF-only 行尾会让 cmd 静默跳行——必须 CRLF。
</details>

<details>
<summary><b>桌宠起不来 / 点击没反应</b></summary>

先跑 `诊断小芸桌面.bat`。高频原因：
① 僵尸实例的 DWM Ghost 替身窗口（"未响应"）压住点击层——Stop 脚本按命令行清场；
② 有进程没窗口（判 GUI 死活看窗口不看 pid 文件）；
③ 125% DPI 下坐标偏 1.25 倍（抓屏/测点击必须 `SetProcessDpiAwareness(2)`）。
</details>

<details>
<summary><b>她自言自语 / 自听死循环</b></summary>

五层防护已内置（playing 上报 / resume 委派 / 播放期关麦 / PLAYBACK 清零 / 回声过滤）。
若换设备后复发，按顺序查：USB 音响采集端点是否被选为输入 → VAD 阈值是否低于她的回授声
（实测她的回授 0.0023~0.0112，用户说话起点约 0.0023，**抬阈值救不了，只能靠时序**）。
</details>

<details>
<summary><b>对话 502 / 模型不回话</b></summary>

看 `or_proxy` health 的 `quota_cooldown` 与 `token_usage`：额度烧光会熔断跳档，全链烧光才 502。
`ARK_API_KEY` 与 Plan Key 同值时启动日志有 WARN（自动改用内置 Key）。
模型选择不在 `/v1/registry` 实况清单里时照样返回并插在 auto 后（防界面显示错位）。
</details>

<details>
<summary><b>VRM 模型刘海翘起 / 头发乱飞</b></summary>

两条铁律：`vrm.update` 传 deltaTime（不是绝对秒数）；不缩放模型本体（弹簧骨世界单位陷阱）。
复现时先 `__xyDbg.spring(0)` 冻结物理对比。
</details>

<details>
<summary><b>改了代码不生效</b></summary>

后端/壳在运行时：改形象/动作必须走 `POST /api/model|/api/motion`（直接改 current.txt 会被覆盖）；
改 py 源码必须走 Stop → Start（进程启动时读一次代码）；
改 or_proxy 用 `Swap-Brain.ps1` 热换血。
</details>

## 隐私与数据流

- 所有数据只落在 `project-nn/` 内：`xiaoyun/data/`（记忆/事件）、各日志文件；
- 截屏数据只在「周期感知」时刻发给**你配置的**豆包视觉端点，算完即弃不落盘；
- 语音：本地 whisper 不出网；云端通道（豆包/百度）按 provider 配置启用；
- or_proxy 只监听 127.0.0.1，上游仅限你配置 Key 的三家；
- 无遥测、无自动更新。

## 与 WhalePal 的对比

对比对象 [Jessie-1939/whalepal](https://github.com/Jessie-1939/whalepal)（Electron 鲸鱼娘桌宠），
逐项核对记录见 `xiaoyun/docs/whalepal对比与优化清单.md`。

| 维度 | WhalePal | 念念（本包） | 结论 |
|---|---|---|---|
| 屏幕感知 | ✅ dhash 去重 + 云端 VLM + 13 类分类 | ✅ 已对齐（dhash 阈值 4 同值、同 JSON 契约、同降级策略） | 持平 |
| 活动事件流/今日回顾 | ✅ 日历热力图 + 时间线 | ✅ events_store 聚合 + `/api/context/today` + 设置页 | 持平（无热力图，路线图） |
| 记忆架构 | 文本分层 JSONL（L0-L2） | SQLite FTS5 bigram + bge-m3 向量 RRF + mem0 式四操作消解 | **念念更强**（语义召回） |
| 主动搭话门控 | 9 层（安静时段/未回应停止/值不值得说…） | 4 层 + 深夜催睡关怀意图池 | whalepal 更细，逐步补 |
| 语音 | 路线图（未实现） | **已完整**：流式 TTS 0.45s + ASR 三通道 + 口型双通道 + 五层自听防护 | **念念领先** |
| 角色表现 | 2D 立绘 12 姿势组 | 3D VRM/PMX：眼神追随 + 戳一下挤压物理 + 15 套舞蹈 | 形态不同，3D 更沉浸 |
| 资源占用 | 空转 CPU≈1%（PNG 序列帧） | 3D 常驻 GPU（架构选择代价） | 各有取舍 |
| 出网白名单 | ✅ 代码层硬白名单 | or_proxy 多上游、无硬白名单 | whalepal 更制度化（P2 待补） |

**互补关系**：whalepal 的壁垒是「屏幕理解 + 分层事件记忆 + 克制门控 + 回顾日历」；
念念的壁垒是「3D 沉浸 + 全链路语音 + 语义长期记忆」。两者的屏幕感知管线已融合
（dhash/引擎骨架/13 类分类契约直接借鉴并署名），记忆与语音保持念念自研优势。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/01-项目大纲.md](docs/01-项目大纲.md) | 定位、愿景、与上游项目的关系、范围、成功标准 |
| [docs/02-需求规格.md](docs/02-需求规格.md) | 功能需求 FR-1~32 与非功能需求 NFR-1~10 |
| [docs/03-系统架构.md](docs/03-系统架构.md) | 四组进程拓扑、端口契约、关键数据流、线程模型、存储布局 |
| [docs/04-技术选型与集成方案.md](docs/04-技术选型与集成方案.md) | 每个关键选型的候选对比与实测数据（壳/透明/大脑/TTS/ASR/渲染/记忆） |
| [docs/05-模型变更历程.md](docs/05-模型变更历程.md) | 对话大脑 qwen2.5→qwen3.5→魔搭→or_proxy 三上游的完整演进 |
| [docs/06-迁移改造历程.md](docs/06-迁移改造历程.md) | 十天改造时间线 + 事故登记簿 Top5 |
| [docs/07-上游项目对比与借鉴.md](docs/07-上游项目对比与借鉴.md) | AI-Vtuber / WhalePal / mem0 等的经历、取舍与优劣势 |
| [docs/08-桌宠交互与陪伴设计.md](docs/08-桌宠交互与陪伴设计.md) | 眼神/戳一下/待机微动/眨眼/舞蹈的全部参数与设计原则 |
| [docs/09-风险与架构决策.md](docs/09-风险与架构决策.md) | ADR-001~007（含撤销条件）+ 风险登记 R1~R10 |
| [docs/10-隐私与数据流.md](docs/10-隐私与数据流.md) | 出网清单完整枚举、敏感数据防护、删除方法 |
| [docs/11-语音链路与时序设计.md](docs/11-语音链路与时序设计.md) | 语音状态机、五层自听防护、VAD/ASR/TTS 细节、口型双通道 |
| [docs/12-记忆介质决策.md](docs/12-记忆介质决策.md) | 为什么是 FTS5+向量 RRF；事实层与事件层并存 |
| [docs/13-验收标准与测试.md](docs/13-验收标准与测试.md) | 12 场景验收清单、测试工具箱、性能基准 |
| [docs/14-主动搭话与门控说明.md](docs/14-主动搭话与门控说明.md) | 门控判定链、时段意图池、"为什么不说话" |
| [docs/15-开发与运维手册.md](docs/15-开发与运维手册.md) | 改码生效方式、诊断工具箱、故障速查、编码铁律、坑清单 |
| [docs/whalepal对比与优化清单.md](docs/whalepal对比与优化清单.md) | 与 whalepal 的逐项核对与 P0-P3 优化清单 |
| [docs/口型同步-实现说明.md](docs/口型同步-实现说明.md) | 口型引擎实现细节 |
| [docs/记忆语义检索-实现说明.md](docs/记忆语义检索-实现说明.md) | RRF 融合检索实现细节 |
| [docs/语音指令路由调研报告.md](docs/语音指令路由调研报告.md) | 指令路由设计调研 |
| [docs/语音指令速查表.md](docs/语音指令速查表.md) | 可说的指令一览（用户手册） |
| [docs/16-人物调色盘设置.md](docs/16-人物调色盘设置.md) | 四滑块调色管线、16 只模型实测定档值、VRM 肤色 tint 体系 |

## 路线图

- 🔁 桌面壳 pywebview 部分迁入 nn_pet class 体系（Catcher/MicButton 整类搬运）
- 🖥️ PMX↔VRM 跨页切换由壳层按 kind 自动选页（当前需重启壳换页）
- 🎤 语音指令扩展至全功能口令化
- 🧠 记忆跨项目共享（工作态门控细化）

## 致谢

- [Ikaros-521/AI-Vtuber](https://github.com/Ikaros-521/AI-Vtuber)（Luna 整合包，本项目起点）
- [Jessie-1939/whalepal](https://github.com/Jessie-1939/whalepal)（MIT；屏幕感知管线的 dhash/去重窗口/引擎骨架设计借鉴，见 `perception.py` 头注）
- 模之屋（PMX 模型素材，按各模型授权条款使用）、腾讯混元 3D（Hunyuan3D-2mini/2mv）、Avaturn（照片重建）
- 字节跳动火山方舟（豆包 TTS/ASR/Vision/AgentPlan）、ModelScope、OpenRouter
