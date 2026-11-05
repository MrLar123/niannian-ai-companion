# -*- coding: utf-8 -*-
"""把仓库主 README.md 从 2026-10-24 快照校准到 2026-11-05 实况。

    python tools/calibrate_readme_20261105.py --check   # 预演，不写文件
    python tools/calibrate_readme_20261105.py           # 应用
    python tools/calibrate_readme_20261105.py           # 再跑应为 0 变更（幂等）

基准全部取自同日真导入 xiaoyun_server.py 后的运行时值：
  31 形象 / 19 动作 / 19 人设 / 21 音色 / 69 路由装饰器（63 条唯一路径）
  xiaoyun_server.py 5088 行、desktop_app.py 2703 行、VAD 0.0015 / K=3.2
历史实测数据（模型延迟表等）保留原文，只把「当前状态」类表述改准。
"""
import io
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TARGET = os.path.join(REPO, "README.md")
MARK = "<!-- readme-calib-20261105 -->"

# (old, new)。old 必须唯一命中；若 old 不在而 new 已在，视为「已应用」，保证幂等。
R = []


def rep(old, new):
    R.append((old, new))


# ---------- 抬头与徽章 ----------
rep(
    r"""**一只住在你桌面、会说话、会看你、记得住你的 VRM/PMX 桌宠女友。**

*Windows 原生桌宠：pywebview 透明窗口 + 云端大脑（豆包/DeepSeek/GLM 三上游自动路由）
+ 豆包流式语音（首响 ~0.45s）+ 语音识别三通道 + 屏幕感知 + 长期记忆。*

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)](#快速开始)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.13-3776AB.svg)](#技术栈与结构)
[![Models](https://img.shields.io/badge/%E5%BD%A2%E8%B1%A1-15%20PMX%20%2B%2013%20VRM-9B59B6.svg)](#人物模型库)
[![Persona](https://img.shields.io/badge/%E4%BA%BA%E8%AE%BE-19%20%E5%A5%97-E91E63.svg)](#人设系统)""",
    r"""**一只住在你桌面、会说话、会看你、记得住你的 3D 桌宠女友念念。**

*Windows 原生桌宠：pywebview 透明窗口 + or_proxy 多上游大脑 + 豆包流式语音（首响 ~0.45s）。*
*ASR 三通道 + 屏幕感知 + 长期记忆 + 情绪表情；当前默认 PMX 页（31 套形象），VRM 页仅实验保留。*

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)](#快速开始)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.13-3776AB.svg)](#技术栈与结构)
[![Models](https://img.shields.io/badge/models-31%20PMX-9B59B6.svg)](#人物模型库)
[![Voices](https://img.shields.io/badge/voices-21-1F9D55.svg)](#tts她说)
[![Motions](https://img.shields.io/badge/motions-19-FF8C00.svg)](#人物模型库)
[![Persona](https://img.shields.io/badge/persona-19-E91E63.svg)](#人设系统)""")

rep(
    r"""**念念 = VRM/PMX 3D 形象的「陪伴」 × 云端多模态模型的「大脑」 × 本地管线的「隐私兜底」。**

她是一个 Windows 原生桌面应用（不是网页）：一只透明置顶、可拖拽、可点击、可说话的 3D 桌宠待在屏幕角落。
背后由四组进程组成：

```
桌面壳(透明窗口+托盘) ──┐
语音识别 ASR(8091) ────┼──→ 后端 Flask(8090) ──→ or_proxy(8787) ──→ 魔搭/方舟/OpenRouter
屏幕感知(mss+dhash) ───┘         │
                              记忆 SQLite + Ollama embed(11434, 可选)
```

她能陪你聊天（文字+语音全双工）、念台词带情绪（语速/音调随内容变化）、
眼神跟着你的鼠标走、被戳会瘪嘴、跳舞有 15 套动作、还记得你们聊过什么。""",
    r"""**念念 = 31 套 PMX 形象的「陪伴」 × 云端多模态模型的「大脑」 × 本地管线的「隐私兜底」。**

她是一个 Windows 原生桌面应用（不是网页）：一只透明置顶、可拖拽、可点击、可说话的 3D 桌宠待在屏幕角落。
背后由四组进程组成：

```
桌面壳(透明窗口+托盘) ──┐
语音识别 ASR(8091) ────┼──→ 后端 Flask(8090) ──→ or_proxy(8787) ──→ 方舟 / OpenRouter / 魔搭(默认禁用)
屏幕感知(mss+dhash) ───┘         │        └─→ 豆包 TTS(Plan) / ASR(ws) / 视觉 / 生图
                              记忆 SQLite + Ollama bge-m3 embed(11434)
                              （可选 embed_service 8092，需 XIAOYUN_EMBED_BACKEND=http）
```

她能陪你聊天（文字+语音全双工）、念台词带情绪（规则层 + LLM judge + PMX 表情）、
眼神跟着你的鼠标走、被戳会瘪嘴、跳舞有 19 套动作、还记得你们聊过什么。""")

