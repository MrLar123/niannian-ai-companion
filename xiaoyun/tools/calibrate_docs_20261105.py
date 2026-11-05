# -*- coding: utf-8 -*-
"""2026-11-05 文档实况校准：把 10-24 快照修正到 11-05 代码实况。

    python tools/calibrate_docs_20261105.py            # 应用修订（幂等）
    python tools/calibrate_docs_20261105.py --check   # 只报告将改哪些文件

设计：
  - REPLACES 是「硬错误」定点替换（数字/路径/结论被推翻），每处必须命中或已应用。
  - APPENDS 是「新增进展」章节，用 <!-- calib-20261105 --> 标记防重复追加。
  - History 类文档（05/06/16/17）保留历史叙述原文，只加时点说明与现状指针，不篡改史实。
  - 所有实况数字取自同日真导入 xiaoyun_server.py 的运行时值，不靠记忆抄。
"""
import io
import os
import sys

DOCS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))
MARK = "<!-- calib-20261105 -->"

REPLACES = [
    # ---------- 01 ----------
    ("01-项目大纲.md",
     "| # | 标准 | 现状（2026-10-24） |",
     "| # | 标准 | 现状（2026-11-05 复核） |"),
    ("01-项目大纲.md",
     "| 6 | 长期记忆可语义召回 | ✅ 54 条事实，RRF 融合 |",
     "| 6 | 长期记忆可语义召回 | ✅ FTS5 + bge-m3 向量 RRF 融合，事实条数随使用增长 |"),
    # ---------- 03 ----------
    ("03-系统架构.md",
     "│  66 个端点：对话/语音时序/模型/人设/调色/感知/记忆/设置          │",
     "│  69 个路由：对话/语音时序/模型/人设/调色/感知/记忆/设置          │"),
    ("03-系统架构.md",
     "| 11434 | Ollama | HTTP | 仅 bge-m3 embed（不可用自动降级关键词，不影响对话） |",
     "| 11434 | Ollama | HTTP | bge-m3 embed 1024 维（默认后端；不可用自动降级关键词，不影响对话） |\n"
     "| 8092 | 自建 embed_service（可选） | HTTP | 仅当 `XIAOYUN_EMBED_BACKEND=http` 时启用，默认不走 |"),
    ("03-系统架构.md",
     "| `xiaoyun/data/gaze_mode.txt` / `poke_style.txt` | 用户偏好 |",
     "| `xiaoyun/data/gaze_mode.txt` / `poke_style.txt` | 用户偏好 |\n"
     "| `xiaoyun/data/cmd_prefs.json` | 语音指令偏好（四类 key→次数/最近时间），**只排反问清单，不参与执行判断** |\n"
     "| `xiaoyun/assets/emo_slots.json` | 31 套形象 × 7 表情槽位的 morph 权重标定 |"),
    # ---------- 05 ----------
    ("05-模型变更历程.md",
     "## 现行模型清单（2026-10 实测）",
     "## 对话模型清单（2026-11-05：`CHAT_MODEL_PREFERRED` 11 项）"),
    ("05-模型变更历程.md",
     "| TTS | edge 晓伊 → 晓晓 → MiniMax 探活（余额不足未启用）→ **豆包 2.0**（现行，speaker 按 20 套人设映射）+ GPT-SoVITS 本地声线（按需拉起） |",
     "| TTS | edge 晓伊 → 晓晓 → MiniMax 探活（余额不足未启用）→ **豆包 2.0 / Plan 网关**（现行，21 项音色菜单：edge 6 + GSV 4 + 豆包 11）+ GPT-SoVITS 本地声线（按需拉起，9880） |"),
    ("05-模型变更历程.md",
     "| ASR | faster-whisper small → +百度 REST 兜底 → **豆包流式 sauc**（现行三通道） |",
     "| ASR | faster-whisper small → +百度 REST 兜底 → **豆包流式 sauc**（现行三通道）；另接入独立 `doubao-seed-asr-2.0` WebSocket（Plan Key，快 28~31%） |"),
    ("05-模型变更历程.md",
     "| 视觉 | doubao-seed-2.1-lite（AgentPlan /api/plan/v3，原生多模态）——1.6/1.5-vision 系全部 404 后确定的唯一通路 |",
     "| 视觉 | chat 端点 `doubao-seed-2.1-pro` / `lite` / `2.0-mini`——1.6/1.5-vision 系全部 404 后确定的通路 |"),
    # ---------- 11 ----------
    ("11-语音链路与时序设计.md",
     "  下限 XIAOYUN_ASR_THR（默认 0.0010）\n  K    XIAOYUN_ASR_NOISE_K（默认 1.3，噪声底自适应：安静时更灵敏、吵时收紧）",
     "  下限 XIAOYUN_ASR_THR（默认 0.0015，见 asr_service.py THR_FLOOR）\n"
     "  K    XIAOYUN_ASR_NOISE_K（默认 3.2，见 NOISE_K；噪声底自适应：安静时更灵敏、吵时收紧）\n"
     "  注：10-24 文档原记 0.0010 / 1.3 已被后续实测推翻，勿再按旧值调参。"),
    # ---------- 13 ----------
    ("13-验收标准与测试.md",
     "| `nn_pet/tools/test_nn_pet.py` | class 重构包 32 项：舞台清洗 5 / HistoryStore 7 / VoiceSession 7 / Flask 端点 13 | `runtime\\Miniconda3\\python.exe nn_pet\\tools\\test_nn_pet.py` |",
     "| `tools/test_voice_command_router.py` | 语音指令纯函数 **84 项** | `runtime\\Miniconda3\\python.exe tools/test_voice_command_router.py` |\n"
     "| `tools/test_voice_command_wiring.py` | 真导入服务端 + Flask `test_client` 真 HTTP 接线全套 | 同上（wiring） |\n"
     "| `tools/test_menu_labels.py` / `test_voice_menu.py` | 菜单标签归一化 / 21 项音色菜单 | 同上 |\n"
     "| `tools/test_emotion_rules.py` / `test_emo_backend.py` | 情绪规则层 50 条 + 后端 58 项 | 同上 |"),
    ("13-验收标准与测试.md",
     "| 语音指令 | parse_command 单测 58 项 + 接线 25 项 | `xiaoyun/tools/` |",
     "| 语音指令 | 84 项纯函数 + 接线全套（含确认闭环 / 偏好落盘 / 真 HTTP） | `xiaoyun/tools/` |"),
    ("13-验收标准与测试.md",
     "1. Chrome `--headless` 必须带 `--disable-gpu`（SwiftShader 崩溃 → 页面重载丢 fetch 垫片）；",
     "1. Chrome `--headless` 带 `--disable-gpu`（SwiftShader 崩溃 → 页面重载丢 fetch 垫片）；"
     "**但 WebView2 桌宠真机/远程调试验证不要带**（会拿不到真实渲染结果，见 `PMX表情体检报告.md`）——两个场景别混用；"),
    ("13-验收标准与测试.md",
     "## 性能基准（本机 RTX 5050 / 32G）",
     "## 性能基准（本机 RTX 5050 8GB / **物理内存 16GB**，页面文件 `D:\\pagefile.sys 32768 65536`）"),
    # ---------- 15 ----------
    ("15-开发与运维手册.md",
     "- `D:\\LunaAI\\xiaoyun`（开发树）与 `D:\\LunaAI\\project-nn\\xiaoyun`（发布树）**双树同步**：改完一侧必须 sync（脚本/日志路径已双树一致）；",
     "- **单树 + git**：`D:\\LunaAI\\project-nn\\xiaoyun` 是唯一工作树（旧开发树 `D:\\LunaAI\\xiaoyun` 已不存在），"
     "仓库根 `D:\\LunaAI\\project-nn`，远端 `github.com/MrLar123/niannian-ai-companion`；改完走 commit + push；"),
    ("15-开发与运维手册.md",
     "- 沙箱内 agent 拉起的常驻进程活不过回合——**启动验证用临时端口前台跑完即杀**，正式启动由用户双击 bat。",
     "- agent 会话里拉起的常驻进程活不过会话结束（WMI 实测，约 2 分钟被回收）；"
     "**可靠启动靠用户双击 bat**，agent 侧验证用 `run_in_background` 直接阻塞式跑服务脚本。\n"
     "- 文档同步：`python tools/sync_docs.py` 把根目录调研报告与 `xiaoyun/docs` 生成物单向汇入仓库 `docs/`；`--check` 只报差异。"),
    # ---------- 16 ----------
    ("16-人物调色盘设置.md",
     "## 4. 当前 16 只模型实测定档值（2026-10-24 读取 model.json）",
     "## 4. 16 只模型实测定档值（2026-10-24 读取 model.json，**历史快照**）\n\n"
     "> 本节是 10-24 的定档记录，保留原值供追溯。当前注册表已扩到 **31 套**形象，"
     "新增形象的调色值以各自 `assets/models/*/model.json` 为准。"),
    # ---------- 记忆检索路径 ----------
    ("记忆语义检索-实现说明.md",
     "# 手动起：D:\\LunaAI\\hunyuan3d\\venv\\Scripts\\python.exe D:\\LunaAI\\xiaoyun\\embed_service.py",
     "# 手动起：D:\\LunaAI\\project-nn\\runtime\\Miniconda3\\python.exe D:\\LunaAI\\project-nn\\xiaoyun\\embed_service.py"),
]