# ---------- 能力清单 ----------
rep(
    r"""- 🎭 **形象**：15 只 PMX 模型（模之屋姵儿、甘雨、雷电将军、卡芙卡、千咲…）+ 13 个 VRM/GLB（小芸、小芸-jk、Avaturn 照片重建版）；托盘/右键菜单一键切换，2 秒热生效。
- 💃 **动作**：15 套 VMD 舞蹈（MMD 原生骨名），VRM 页做了骨骼重定向（MMD 左手系 → VRM humanoid），舞蹈期间眼神自动让位。
- 💬 **对话**：or_proxy 自动路由三大上游（详见[对话大脑](#对话大脑-or_proxy)），按实时延迟择优、额度熔断、401 跳档；设置页 `http://127.0.0.1:8090/settings` 可看清单/延迟/用量条形图。
- 🎙️ **语音全双工**：你说 → 她听（VAD 连续对话模式）→ 云端转写 → 她回答 → 流式 TTS 边收边播 → 口型同步 → 播完自动恢复收音。自听死循环有五层防护（见[语音链路](#语音时序铁律)）。
- 😊 **情绪表达**：回复文本命中情绪词库自动调语速/音调（开心 +8%/+2Hz、傲娇 +5%/+3Hz、吃醋 +6%/+1Hz、心疼 0%/-2Hz）；VRM 脸上还有眨眼（0.18s、2~4.5s 随机、30% 连眨）+ 口型五元音。
- 👀 **眼神追随**：壳层 20Hz 轮询光标 → 眼骨最大 YAW 0.26 / PITCH 0.20，死区 46px、参考距离 260px、`1-exp(-dt/τ)` 指数平滑；头部跟随系数 1.6/2.0。
- 👆 **戳一下**：rig.scale 等体积挤压（禁碰 position，三级延迟 躯干 0 / +40ms / +80ms），easeOutBack 过冲曲线；连戳 <800ms 切嫌弃表情。
- 🧠 **记忆**：SQLite FTS5（中文 bigram 索引）+ bge-m3 向量语义检索，RRF 融合排序；情境引擎空闲 900s 后主动搭话。
- 🖥️ **屏幕感知**：每 60s mss 截屏 → dhash 汉明距离 ≤4 去重 → 豆包视觉模型理解你在干嘛（截图只活在内存，不落盘）。
- 🔧 **运维**：托盘五组子菜单（人设/音色/对话模型/人物模型/动作）、`/settings` 网页面板、诊断脚本逐项体检、Swap-Brain 一键换脑。""",
    r"""- 🎭 **形象**：31 套 PMX 模型（甘雨、雷电将军、卡芙卡泳装/瑜伽裤、绯雪双形态…），按「角色 · 服装」两级分组；托盘/右键菜单一键切换，2 秒热生效。
- 💃 **动作**：19 套 VMD 舞蹈（MMD 原生骨名），按「动作名 · 时长」分组；语音说「跳个舞」播一遍自动回待机（one-shot，不覆盖菜单里的持久选择）。
- 💬 **对话**：or_proxy 按 `/v1/registry` 实况与 `CHAT_MODEL_PREFERRED` 交集生成对话模型菜单，EMA 延迟择优、额度熔断、401 跳档（详见[对话大脑](#对话大脑-or_proxy)）；设置页 `http://127.0.0.1:8090/settings` 看清单/延迟/用量。
- 🎙️ **语音全双工**：你说 → 她听（VAD 连续对话模式）→ 云端转写 → 她回答 → 流式 TTS 边收边播 → 口型同步 → 播完自动恢复收音。自听死循环有五层防护（见[语音链路](#语音时序铁律)）。
- 🎚️ **音色**：21 项（6 edge + 4 GPT-SoVITS + 11 豆包女声，11/11 实测有音频字节），菜单实时刷新；选过一次即写 `voice_override.json` 的 `follow_preset=false`，换人设不再覆盖你挑的声音。
- 😊 **情绪表达**：`emotion_rules.py` 规则层先落地 + LLM judge 复核（模型回 `none` 是有效结论）；PMX 7 表情槽位按日文 morph 名标定，**31 形象 × 8 情绪 = 248 格全达标**；TTS 语速/音量随情绪偏移，眨眼 + 口型五元音。
- 👀 **眼神追随**：壳层 20Hz 轮询光标 → 眼骨最大 YAW 0.26 / PITCH 0.20，死区 46px、参考距离 260px；眼骨**同号不取负**，idle 绝对写 / dance `+=`，舞蹈期间让位。
- 👆 **戳一下**：rig.scale 等体积挤压（禁碰 position，三级延迟 0/+40/+80ms），easeOutBack 过冲；连戳 <800ms 切嫌弃；poke 已降级为 overlay(140ms TTL)，不再抢表情写权。
- 🧠 **记忆**：SQLite FTS5（中文 bigram 索引）+ bge-m3 向量语义检索，RRF 融合排序；情境引擎空闲 900s 后主动搭话。
- 🗣️ **语音指令**：四类意图（换形象 / 换动作 / 换音色 / **换性格**）；听岔了会报候选反问，说「对」即执行，近似**只用来反问、绝不直接改状态**；偏好记在 `data/cmd_prefs.json`。
- 🖥️ **屏幕感知**：每 60s mss 截屏 → dhash 汉明距离 ≤4 去重 → 豆包视觉模型理解你在干嘛（截图只活在内存，不落盘）。
- 🔧 **运维**：托盘五组子菜单（人设/音色/对话模型/角色·服装/动作名·时长）、`/settings` 网页面板、诊断脚本逐项体检、Swap-Brain 一键换脑。

> **2026-11 新增能力**一览与踩坑记录见 [docs/18-2026-11 变更纪要.md](docs/18-2026-11%20变更纪要.md)，
> 全部实况数字以 [docs/00-文档索引与当前实况.md](docs/00-文档索引与当前实况.md) 为准。""")

# ---------- 快速开始 / 启动链 / 凭证 ----------
rep(
    r"""```powershell
# 全部依赖已打进包内（runtime/Miniconda3 + 两个 venv），无需安装任何东西
D:\LunaAI\project-nn\xiaoyun\启动小芸桌面.bat     # 双击即全链拉起
D:\LunaAI\project-nn\xiaoyun\停止小芸桌面.bat     # 全链停止
D:\LunaAI\project-nn\xiaoyun\scripts\诊断小芸桌面.bat     # 逐项体检
```""",
    r"""```powershell
# 全部依赖已打进包内（runtime/Miniconda3 + 两个 venv），无需安装任何东西
D:\LunaAI\project-nn\xiaoyun\scripts\启动小芸桌面.bat     # 双击即全链拉起
D:\LunaAI\project-nn\xiaoyun\scripts\停止小芸桌面.bat     # 全链停止
D:\LunaAI\project-nn\xiaoyun\scripts\诊断小芸桌面.bat     # 逐项体检
```

> 桌面上 `DeskBox\桌面小芸\` 里放的是同一套脚本的快捷副本（`重启小芸桌面.bat` 等）。
> **agent 会话里拉起的常驻进程活不过会话结束**，正式启动请双击 bat。""")

rep(
    r"""1. **or_proxy 8787** 在线检查，不在线自动拉起；
2. **Ollama 11434**（仅记忆语义检索用，不可用自动降级关键词，不影响对话）；
3. **后端 8090**（Miniconda python 跑 xiaoyun_server.py）；
4. **ASR 8091**（.venv-asr 跑 faster-whisper small，CPU int8 不占显存）；
5. **Keep-Ollama** 保活守护（60s 巡检）；
6. **桌面壳**（.venv pythonw 跑 desktop_app.py，透明窗口 + 托盘 + 隐形点击层）。

单实例锁（命名互斥量 `XiaoyunCtrl`）防止重复启动；Stop 脚本按命令行清理全部残留并复核端口释放。""",
    r"""1. **or_proxy 8787** 在线检查，不在线自动拉起（用 WorkBuddy 内置 Python 3.13）；
2. **Ollama 11434** 探活（bge-m3 1024 维，仅记忆语义检索；不会被 Stop 脚本杀掉）；
3. **后端 8090**（`runtime\Miniconda3\python.exe` 跑 `xiaoyun_server.py`，Python 3.10.11）；
4. **ASR 8091**（`.venv-asr\pythonw.exe` 跑 faster-whisper small，CPU int8 不占显存）；
5. **桌面壳**（`.venv\pythonw.exe` 跑 `desktop_app.py`，透明窗 + 托盘 + 隐形点击层）。

可选：`embed_service.py` 8092（bge-small 512 维），仅当 `XIAOYUN_EMBED_BACKEND=http` 时启用，
主线走 Ollama bge-m3；换 embed 模型必须 `purge_mismatched_vectors(dim)` 清掉不同维度的旧向量。

单实例锁（命名互斥量 `XiaoyunCtrl`）防止重复启动；Stop 脚本按命令行清理全部残留并复核端口释放。
**Windows 允许多进程绑同一端口**，所以启动脚本会额外核对 8090/8091 的监听进程数。""")

rep(
    r"""- **API Key**（写在用户级环境变量，脚本自动继承）：

  | 环境变量 | 用途 | 必需 |
  |---|---|---|
  | `ARK_API_KEY` / `ARK_STANDARD_API_KEY` | 方舟标准通道（豆包/DeepSeek/GLM 对话） | 至少一个上游 |
  | `ARK_PLAN_API_KEY`（或复用 `VOLC_SPEECH_API_KEY`） | 方舟 Agent Plan 通道（视觉理解/生图/embedding） | 屏幕感知需要 |
  | `VOLC_SPEECH_API_KEY` | 豆包 TTS + 豆包流式 ASR | 语音需要 |
  | `MODELSCOPE_API_KEY` | 魔搭 Qwen3.5-27B（免费档） | 可选 |
  | `XIAOYUN_BAIDU_*` | 百度短语音识别兜底 | 可选 |

  > ⚠️ `ARK_API_KEY` 若与 Plan Key 同值，or_proxy 会自动告警并改用内置标准 Key
  > （2026-10-23 串道事故的防御，见 `or_proxy.py` `_resolve_ark_key()`）。

- **屏幕分辨率 ≥ 1080p**：窗口 670×1010（等比可调 `PET_W/PET_H`）。""",
    r"""- **API Key**（写在**用户级环境变量**，脚本自动继承；**仓库里零明文 Key**，clone 后必须自己配）：

  | 环境变量 | 用途 | 必需 |
  |---|---|---|
  | `ARK_API_KEY` / `ARK_STANDARD_API_KEY` | 方舟**标准通道** `/api/v3/*`（对话 / 视觉 / 向量） | 至少一个上游 |
  | `OPENROUTER_API_KEY` | OpenRouter 免费模型池（当前 auto 路由常用） | 推荐 |
  | `ARK_PLAN_API_KEY` / `VOLC_SPEECH_API_KEY` | **Plan 通道**：TTS `/api/v3/plan/tts/*`、ASR ws、`/api/plan/v3/*` 生图生视频 | 语音 / 生图需要 |
  | `VOLC_SPEECH_PLATFORM_KEY` | 豆包语音控制台 Key，仅 `/api/v3/tts/unidirectional`、`tts/create` 认它 | 可选 |
  | `MODELSCOPE_API_KEY` | 魔搭（账户欠费，默认禁用） | 可选 |
  | `XIAOYUN_BAIDU_*` | 百度短语音识别兜底 | 可选 |

  > ⚠️ **三套凭证互不通用**：方舟标准 Key 打 `/api/v3/plan/*` 会 401 `45000010`；
  > Plan Key 打生图要用 `/api/plan/v3/*` 且只认 Plan 专属 Key；豆包控制台 Key 又是第三把。
  > 详见 [docs/调研报告/方舟四类模型能力清单与接入.md](docs/调研报告/方舟四类模型能力清单与接入.md) 与
  > `xiaoyun/tools/tts_auth_probe.py`。

- **屏幕分辨率 ≥ 1080p**：窗口 670×1010（等比可调 `PET_W/PET_H`）。
- **物理内存**：本机 16GB + 页面文件 `D:\pagefile.sys`（改完需重启 OS 生效），跑大模型权重前先确认。""")

# ---------- 云端模型 / TTS / ASR / 视觉 ----------
rep(
    r"""`or_proxy/or_proxy.py`（单文件、零第三方依赖、Python 3.8+）暴露 OpenAI 兼容端点：""",
    r"""`or_proxy/or_proxy.py`（单文件、零第三方依赖、Python 3.8+，**已入仓库且不含任何明文凭证**）暴露 OpenAI 兼容端点：""")

rep(
    r"""| `GET /v1/models` / `GET /v1/registry` | 模型清单（后者纯本地快照，不做外网探测） |""",
    r"""| `GET /v1/registry` | 模型清单（**纯本地注册表**，对话模型菜单以它为准；`/v1/models` 会真拉外网，不能当健康探测） |""")

rep(
    r"""**上游优先级**：`ark（主力额度池）→ arkplan（Plan Key 备援）→ openrouter（兜底）→ modelscope`。

**模型池（2026-10 实测）**：""",
    r"""**上游优先级**：`UPSTREAM_PRIORITY = [ark, arkplan, openrouter, modelscope]`，
其中 `modelscope` 因账户欠费由 `OR_DISABLE_UPSTREAMS` 默认禁用、`arkplan` 缺 Plan Key 时自动摘除。
对话模型菜单 = `CHAT_MODEL_PREFERRED`（11 项）∩ `/v1/registry` 实况，避免选出已下线的模型。
**上游顺序会随额度/可用性调整**，以 `or_proxy.py` 顶部注释与 `/health` 返回为准。

**模型池（2026-10 实测，历史数据保留供追溯）**：""")