APPENDS = {
"01-项目大纲.md": """
## 6. 2026-11-05 范围复核

- 注册表实况：**31 套**形象（日常 15 / 泳装 5 / 礼服·旗袍·居家 6 / 特殊形态 4）、**19 支**动作（舞蹈 15 / 待机小动作 4）、**21 项**音色（edge 6 + GPT-SoVITS 4 + 豆包 11）、**19 套**人设。
- 新增子系统：情绪旁路（`emotion_rules.py` + LLM judge）、PMX 表情槽位标定（`assets/emo_slots.json`）、语音指令闭环（四类意图 + 反问确认 + 偏好）、两级右键/托盘菜单。
- 后端 `xiaoyun_server.py` 现 5088 行 / 69 个路由（52 个 `/api` + 17 个页面），运行时 `project-nn\\runtime\\Miniconda3\\python.exe`（3.10.11）。
""",

"02-需求规格.md": """
## 2026-11-05 需求落地状态

| 需求 | 状态 | 落点 |
|---|---|---|
| 换性格（第四类语音意图） | ✅ | `switch_preset`，须带「性格/人设/人格/脾气」锚点才与音色别名消歧 |
| 语音播一次动作后回待机 | ✅ | 后端 `ONESHOT{key,seq,at}` + 页面 `LoopOnce` + `POST /api/motion_done` |
| ASR 听错时反问而非静默 | ✅ | `ask_clarify` + `CLARIFY_PENDING`(45s)，「对 / 第一个 / 重报名字」闭环 |
| 指令偏好 | ✅ | `data/cmd_prefs.json`，仅排序反问候选，不参与执行判断 |
| 表情随语气变化 | ✅ | `emotion_rules.py` 唯一真源；`Face` 是唯一表情写者 |
| 菜单说人话 | ✅ | 「角色 · 服装」「动作名 · 时长」+ `/api/models`、`/api/motions` 下发 `groups` |
| 31 套形象表情可用 | ✅ | 31 × 8 情绪 = 248 格全达标，见 `PMX表情体检报告.md` |
""",

"03-系统架构.md": """
## 6. 2026-11-05 架构增补

- **运行时**：后端 `project-nn\\runtime\\Miniconda3\\python.exe`（3.10.11）；桌面壳 `.venv`、ASR `.venv-asr` 为 3.13。`xiaoyun\\.venv` 只含桌面壳依赖，不要拿它跑后端。
- **启动链**：桌面 bat（`DeskBox\\桌面小芸\\重启小芸桌面.bat` → `Restart-Pet.ps1`）→ `Start-Xiaoyun.ps1`（单实例锁）→ or_proxy(8787) → Ollama 探活 → 后端(8090) → ASR(8091) → 桌面窗。
- **新增端点**：`/api/motion_done`（one-shot 动作播完回报）、`/api/voice-options`（21 项音色实时下发）、`/api/models` 与 `/api/motions` 带 `groups`/`oneshot`/`oneshot_seq`、`/api/last` 两分支带 `emo`。
- **主动开口唯一出口 `_publish_reply()`**：SEQ / GUARD / PLAYBACK 三件套都在这里，禁止复制发布逻辑；`STAGE_HINT` 只注入 messages 末尾，`_strip_stage_text()` 在发布前兜底，TTS 不念舞台指令。
- **情绪链路**：`_kick_emotion` 挂 `_brain_turn` / `_cmd_say` / `_speak_proactively` 三处 → 规则层 <1ms 先落地 → daemon 跑 judge → `EMO_STATE` → 前端 `Face` 写表情。
""",

"04-技术选型与集成方案.md": """
## 10. 2026-11-05 补：凭证双 Key 体系（别再试错）

方舟权限**逐模型授予**，两把 Key 不能混用：

| Key | 端点 | 用途 |
|---|---|---|
| 普通 `ARK_API_KEY` | `/api/v3/*` | LLM 对话、视觉理解、向量 |
| Plan Key（`VOLC_SPEECH_PLATFORM_KEY` / Plan 专属 Key） | `/api/v3/plan/*`、`/api/plan/v3/*` | TTS、生图 5.x、ASR WebSocket |

- 方舟 Key **只能**调 `/api/v3/plan/tts/unidirectional`；文档里的 `/api/v3/tts/unidirectional`、`tts/create`(seed-audio-1.0) 只认**豆包语音控制台** Key。方舟 Key 塞 `X-Api-Key` → 401 `45000010`，Bearer → 400 `45000000`。
- 生图/生视频必须 **Plan 专属 Key + `/api/plan/v3/...` URL**；普通 Key 对 `/api/plan` 端点 401。
- Plan 网关只镜像 5 个 TTS/ASR 端点，其余 21 个 404；resource 白名单只有 `seed-tts-2.0` / `volc.seedasr.sauc.duration` / `volc.seedasr.auc`。详见 `调研报告/豆包语音端点全量探测报告.md`。
- **TTS 韵律新结论**：`speech_rate` 1:1（+60 → 1.60x）、`loudness_rate` 生效；`post_process.pitch` 实测无效，`emotion` 字段文档不存在；`audio_params.enable_subtitle=true` 可返回 `sentence.words[]` 逐字时间戳（旧结论「无字级时间戳」已推翻）。
- **对话模型菜单** = `CHAT_MODEL_PREFERRED` ∩ `/v1/registry` 实况；`/v1/models` 会真拉外网，不能当健康探测。
""",

"05-模型变更历程.md": """
## 2026-11-05 增补

对话模型菜单现由 `CHAT_MODEL_PREFERRED` 11 项 ∩ `/v1/registry` 实况决定（表中延迟为 10 月实测，保留供参考）：

| 菜单项 | 对应模型 ID |
|---|---|
| 自动 · 按延迟择优 | `auto` |
| DeepSeek V4.1 经济档 | `deepseek-v4-1-flash-economy-261017` |
| 豆包 Character 2.0 | `doubao-seed-character-260628` |
| 豆包 2.0 mini | `doubao-seed-2-0-mini-260428` |
| 豆包 2.1 Pro | `doubao-seed-2-1-pro-260915` |
| DeepSeek V4.1 Flash | `deepseek-v4-1-flash-261017` |
| DeepSeek V4 Pro | `deepseek-v4-pro-ga-260813` |
| GLM-5.3 Flash | `glm-5-3-flash-260828` |
| 豆包 2.1 lite · 额度告急 | `doubao-seed-2-1-lite-260915` |
| DeepSeek V4.1 Flash · Plan | `deepseek-v4.1-flash` |
| 豆包 2.1 Pro · Plan | `doubao-seed-2.1-pro` |

情绪判定模型写死 `doubao-seed-character-260628`（**不能走 auto**），且必须带 `thinking:{"type":"disabled"}`，否则 `max_tokens=8` 被思维链吃光、`content` 全空却 HTTP 200。模型回 `none` 是有效结论，要覆盖规则层。
""",

"06-迁移改造历程.md": """
### 10-25~11-05：情绪、表情、音色、菜单、语音指令五线收口

- **情绪旁路**：`emotion_rules.py` 独立真源，规则层 + LLM judge 双层；踩坑三连（输入必须是「他说的话」、judge 必带 `thinking:disabled`、模型不能 auto）；`Face` 收为唯一表情写者，poke 降级 overlay(140ms TTL) 治「戳完突然面无表情」。
- **PMX 表情体检**：31 模型 × 8 情绪 = 248 格全达标；「笑い」多为闭眼笑、「にこり」才是睁眼微笑；脸红缺贴图用 `__tint` 脸材质染色兜底。产物 `assets/emo_slots.json`、`docs/PMX表情体检报告.md`。
- **音色扩容**：豆包女声 11/11 实测通过并入菜单，共 21 项；根治「音色清单只取一次」——壳 `preset_watch` 20s 刷新并按变化指纹重建。
- **菜单人话化**：31 形象 / 19 动作改为「角色 · 服装」「动作名 · 时长」+ 两级分组，右键 Win32 与托盘 pystray 同源。
- **语音指令闭环**：动作 one-shot（播一遍回待机，不覆盖菜单持久选择）、新增第四类「换性格」、ASR 近似**只反问不执行**、`CLARIFY_PENDING` 确认闭环、`cmd_prefs.json` 偏好；回归 84 项纯函数 + 接线全套。
""",

"07-上游项目对比与借鉴.md": """
## 2026-11-05 复核说明

本文「待补 / 部分补齐」是按 2026-10-24 代码状态写的**历史评估**，保留原文供追溯。此后的变化：屏幕/活动感知管道（`perception.py`）、事件流（`events_store.py`）、情境引擎（`context_engine.py`）、主动搭话门控的工作态/凌晨分支均已落地；**出站域名硬白名单与日历热力图仍未做**。其余「待补」判定以 `00-文档索引与当前实况.md` 为准。
""",

"08-桌宠交互与陪伴设计.md": """
## 9. 2026-11-05 交互增补

- **眼神**：CursorPoller(20Hz) → `/api/gaze/cursor` → `/api/gaze` → GazeController；**眼骨同号不取负**，YAW 0.26 / PITCH 0.20；头部两组共用 `gazeHeadParts()`，idle 绝对写 / dance `+=`，渲染循环两分支都调 `applyGazeEyes`。
- **戳一下**：单击 `/api/poke`、双击 0.42s `show_chat`；挤压**只用 `rig.scale` 等体积变形，禁碰 position**；三级延迟 0/40/80ms + easeOutBack 过冲，连戳 <800ms 切嫌弃。`Face` 是唯一表情写者，poke 表情降级为 overlay(140ms TTL)。
- **舞蹈双语义**：语音「跳个舞」= one-shot 播一遍回待机（内存状态，不写盘）；菜单点选 = 持久 `current.txt`。判定靠 **seq 递增而非比 key**（同一支舞连说两次 key 不变），结束判定放渲染循环 `tickOneshot()`（放 2s 轮询会滞后 2 秒干站末帧）。
- **表情**：7 槽位按日文 morph 名解析；morph 权重是持久数组，情绪回落必须每帧显式写 0。
""",

"09-风险与架构决策.md": """
## 2026-11-05 新增 ADR

| ADR | 决策 | 原因 |
|---|---|---|
| ADR-008 | ASR 近似**只用来反问报候选，绝不用来执行** | 两字名错一字相似度 0.5，两个毫不相干的两字名共一字也是 0.5（实测「换成甘雨」被比成音色「晓雨」）。执行层两字名阈值 1.01 直接禁用近似；反问走独立 `loose_candidates()` +「换/切动词 + 非问句 + 残留≥2 字」闸门 |
| ADR-009 | 动作 one-shot 与持久选择分离 | 语音播一遍不该覆盖菜单当前选择；`ONESHOT` 只进内存、`current.txt` 只由菜单写；seq 递增而非比 key |
| ADR-010 | 反问清单按偏好排序，但偏好**不参与执行判断** | `cmd_prefs.json` 只在指令成功执行时累加，与 `memory.db` 长期记忆隔离，避免「常点的名字」被当成隐式指令 |
| ADR-011 | 凭证双 Key + 模型权限逐项实测 | 方舟普通 Key 与 Plan/语音控制台 Key 端点互不通用；控制台「已开通」≠ API 可调，`GET /api/v3/models` 可信但 Published ≠ 有权限 |
| ADR-012 | 舞台指令只进 messages、不入 TTS | `STAGE_HINT` 注入末尾 + `_strip_stage_text()` 在 `_publish_reply` 兜底，防止她把「（歪头）」念出来 |
""",

"10-隐私与数据流.md": """
## 2026-11-05 出网清单补充

| 数据 | 去向 | 触发时机 | 可关 |
|---|---|---|---|
| 合成文本 | 豆包 Plan 网关 `/api/v3/plan/tts/unidirectional`（Plan Key） | 她说话时 | 可切 edge / GSV（全本地） |
| 语音音频 | `doubao-seed-asr-2.0` WebSocket（Plan Key） | 说话时，比 sauc 快 28~31% | 可切本地 whisper |
| 生图提示词/参考图 | 火山方舟 `/api/plan/v3/images/generations`（Plan 专属 Key） | 主动生图 | 是（非核心链路） |
| 情绪判定文本 | 豆包 `doubao-seed-character-260628` | 每轮对话（daemon 异步） | 规则层仍在，可降级 |

**本地且不出网的新增数据**：`data/cmd_prefs.json`（指令偏好计数）、`assets/emo_slots.json`（表情权重）、`人设/voice_override.json`（音色覆盖）、`data/events.jsonl`（感知事件）。

**.gitignore 红线**（push 前必查）：`xiaoyun/data/` 下的 `memory.db*`、`events.jsonl`、`*.json` 偏好文件、`人设/voice_override.json`、`*.key` / `*.env` / `secrets*`、`docs/验收截图/`（31 张 PNG 共 29MB）一律不进仓库；API Key 只走用户级环境变量。
""",

"11-语音链路与时序设计.md": """
## 2026-11-05 语音链路增补

- **凭证**：TTS 的 Plan 网关只认方舟 Key（`/api/v3/plan/tts/unidirectional`），文档中的 `/api/v3/tts/unidirectional`、`tts/create` 只认豆包语音控制台 Key；两套 Key 混用会 401/400。详见 `04-技术选型与集成方案.md` 第 10 节。
- **双向流式 TTS 帧**：header(4B) + event(4B) + [sid_len + sid，仅 event≥100] + size(4B) + payload；**发完 TaskRequest 立刻发 FinishSession(102)**，否则永久静默。
- **字级时间戳**：`audio_params.enable_subtitle=true` 返回 `sentence.words[]`（放 `req_params` 静默无效；首条为空需按序累积）。旧结论「豆包无字级时间戳」已推翻。
- **韵律**：`speech_rate` 1:1（+60 → 1.60x）、`loudness_rate` 生效；`post_process.pitch` 无效、`emotion` 字段不存在。
- **ASR 新通道**：独立 `doubao-seed-asr-2.0` WebSocket（Plan Key，wav/16bit/单声道），由 `or_proxy._asr_ws()` 封装；Router 仍是 volc → baidu → whisper。
- **时序铁律不变**：页面周期上报 `/api/voice/playing {seq,dur}`，漏报会退字数估算并卡「准备开口」；`/api/voice/resume` 在 busy 下必须委派；主动开口唯一出口 `_publish_reply()`。
""",

"12-记忆介质决策.md": """
## 2026-11-05 增补：两套「记忆」严格隔离

| 文件 | 是什么 | 谁写 | 参与什么 |
|---|---|---|---|
| `data/memory.db` | 长期记忆（FTS5 + vectors，RRF） | `memory_store` / `memory_brain` | 对话召回、事实层 |
| `data/cmd_prefs.json` | 四类语音指令的 `key→{count,last,label}` | 仅指令**成功执行**时 | **只排反问候选顺序**，不参与任何执行判断 |

理由：偏好是「使用频次」而非「用户意图」，若让它进执行链，常点的名字会被当成隐式指令，重演 ADR-008 的误触。`/api/reset` 不删长期记忆；换 embed 必调 `purge_mismatched_vectors(dim)`。Ollama 不被 `Stop-Xiaoyun` 杀、不自动 pull。
""",

"13-验收标准与测试.md": """
## 2026-11-05 新增验收项

| # | 场景 | 判据 | 状态 |
|---|---|---|---|
| 13 | 31 套形象 × 8 情绪表情 | 248 格全达标，calib 与 audit 双链路一致、噪声 0 | ✅ `docs/PMX表情体检报告.md` |
| 14 | 21 项音色菜单 | 11/11 豆包女声实测有音频字节；加音色后整壳重启能被 `preset_watch` 指纹捕获 | ✅ `tools/test_voice_menu.py` |
| 15 | 语音指令四类意图 | 84 项纯函数 + 接线全套（确认闭环 / 偏好落盘 / 真 HTTP 两入口） | ✅ `tools/test_voice_command_*.py` |
| 16 | ASR 听错不静默不误切 | 「换成甘兩」→ 反问「甘雨」→「对」→ 真换装；「换成现金」/闲聊零打扰 | ✅ |
| 17 | 动作 one-shot | 「跳个舞」播一遍回待机，`current.txt` 不变；连续两次靠 seq 区分 | ✅ `/api/motion_done` |
| 18 | 菜单两级分组 | 31 形象 / 19 动作 group 下发，右键 Win32 与 pystray 同源 | ✅ `tools/dump_menu_tree.py` |

**跑测试用**：`D:\\LunaAI\\project-nn\\runtime\\Miniconda3\\python.exe`（3.10.11），不要用 `xiaoyun\\.venv`。wiring 测试必须隔离 `PRESET_STATE` 与 `CMD_PREFS_PATH`，否则会改掉用户真实人设与推荐顺序。

**真机验证铁律**：桌宠是 `WS_EX_LAYERED` 透明窗，`ImageGrab`/BitBlt 只拍到壁纸，必须 `PrintWindow(..., PW_RENDERFULLCONTENT=2)` + `GetDIBits`（`tools/pet_printwindow.py`）；走 WebView2 远程调试端口 9223 + `tools/pet_emo_live.mjs`；页内画面差用 readPixels；CDP 抖动 200ms 禁精确 dt 断言。
""",

"14-主动搭话与门控说明.md": """
## 2026-11-05 门控补记

- **工作态门控**：`decide(working=)` 会压低 idle 类主动开口，避免打断用户工作；**凌晨催睡放行**是特例，必须放在通用压制分支之后（分支顺序踩过坑，写反了会连催睡一起压掉）。
- 触发源与门控判定链不变，但主动开口**只走 `_publish_reply()`**（SEQ/GUARD/PLAYBACK 三件套），不要另起发布路径；`_kick_emotion` 挂在 `_speak_proactively` 首行，主动开口也会带情绪。
- 「未回应处理」仍是 whalepal P1 待补，未在本期实现，勿按已落地理解。
""",

"15-开发与运维手册.md": """
## 7. 2026-11-05 运维增补

| 事项 | 现行做法 |
|---|---|
| 后端运行时 | `D:\\LunaAI\\project-nn\\runtime\\Miniconda3\\python.exe`（3.10.11）；`xiaoyun\\.venv` 只装桌面壳依赖 |
| 改后端生效 | Stop → Start（进程启动读一次）；改 `or_proxy.py` 走 `Swap-Brain.ps1`；改 ASR 走 `Restart-ASR.bat` |
| 菜单/音色/动作/人设改完 | 必跑 `tools/test_voice_command_router.py`(84) + `tools/test_voice_command_wiring.py`；wiring 隔离 `PRESET_STATE` 与 `CMD_PREFS_PATH` |
| 改文档 | 改真源：`xiaoyun/docs/` 的速查表与体检报告是**生成物，禁手改**，跑 `make_voice_cmd_sheet.py` / `make_emo_report.py`；根目录 `D:\\LunaAI\\docs` 的大报告改完跑 `tools/sync_docs.py` 汇入仓库 `docs/调研报告/` |
| 加音色 | `VOLC_OPTIONS` 是唯一清单，`/api/voice-options` 下发；壳 `preset_watch` 20s 刷新，旧版只取一次需整壳重启 |
| 端口健康探测 | `/v1/registry` 纯本地；`/v1/models` 会真拉外网 30s，**不能**当探活 |
| push 前 | `git status --short` 核对无 `data/*.json` / `验收截图/` / `*.key`；本机测端口加 `--noproxy '*'` |

**git 仓库**：`D:\\LunaAI\\project-nn`，分支 `main`，远端 `https://github.com/MrLar123/niannian-ai-companion.git`。仓库 `.git/config` 曾写死 `127.0.0.1:13128` 代理，该端口不监听会导致 push 报 `Failed to connect ... over proxy`；本机实测 git **直连可用**，清掉代理配置即可。
""",

"16-人物调色盘设置.md": """
## 8. 2026-11-05 复核

本文是 2026-10-24 的 16 只模型定档快照，滑块管线与取值优先级**仍然有效**。当前形象已扩到 31 套并按「日常 / 泳装 / 礼服·旗袍·居家 / 特殊形态」四组归类，菜单显示名统一为「角色 · 服装」；新增形象的调色参数以各自 `model.json` 为准，不在本文补表。
""",

"17-3D建模历程与素材收集.md": """
## 8. 2026-11-05 复核

本文记录的是 10 月四路线实测的方法论与结论，史实保留。此后管线补充两条铁律：① `vrm.update()` 传 **deltaTime** 而非绝对秒数；② **禁止缩放模型本体**（弹簧骨/碰撞半径是世界单位），要调远近就调相机距离。导出后必跑 `fix_vrm_restpose.py`；`HUMAN_BONES` 含 leftEye/rightEye。桌面页已固定为 PMX 页（`PET_PAGE=/desktoppmx`）。
""",

"whalepal对比与优化清单.md": """
## 六、2026-11-05 落地进度复核

| 原优先级 | 项 | 现状 |
|---|---|---|
| P0 | 屏幕/活动感知管道 | ✅ 已落地：`perception.py`（mss/dhash/前景窗）+ 豆包 Vision 结构化理解 |
| P1 | 活动事件流 + 今日回顾 | ✅ 已落地：`events_store.py`（单段 600s / 7 天）、`context_engine.py` |
| P1 | 主动搭话门控补全 | 🟡 部分：工作态压制、凌晨催睡已补；**未回应停止追问仍待补** |
| P2 | 出网白名单 + 隐私红线 | ❌ 未做硬白名单；记忆抽取提示词隐私红线仍待补 |
| P3 | 设置面板 / 日历热力图 | ❌ 待补 |

本期另有 whalepal 未覆盖的能力：情绪旁路、31 形象 × 8 情绪标定、语音指令四类意图与反问闭环。
""",

"口型同步-实现说明.md": """
## 7. 2026-11-05 复核

`lipsync.js` 的现行链路：拼音 → 五元音（a/i/u/e/o）→ 谱通量 **onset** 锚定时间轴；流式型按每块 PCM 20ms 帧 RMS 逐帧对齐，不预测。本文第 3 节的「onset 锚定（2026-10-19 升级）」仍是现行做法，未被替换。PMX 表情另走 `assets/emo_slots.json` 的 7 槽位 morph，与口型互不影响。
""",

"记忆语义检索-实现说明.md": """
## 6. 2026-11-05 复核

- 默认后端是 **Ollama `bge-m3`（1024 维）**，走 `http://127.0.0.1:11434`；`8092` 只在显式设 `XIAOYUN_EMBED_BACKEND=http` 时作为自建 `embed_service.py` 的备用端口，不是常驻服务。
- 换模型/换维度必须 `purge_mismatched_vectors(dim)`，否则向量表残留旧维度会污染 RRF。
- 手动起服务与跑测试都用 `D:\\LunaAI\\project-nn\\runtime\\Miniconda3\\python.exe`；旧文档里的 `D:\\LunaAI\\xiaoyun\\...` 开发树已不存在。
- `data/cmd_prefs.json` 是语音指令偏好，**不是**长期记忆，不进 `memory.db`，详见 `12-记忆介质决策.md`。
""",
}