rep(
    r"""| **豆包 volc**（默认） | seed-tts-2.0，默认 speaker=`zh_female_vv_uranus_bigtts`（当前人设「元气」实际用 `zh_female_xiaohe_uranus_bigtts`），`speech_rate=0` 起步、人设/情绪各自叠加偏移；流式 PCM 24kHz，首响 0.38~0.45s（暖连接） | `XIAOYUN_TTS_ENGINE=volc` |
| **edge-tts** | 保底（豆包挂了她也不哑），音色 `zh-CN-XiaoyiNeural` 等 8 个中文声线 | 自动回退 |""",
    r"""| **豆包 volc**（默认） | seed-tts-2.0（Plan 端点 `/api/v3/plan/tts/unidirectional`），默认 speaker=`zh_female_vv_uranus_bigtts`；`speech_rate` 1:1（+60→1.60x）、`loudness_rate` 生效，`post_process.pitch` 实测无效、无 `emotion` 字段；`audio_params.enable_subtitle=true` 可拿字级时间戳。流式 PCM 24kHz，首响 0.38~0.45s（暖连接） | `XIAOYUN_TTS_ENGINE=volc` |
| **edge-tts** | 保底（豆包挂了她也不哑），6 项中文声线入菜单 | 自动回退 |""")

rep(
    r"""**流式细节**（`tts_stream.py`）：句号级切段（`max_len=60`，日常一两条回复 = 一次请求，语调完整）；
豆包首包与文本长度几乎无关（连接复用才是关键），共享 `requests.Session` 连接池；
等待大脑期间后台预热 TLS 连接（再省 ~0.2s）。""",
    r"""**流式细节**（`tts_stream.py`）：句号级切段（`max_len=60`，日常一两条回复 = 一次请求，语调完整）；
豆包首包与文本长度几乎无关（连接复用才是关键），共享 `requests.Session` 连接池；
等待大脑期间后台预热 TLS 连接（再省 ~0.2s）。
音色菜单共 **21 项**（6 edge + 4 GSV + 11 豆包女声），由 `/api/voice-options` 下发；
桌面壳 `preset_watch` 每 20s 按变化指纹重建菜单，加音色不再需要整壳重启。""")

rep(
    r"""VAD 阈值自适应：`max(下限, 噪声底×K)`，默认下限 0.0010、K=1.3（`XIAOYUN_ASR_THR` / `XIAOYUN_ASR_NOISE_K`）；
换麦克风用 `XIAOYUN_ASR_INPUT="设备名子串"`（注意 USB 音响的采集端点会回环系统播放，**不能当输入**）。""",
    r"""VAD 阈值自适应：`max(下限, 噪声底×K)`，默认下限 **0.0015**、**K=3.2**（`XIAOYUN_ASR_THR` / `XIAOYUN_ASR_NOISE_K`）；
换麦克风用 `XIAOYUN_ASR_INPUT="设备名子串"`（注意 USB 音响的采集端点会回环系统播放，**不能当输入**）。
另有独立 ASR 模型 `doubao-seed-asr-2.0` 走 WebSocket（Plan Key，wav/16bit/单声道），
由 `or_proxy._asr_ws()` 封装，实测比 sauc 通道快 28~31%。""")

rep(
    r"""AgentPlan `doubao-seed-2.1-lite`（OpenAI 兼容 `/api/plan/v3`，原生多模态）。""",
    r"""视觉理解模型 `doubao-seed-2.1-pro/lite`、`2.0-mini`（多模态 chat 端点；1.6 / 1.5-vision 系全 404）。
生图才走 `/api/plan/v3/images/generations`（seedream 5.0-pro/flash）。""")

# ---------- 人设 / 模型库 ----------
rep(
    r"""19 套人格 JSON（`xiaoyun/人设/`），每套 = 人设文本 + 引擎 + 声线 + 语速/音调/情绪：""",
    r"""19 套人格 JSON（`xiaoyun/人设/`），每套 = 人设文本 + 引擎 + 声线 + 语速/音调/情绪（当前生效项看 `current.txt`，不写死在文档里）：""")

rep(
    r"""| `yuanqi`（当前） | 元气 | `gaoleng-youhuo` | 高冷诱惑御姐 |""",
    r"""| `yuanqi` | 元气 | `gaoleng-youhuo` | 高冷诱惑御姐 |""")

rep(
    r"""切换：托盘「人设（切换人格）」子菜单 radio，或 `POST /api/preset {"key":"..."}`，热生效 + 持久化到 `current.txt`。
音色独立于人设（`voice_override.json`），可混搭。""",
    r"""切换：托盘「人设（切换人格）」子菜单 radio、`POST /api/preset {"key":"..."}`，或**语音说「换成傲娇的性格」**（第四类意图）。
音色独立于人设：手动选过一次即写 `voice_override.json` 的 `follow_preset=false`，此后换人设只换人格与语速。
「温柔 / 高冷」这类与音色别名重叠的词，必须带锚点（性格 / 人设 / 人格 / 脾气）才按人格走，否则仍是换音色。""")

rep(
    r"""- **PMX（15 只）**：`assets/models/<key>/`，MMD 原生格式，MMDLoader 加载。
  每只带 `model.json`（yaw/margin/nudge/arm_drop + gamma/contrast/saturate/bright 调色，`/pmxtune` 面板实时调）。
  键名：elysia / feixue / feixue2 / ganyu / gengchen / huangquan / kafka / kafka_yoga / luotianyi / qianxi / raiden / rover / wuluo / xiaoyu / yowane。
- **VRM（13 个）**：`assets/*.vrm`，three-vrm 加载。当前主力 `小芸.vrm`（54 人形骨全手指、14 表情、SpringBone）。
  菜单白名单 `VRM_MENU_KEYS = {"小芸","小芸-jk"}`，新模型写 `assets/<名>.json` 的 `{"in_menu":true}` 放行。
- **Avaturn 照片重建版**：`avaturn_nn.vrm`（187 morph = ARKit52 + 16 viseme + jaw，54 骨含眼球骨）。
- **动作库**：`assets/motions/*.vmd` 15 套，MMD 日文骨名；VRM 页做重定向
  （每帧先全骨铺基准姿态再叠轨道 + ARM_DROP 预烘 + post-multiply 叠加，防"没轨道的骨停在 T-pose"）。""",
    r"""- **PMX（31 套，`_models()` 运行时值）**：`assets/models/<key>/`，MMD 原生格式，MMDLoader 加载。
  每套带 `model.json`（yaw/margin/nudge/arm_drop + gamma/contrast/saturate/bright 调色 + `group`/`order` 分组，
  `/pmxtune` 面板实时调）。菜单显示名统一为「**角色 · 服装**」，分组由 `/api/models` 的 `groups`（4 组）下发。
  键名示例：elysia / feixue / feixue2 / ganyu / kafka / kafka_yoga / raiden / qianxi …（完整清单见 `assets/models/*/model.json`）。
- **表情槽位**：`assets/emo_slots.json` 存 31 套形象 × 7 个 morph 槽的权重标定，31 × 8 情绪 = 248 格全达标。
  注意「笑い」多半是闭眼笑、「にこり」才是睁眼微笑；morph 权重是持久数组，情绪回落必须每帧显式写 0。
- **VRM（实验保留）**：`assets/*.vrm` 与 `desktopvrm.html` 仍在，但**桌面默认页已固定 PMX**（`PET_PAGE=/desktoppmx`），
  `VRM_MENU_KEYS` 为空、假 VRM 已下线，VRM 页只能 `?model=<名>` 显式指定。
  以下两条铁律仅适用于这条实验链路。
- **动作库（19 套）**：`assets/motions/*.vmd` + 同名 `.json`（`group`/`order`/时长），菜单显示「**动作名 · 时长**」，2 组。
  PMX 页直接播 MMD 日文骨名；语音触发的动作为 one-shot（`LoopOnce` + `clampWhenFinished`，播完 `POST /api/motion_done` 回待机）。""")