def load(name):
    return io.open(os.path.join(DOCS, name), encoding="utf-8").read()


def save(name, text):
    with io.open(os.path.join(DOCS, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    check = "--check" in sys.argv
    touched = {}
    errors = []
    for name, old, new in REPLACES:
        # 同一文件多处替换必须基于上一处的结果，不能重新读盘（磁盘此刻还没写回）
        text = touched.get(name) if name in touched else load(name)
        # 先判「已应用」：有些规则的 new 以 old 开头（在表格行后追加新行），
        # 若先判 old 会每次复跑都再插一遍，文件无限膨胀。
        if new in text:
            pass                      # 已应用，幂等
        elif old in text:
            text = text.replace(old, new, 1)
        else:
            errors.append("替换未命中: %s :: %s" % (name, old[:48]))
        touched[name] = text

    for name, extra in APPENDS.items():
        text = touched.get(name) or load(name)
        if MARK not in text:
            text = text.rstrip("\n") + "\n\n" + MARK + "\n" + extra.strip("\n") + "\n"
            touched[name] = text

    if errors:
        for e in errors:
            print("  [错误] " + e)
        return 1

    pending = [n for n in touched if touched[n] != load(n)]
    print("替换规则 %d 处 / 增补章节 %d 篇；本次%s %d 个文件" %
          (len(REPLACES), len(APPENDS), "待改" if check else "实改", len(pending)))
    for name in sorted(pending):
        print("  %s %s" % ("[待改]" if check else "[已改]", name))
    if not check:
        for name in pending:
            save(name, touched[name])
    return 0


if __name__ == "__main__":
    sys.exit(main())