# ---------- API ----------
rep(
    r"""## 后端 API 一览（66 个端点）

完整路由见 `xiaoyun_server.py`。高频端点：""",
    r"""## 后端 API 一览（69 个路由装饰器 / 63 条唯一路径）

完整路由见 `xiaoyun_server.py`（5088 行）。高频端点：""")

rep(
    r"""| `/api/motions` `/api/motion` | GET/POST | 动作库/切换 |
| `/api/chat-model` | GET/POST | 对话模型（顺序表 ∩ or_proxy 实况，防静默回退） |""",
    r"""| `/api/motions` `/api/motion` | GET/POST | 动作库（`groups` 分组）/切换；语音 one-shot 由 `oneshot`+`oneshot_seq` 下发 |
| `/api/motion_done` | POST | one-shot 播完回报（带 key 校验，旧请求 `stale=true`，播完清冷却） |
| `/api/voice-options` | GET | 21 项音色唯一清单（`/api/voice-options` 下发，壳 20s 刷新） |
| `/api/chat-model` | GET/POST | 对话模型（顺序表 ∩ `/v1/registry` 实况，防静默回退） |""")

# ---------- 语音指令 ----------
rep(
    r"""不用手点托盘——直接说话就能换装、跳舞、换声音。实现在 `voice_command_router.py`（纯函数规则层）。

**指令优先级**：`stop > motion > voice > model`（一句话含多意图时，取说得靠前的半句）。

| 说什么 | 触发 | 例子 |
|---|---|---|
| 停动作 | `停下/别跳了/休息一下/待机/站好` | "停下来休息吧" → 回待机 |
| 换舞 | 动词 + 动作槽位 | "跳个小蛋糕" → 播 xiaodangao |
| 换音色 | 动词 + 音色槽位 | "换成御姐的声音" → 高冷御姐 |
| 换形象 | 动词 + 模型槽位 | "换成甘雨"/"想看佩儿"/"变成5号" → 切模型 |
| 反问 | 槽位歧义/缺失 | "卡芙卡"有两个形态 → 她"你要哪一个呀？" |""",
    r"""不用手点托盘——直接说话就能换装、跳舞、换声音、换性格。实现在 `voice_command_router.py`（纯函数规则层，712 行）。

**指令优先级**：`停动作 > 性格 > 动作 > 音色 > 形象`（一句话含多意图时，取说得靠前的半句）。

| 说什么 | 触发 | 例子 |
|---|---|---|
| 停动作 | `停下/别跳了/休息一下/待机/站好` | "停下来休息吧" → 回待机 |
| 换性格 | 动词 + 人格锚点 | "换成傲娇的性格" → 换人设（不带锚点仍是换音色） |
| 换舞 | 动词 + 动作槽位 | "跳个小蛋糕" → 播一遍自动回待机（one-shot） |
| 换音色 | 动词 + 音色槽位 | "换成御姐的声音" → 高冷御姐 |
| 换形象 | 动词 + 模型槽位 | "换成甘雨"/"想看佩儿"/"变成5号" → 切模型 |
| 反问 | 槽位歧义 / 名字听岔 | 「换成甘兩」→「你说的是「甘雨」吗？」；说「对」即执行 |""")

rep(
    r"""- 槽位是闸门：光说"换成现金"不会误伤——动词命中但槽位点不中就不算指令，照常走对话大脑。
- 40 字以内才算指令，长句一律当对话。""",
    r"""- 槽位是闸门：光说"换成现金"不会误伤——动词命中但槽位点不中就不算指令，照常走对话大脑。
- 40 字以内才算指令，长句一律当对话。
- **近似只用来报候选，绝不用来改状态**：两字名执行层禁近似（「换成甘雨」不会被听成音色「晓雨」），
  三字以上 `FUZZ_MIN=0.72` + `FUZZ_GAP=0.06`；听岔时走独立的 `loose_candidates()` 反问。
- **反问闭环**：`CLARIFY_PENDING` 存候选 45 秒，「对 / 第一个 / 重报名字」都会合成精确名字回放
  `_try_voice_command()`（复用原执行链）；带换/跳动词的新指令先作废待办再正常解析。
- **偏好**：`data/cmd_prefs.json` 只在指令成功执行时累加，仅用于把常点的名字排在反问清单前面，
  不参与任何执行判断，与 `memory.db` 长期记忆完全隔离。
- 完整可说清单见 [docs/语音指令速查表.md](docs/语音指令速查表.md)（生成器产物，禁手改）。""")

# ---------- 目录结构 ----------
rep(
    r"""│  ├─ xiaoyun_server.py    后端（4174 行：66 端点 + 语音时序 + 记忆 + 感知 + 托盘数据源）
│  ├─ desktop_app.py       桌面壳（2488 行：pywebview 透明窗 + Catcher 隐形点击层 + 托盘）""",
    r"""│  ├─ xiaoyun_server.py    后端（5088 行：69 路由装饰器 + 语音时序 + 记忆 + 感知 + 托盘数据源）
│  ├─ desktop_app.py       桌面壳（2703 行：pywebview 透明窗 + 两级 Win32/pystray 同源菜单）""")

rep(
    r"""│  ├─ voice_command_router.py  语音指令（stop>motion>voice>model 四分支 + ASR 友好别名/编号）""",
    r"""│  ├─ voice_command_router.py  语音指令（712 行：四类意图 + 近似只反问 + 偏好排序）
│  ├─ emotion_rules.py     情绪判定唯一真源（规则层 + JUDGE_SYS，与 emo_probe 提示词逐字一致）""")

rep(
    r"""│  ├─ embed_service.py     本地 embedding 兜底（bge-small-zh-v1.5）""",
    r"""│  ├─ embed_service.py     本地 embedding 兜底（8092，bge-small，512 维；主线走 Ollama bge-m3 1024 维）""")

rep(
    r"""│  ├─ assets/              模型（15 PMX + 13 VRM/GLB）、15 VMD 动作、three.js 本地库、lipsync
│  ├─ models/              本地权重（faster-whisper-small/large-v3、bge-small-zh-v1.5，3.6G）
│  ├─ 人设/                19 套人格 JSON + current.txt + voice_override.json
│  ├─ data/                memory.db（54 条事实）、events.jsonl、gaze_mode.txt、poke_style.txt
│  ├─ agentplan/           方舟 Plan Key 链（零依赖）
│  ├─ tools/               65+ 诊断/测试/抓屏/无头验证脚本
│  ├─ docs/                实现说明（口型同步 / 记忆语义检索 / whalepal 对比优化清单）""",
    r"""│  ├─ assets/              31 套 PMX（含 emo_slots.json 表情权重）、19 VMD + 同名 json、three.js 本地库、lipsync
│  ├─ models/              本地权重（faster-whisper-small/large-v3、bge-small-zh-v1.5）
│  ├─ 人设/                19 套人格 JSON + current.txt + voice_override.json
│  ├─ data/                memory.db、events.jsonl、cmd_prefs.json、gaze_mode.txt、poke_style.txt
│  ├─ agentplan/           方舟 Plan Key 链（零依赖）
│  ├─ tools/               59 个 py + 29 个 mjs：诊断/测试/抓屏/无头验证/文档同步与校准
│  ├─ docs/                实现说明、语音指令速查表、PMX 体检报告、验收截图（本地材料，不进仓库）""")

rep(
    r"""│  ├─ scripts/             全部启停/运维脚本（启动/停止/重启/诊断 bat + Start/Stop/Restart/Swap/Diagnose ps1）""",
    r"""│  ├─ scripts/             全部启停/运维脚本（8 个 bat + Start/Stop/Restart/Swap/Diagnose ps1）""")

rep(
    r"""│  ├─ xiaoyun_server.py    后端（核心 py 留根目录：XIAOYUN_DIR 是全部相对路径的锚点）
│  ├─ desktop_app.py / asr_service.py / tts_stream.py / perception.py / …（12 个核心模块同上）
│  ├─ xiaoyun.ico          图标（desktop_app 按 ROOT 引用，留根）""",
    r"""│  ├─ xiaoyun.ico          图标（desktop_app 按 ROOT 引用，留根）""")

rep(
    r"""├─ or_proxy/               对话大脑代理（单文件零依赖）+ README_OR_PROXY.md
├─ nn_pet/                 class 封装重构版（见下）
├─ logs/                   or_proxy 日志输出
├─ README.md               本文件
└─ 迁移历程总结_2026-10-16至10-24.md   九天改造全记录（六阶段）""",
    r"""├─ or_proxy/               对话大脑代理（零明文 Key，已入仓）+ README_OR_PROXY.md + 探测脚本
├─ nn_pet/                 class 封装重构版（10-24 实验分支，**非现行后端**）
├─ docs/                   00 实况索引 + 01~18 编号文档 + 专题 + 调研报告/（5 份大报告）
├─ logs/                   or_proxy 日志输出
├─ README.md               本文件（2026-11-05 校准）
└─ 迁移历程总结_2026-10-16至10-24.md   九天改造全记录 + 第七阶段（10-25~11-05）续篇""")

# ---------- nn_pet / 测试 ----------
rep(
    r"""`nn_pet/` 是把过程式 `xiaoyun_server.py`（171 个函数）按职责重组的 class 等价实现：""",
    r"""> **状态说明（2026-11-05）**：`nn_pet/` 是 10-24 的**重构试验分支**，不是现行后端。
> 此后 11 月所有功能（情绪旁路、表情标定、21 音色、菜单分组、语音指令四类意图与反问闭环）
> 都落在 `xiaoyun/xiaoyun_server.py`（现 5088 行）上，`nn_pet` 未同步，请勿按本章节启动"正式"服务。
> 保留本章节用于设计追溯；`nn_pet/tools/test_nn_pet.py` 的 32 项测试只覆盖该试验分支。

`nn_pet/` 是把当时的过程式 `xiaoyun_server.py` 按职责重组的 class 等价实现：""")

rep(
    r"""```powershell
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
```""",
    r"""```powershell
# 现行主链路回归（改菜单/音色/动作/人设/语音指令后必跑）
$x = "D:\LunaAI\project-nn\runtime\Miniconda3\python.exe"
& $x xiaoyun\tools\test_voice_command_router.py     # 84 项纯函数用例
& $x xiaoyun\tools\test_voice_command_wiring.py     # 真导入服务端 + Flask test_client 真 HTTP
& $x xiaoyun\tools\make_voice_cmd_sheet.py          # 重生成速查表（禁手改）
& $x xiaoyun\tools\test_menu_labels.py              # 菜单标签/分组自检
& $x xiaoyun\tools\test_voice_menu.py               # 21 项音色清单自检
& $x xiaoyun\tools\test_emo_backend.py              # 情绪链路

# 文档一致性与校准（推 GitHub 前）
& $x xiaoyun\tools\sync_docs.py --check
& $x xiaoyun\tools\calibrate_docs_20261105.py --check
& $x xiaoyun\tools\calibrate_readme_20261105.py --check

# 诊断（7 项全 OK 为健康：模型/后端/页面/venv/进程/窗口/日志尾部）
xiaoyun\scripts\诊断小芸桌面.bat

# 真机/无头验证（透明窗必须用 PrintWindow + PW_RENDERFULLCONTENT=2）
node xiaoyun\tools\pmx_emo_lab.mjs
node xiaoyun\tools\face_ab.mjs
```""")

rep(
    r"""**无头测试铁律**：Chrome `--headless` 必须带 `--disable-gpu`；截图与断言分离
（captureScreenshot 崩 SwiftShader → 页面重载丢 fetch 垫片）；CDP 采样抖动 200ms，禁精确 dt 断言；
有 VMD 在播别断言眼骨绝对值。""",
    r"""**无头测试铁律**：**不要带 `--disable-gpu`**（关掉 GPU 后 WebGL 渲染与真机不一致，PMX 表情像素口径会失真）；
桌宠是 `WS_EX_LAYERED` 透明窗，`ImageGrab`/BitBlt 只拍到壁纸，必须
`PrintWindow(..., PW_RENDERFULLCONTENT=2)` + `GetDIBits`（`tools/pet_printwindow.py`）。
截图与断言分离；CDP 采样抖动 200ms，禁精确 dt 断言；`desktopvrm.html` 是 module，
CDP 看不到模块内 `vrm`，一律走页面暴露的 `__xyDbg`，画面差异用**页内 readPixels**。""")

rep(
    r"""### 环境变量（49 个 `XIAOYUN_*`，高频项）""",
    r"""### 环境变量（后端 `xiaoyun_server.py` 内 29 个 `XIAOYUN_*`，高频项）""")

rep(
    r"""| `XIAOYUN_ASR_THR` / `_NOISE_K` | 0.0010 / 1.3 | VAD 阈值下限 / 噪声倍率 |""",
    r"""| `XIAOYUN_ASR_THR` / `_NOISE_K` | 0.0015 / 3.2 | VAD 阈值下限 / 噪声倍率 |""")

rep(
    r"""  实测本机静音 RMS≈0.00015 → 阈值收敛 0.0010~0.0015；""",
    r"""  实测本机静音 RMS≈0.00015；现行下限 0.0015、K=3.2，真正的根治手段是播放期间关麦而非抬阈值；""")

rep(
    r"""│  ├─ desktop.html/2d/3d/fbx、chat.html、settings.html、streamdemo.html、pmxtune.html …共 17 页""",
    r"""│  ├─ desktop.html/2d/3d/fbx、chat.html、settings.html、streamdemo.html、pmxtune.html …（现行有路由页面 11 个）""")

rep(
    r"""```powershell
# 运行 32 项单元测试（无头，test_client 驱动）
runtime\Miniconda3\python.exe nn_pet\tools\test_nn_pet.py
# 启动 class 版后端（默认 8090，XIAOYUN_PORT 可覆盖）
runtime\Miniconda3\python.exe nn_pet\nn_pet\server.py
```""",
    r"""```powershell
# 仅用于试验分支自检，不要当现行回归（现行命令见「开发与测试」）
runtime\Miniconda3\python.exe nn_pet\tools\test_nn_pet.py    # 32 项，只覆盖 nn_pet
# 下面这行仅供设计对照，不要用它启动正式服务（会占住 8090）
# runtime\Miniconda3\python.exe nn_pet\nn_pet\server.py
```""")

# ---------- FAQ / 隐私 / 对比 ----------
rep(
    r"""看 `or_proxy` health 的 `quota_cooldown` 与 `token_usage`：额度烧光会熔断跳档，全链烧光才 502。
`ARK_API_KEY` 与 Plan Key 同值时启动日志有 WARN（自动改用内置 Key）。
模型选择不在 `/v1/registry` 实况清单里时照样返回并插在 auto 后（防界面显示错位）。""",
    r"""看 `or_proxy` health 的 `quota_cooldown` 与 `token_usage`：额度烧光会熔断跳档，全链烧光才 502。
**源码已无内置兜底 Key**：`ARK_API_KEY` 与 Plan Key 同值时启动日志会 WARN 并要求显式设置
`ARK_STANDARD_API_KEY`；四项凭证（ARK / Plan / OpenRouter / ModelScope）全部只读环境变量。
模型选择不在 `/v1/registry` 实况清单里时照样返回并插在 auto 后（防界面显示错位）。""")

rep(
    r"""- 所有数据只落在 `project-nn/` 内：`xiaoyun/data/`（记忆/事件）、各日志文件；
- 截屏数据只在「周期感知」时刻发给**你配置的**豆包视觉端点，算完即弃不落盘；
- 语音：本地 whisper 不出网；云端通道（豆包/百度）按 provider 配置启用；
- or_proxy 只监听 127.0.0.1，上游仅限你配置 Key 的三家；
- 无遥测、无自动更新。""",
    r"""- 所有数据只落在 `project-nn/` 内：`xiaoyun/data/`（记忆/事件/指令偏好）、
  `assets/emo_slots.json`（表情权重）、`人设/voice_override.json`（音色覆盖）、各日志文件；
- `data/cmd_prefs.json` 只记四类指令的 `key → 次数/最近时间`，**只用于排反问清单**，与长期记忆完全隔离；
- 截屏数据只在「周期感知」时刻发给**你配置的**豆包视觉端点，算完即弃不落盘；
- 语音：本地 whisper 不出网；云端通道（豆包/百度）按 provider 配置启用；
- or_proxy 只监听 127.0.0.1，上游仅限你配置 Key 的几家；
- **仓库零明文凭证**：3 把 Key 已于 2026-11-05 从源码迁到用户级环境变量；
  `xiaoyun/data/*.json`、`memory.db*`、`events.jsonl`、`voice_override.json`、`docs/验收截图/` 均在 `.gitignore` 内；
- 无遥测、无自动更新。""")

rep(
    r"""| 角色表现 | 2D 立绘 12 姿势组 | 3D VRM/PMX：眼神追随 + 戳一下挤压物理 + 15 套舞蹈 | 形态不同，3D 更沉浸 |""",
    r"""| 角色表现 | 2D 立绘 12 姿势组 | 3D PMX（31 套形象 / 248 格表情）：眼神追随 + 戳一下挤压 + 19 套舞蹈 | 形态不同，3D 更沉浸 |""")

# ---------- 文档索引 ----------
rep(
    r"""| 文档 | 内容 |
| --- | --- |
| [docs/01-项目大纲.md](docs/01-项目大纲.md) | 定位、愿景、与上游项目的关系、范围、成功标准 |""",
    r"""| 文档 | 内容 |
| --- | --- |
| **[docs/00-文档索引与当前实况.md](docs/00-文档索引与当前实况.md)** | **唯一实况基准**：31 形象 / 19 动作 / 19 人设 / 21 音色 / 69 路由 / 端口契约 / 真源归属 / 复核命令 |
| [docs/01-项目大纲.md](docs/01-项目大纲.md) | 定位、愿景、与上游项目的关系、范围、成功标准 |""")

rep(
    r"""| [docs/05-模型变更历程.md](docs/05-模型变更历程.md) | 对话大脑 qwen2.5→qwen3.5→魔搭→or_proxy 三上游的完整演进 |
| [docs/06-迁移改造历程.md](docs/06-迁移改造历程.md) | 十天改造时间线 + 事故登记簿 Top5 |""",
    r"""| [docs/05-模型变更历程.md](docs/05-模型变更历程.md) | 对话大脑 qwen2.5→qwen3.5→魔搭→or_proxy 的完整演进 + 11 月凭证/模型体系续篇 |
| [docs/06-迁移改造历程.md](docs/06-迁移改造历程.md) | 十天改造时间线 + 10-25~11-05 续篇 + 事故登记簿 Top5 |""")

rep(
    r"""| [docs/16-人物调色盘设置.md](docs/16-人物调色盘设置.md) | 四滑块调色管线、16 只模型实测定档值、VRM 肤色 tint 体系 |
| [docs/17-3D建模历程与素材收集.md](docs/17-3D建模历程与素材收集.md) | VRoid/MMD/AI 生成/Avaturn 四条路线实测、素材网站清单、LLM 做 3D 难在哪 |""",
    r"""| [docs/16-人物调色盘设置.md](docs/16-人物调色盘设置.md) | 四滑块调色管线、模型实测定档值、VRM 肤色 tint 体系 |
| [docs/17-3D建模历程与素材收集.md](docs/17-3D建模历程与素材收集.md) | VRoid/MMD/AI 生成/Avaturn 四条路线实测、素材网站清单、LLM 做 3D 难在哪 |
| **[docs/18-2026-11 变更纪要.md](docs/18-2026-11%20变更纪要.md)** | 本期五条工作线的成果、验收、遗留与未落地项 |
| [docs/PMX表情体检报告.md](docs/PMX表情体检报告.md) | 31 形象 × 8 情绪 = 248 格表情标定口径与 6 条铁律 |
| [docs/调研报告/](docs/调研报告/) | 5 份专项大报告：方舟四类模型能力 / 豆包端点全量探测 / 情绪旁路一期 / 赛博女友调研 / MiniMax H3 清单 |
| [迁移历程总结_2026-10-16至10-24.md](迁移历程总结_2026-10-16至10-24.md) | 九天改造全记录（六阶段）+ 第七阶段 10-25~11-05 续篇 |
| [or_proxy/README_OR_PROXY.md](or_proxy/README_OR_PROXY.md) | 多上游代理的端点、凭证矩阵、故障排查 |""")

rep(
    r"""## 路线图

- 🔁 桌面壳 pywebview 部分迁入 nn_pet class 体系（Catcher/MicButton 整类搬运）
- 🖥️ PMX↔VRM 跨页切换由壳层按 kind 自动选页（当前需重启壳换页）
- 🎤 语音指令扩展至全功能口令化
- 🧠 记忆跨项目共享（工作态门控细化）""",
    r"""## 路线图

**已完成（2026-11-05）**：✅ 情绪旁路 + PMX 表情全量体检 ✅ 21 项音色与动态菜单
✅ 菜单两级分组与双端同源 ✅ 语音指令四类意图 / one-shot / 反问闭环 / 偏好
✅ 三处 docs 分叉消除与全库校准 ✅ 源码零明文凭证、`or_proxy.py` 入仓

**待做**：

- 🚪 出站域名硬白名单（07 / 10 文档的 P2 项）
- 🔐 记忆抽取提示词的隐私红线（不复述密码/密钥/金额）
- 📅 未回应停止追问 / 日历热力图 / 设置面板
- 🖥️ PMX↔VRM 跨页切换由壳层按 kind 自动选页（当前需重启壳换页）
- 🧠 记忆跨项目共享（工作态门控细化）
- 🔁 桌面壳 pywebview 部分迁入 nn_pet class 体系（低优先，主链路仍在过程式后端）""")

# ---------- 文末校准说明 ----------
APPEND = r"""

---

""" + MARK + r"""
## 附：2026-11-05 校准说明

本文件在 2026-11-05 由 `xiaoyun/tools/calibrate_readme_20261105.py` 从 10-24 快照整体校准：
硬数字（31 形象 / 19 动作 / 19 人设 / 21 音色 / 69 路由装饰器 / 5088 行后端 / VAD 0.0015·3.2）
均取自同日**真导入 `xiaoyun_server.py` 后的运行时值**，不是抄旧文档；
10-24 的实测延迟表等历史数据保留原文供追溯，只标注为历史。

改动要点：桌面默认页改为 PMX（VRM 页实验保留、`VRM_MENU_KEYS` 空）、
三套凭证互不通用且源码零明文 Key、启动脚本真实路径 `xiaoyun\scripts\`、
情绪旁路 / 表情标定 / 音色扩容 / 菜单分组 / 语音指令反问闭环五条新能力、
现行回归命令与文档索引。完整成果清单见 `docs/18-2026-11 变更纪要.md`，
实况数字与复核命令见 `docs/00-文档索引与当前实况.md`。

```powershell
& "D:\LunaAI\project-nn\runtime\Miniconda3\python.exe" `
  "D:\LunaAI\project-nn\xiaoyun\tools\calibrate_readme_20261105.py" --check
```
"""


def main():
    check = "--check" in sys.argv
    with open(TARGET, "rb") as f:
        raw = f.read()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    hits = applied = 0
    errors = []
    for old, new in R:
        # 先判 new：有两条规则是「在 old 段尾追加新内容」，new 天然包含 old，
        # 若先判 old 会每次复跑都再追加一遍（上一版校准脚本踩过这个坑）。
        if new in text:
            applied += 1
        elif old in text:
            text = text.replace(old, new, 1)
            hits += 1
        else:
            errors.append("未命中: " + old.strip().splitlines()[0][:70])

    if MARK not in text:
        text = text.rstrip("\n") + "\n" + APPEND
        added = True
    else:
        added = False

    if errors:
        print("校准中止，存在未命中规则：")
        for e in errors:
            print("  " + e)
        return 2

    print("README 校准：本次替换 %d / 已应用 %d / 规则总数 %d / 追加校准说明 %s"
          % (hits, applied, len(R), "是" if added else "否（已有）"))
    if not check and (hits or added):
        out = text.replace("\n", "\r\n") if crlf else text
        with io.open(TARGET, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        print("  已写入 " + TARGET)
    elif check:
        if hits or added:
            print("  --check 模式：检测到待应用变更，未写文件（退出码 1）")
            return 1
        print("  --check 模式：内容已一致，无需变更（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
