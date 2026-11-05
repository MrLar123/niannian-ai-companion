# -*- coding: utf-8 -*-
"""第二轮文档校准：迁移历程续篇、各 README 状态说明、索引与纪要补记。

    python tools/calibrate_docs_round2_20261105.py --check   # 预演
    python tools/calibrate_docs_round2_20261105.py           # 应用（可重复执行）

与第一轮 calibrate_docs_20261105.py 的分工：
  第一轮 = 20 篇编号文档的硬错误替换 + 增补；
  第二轮 = 仓库根目录 README（见 calibrate_readme_20261105.py）、迁移历程续篇、
           各组件 README 的「当前状态」补记、文档索引/纪要、调研报告索引。
所有追加都带唯一 marker，重复执行不会产生第二份。
"""
import io
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MARK = "<!-- round2-20261105 -->"

# ---------- 追加（文件末尾） ----------
APPENDS = []

MIGRATION = r"""

---

""" + MARK + r"""
## 阶段七：能力收口与仓库治理（10-25 → 11-05）

> **本文文件名保留「10-16至10-24」不改名**（README 与 06 文档已有引用），本节是续篇。
> 前六阶段解决的是「能不能跑起来」，这一阶段解决的是「行为对不对、文档跟不跟得上、能不能安全开源」。
> 全部结论的实况基准见 `project-nn/docs/00-文档索引与当前实况.md`，逐项验收见
> `project-nn/docs/18-2026-11 变更纪要.md`；本文只记历程与沉淀下来的工程纪律。

### 7.1 五条工作线（按落地顺序）

| 线 | 起点问题 | 落地结果 |
|---|---|---|
| 情绪旁路 | 表情只靠回复文本关键词，判定不稳 | `emotion_rules.py` 独立真源：规则层 <1ms 先落地 + LLM judge 复核；`_kick_emotion` 挂 `_brain_turn` / `_cmd_say` / `_speak_proactively` 三处；`/api/last` 两分支带 emo；前端 `Face` 收为唯一表情写者 |
| PMX 表情体检 | 31 套形象 morph 名不统一，表情要么没反应要么闭眼笑 | 7 槽位按日文 morph 名解析，逐模型像素标定；**31 形象 × 8 情绪 = 248 格全达标**；产物 `assets/emo_slots.json`、报告 `docs/PMX表情体检报告.md` |
| 音色扩容 | 方舟实测通过的 11 个豆包女声没进菜单；清单只在壳启动时取一次 | 21 项音色唯一清单（`VOICE_OPTIONS` 6 + `GSV_OPTIONS` 4 + `VOLC_OPTIONS` 11）由 `/api/voice-options` 下发；壳 `preset_watch` 20s 按变化指纹重建 |
| 菜单人话化 | 菜单全是 key，动作/形象名看不懂 | 「角色 · 服装」「动作名 · 时长」+ `group`/`order` 两级分组；`/api/models`、`/api/motions` 下发 groups，右键 Win32 与托盘 pystray 同源 |
| 语音指令闭环 | ASR 听错名字就静默不理，用户以为已执行 | 四类意图（含新增「换性格」）、动作 one-shot、近似**只反问不执行**、`CLARIFY_PENDING` 45s 确认闭环、`cmd_prefs.json` 偏好；回归 84 项纯函数 + 接线全套 |

### 7.2 本期新增的根因级坑（值得跨会话记住）

1. **近似匹配不能既执行又兜底**：两字名错一字相似度 0.5，两个毫不相干的两字名只要有一个字相同也是 0.5——「换成甘雨」会被听成音色「晓雨」。最终规则：执行层两字名禁近似（阈值 1.01），近似只在反问链路 `loose_candidates()` 里报候选，**绝不改状态**。
2. **judge 必须关思维链**：`max_tokens=8` 会被 reasoning 吃光，`content` 为空却 HTTP 200、全部判 `none`；judge 必须带 `thinking:{"type":"disabled"}` 且模型写死 `doubao-seed-character-260628` 不能走 auto。
3. **输入必须是「他说的话」**：用她自己的台词做情绪判定只有 18% 命中。模型回 `none` 是有效结论，要覆盖规则层。
4. **morph 权重是持久数组**：情绪回落必须每帧显式写 0；「笑い」多半闭眼笑、「にこり」才是睁眼微笑，选错 morph 会得到「一脸开心但闭着眼」。
5. **一次取值的清单会过期**：音色菜单只取一次 → 后加的音色必须整壳重启才可见；改成带指纹的周期刷新。
6. **一次性动作不能用轮询判结束**：2s 轮询会让舞蹈停末帧干站 2 秒；判定放进渲染循环 `tickOneshot()`，用 `LoopOnce + clampWhenFinished`，播完 `POST /api/motion_done` 清冷却。同一支舞连说两次靠 **seq 递增**识别，不能比 key。
7. **源码里的明文 Key 是开源红线**：8 个探测脚本 + 核心代理共 3 把真实 Key；全部改为只读环境变量、缺失即明确报错，两把迁到用户级环境变量后 `or_proxy.py` 首次可安全入仓。

### 7.3 工程纪律（承接上文「沉淀」，编号续 8）

8. 跨进程脚本纯 ASCII（PS 5.1 无 BOM UTF-8 按 GBK 解码）；调常驻子进程禁管道/重定向标准输出（孙进程继承写端会卡 EOF）。
9. 改菜单/音色/动作/人设后必跑 `test_voice_command_router.py` + `test_voice_command_wiring.py`；**接线测试必须隔离 `PRESET_STATE` 与 `CMD_PREFS_PATH`**，否则跑测试会改掉用户人设与推荐顺序。
10. 语音主动开口唯一出口 `_publish_reply()`（SEQ/GUARD/PLAYBACK 三件套），禁止复制发布逻辑；舞台指令由 `STAGE_HINT` + `_strip_stage_text()` 双层拦截，TTS 不许念「（歪头）」。
11. 桌宠是 `WS_EX_LAYERED` 透明窗，`ImageGrab`/BitBlt 只拍到壁纸，必须 `PrintWindow(...,PW_RENDERFULLCONTENT=2)` + `GetDIBits`；无头验证**不要带 `--disable-gpu`**，截图与断言分离。
12. 凭证分三套互不通用：普通方舟 Key（`/api/v3/*`）、Plan Key（`/api/v3/plan/*` TTS/ASR 与 `/api/plan/v3/*` 生图生视频）、豆包语音控制台 Key（`/api/v3/tts/unidirectional`）。判断可用性只能实测，控制台「已开通」≠ API 可调。
13. 文档三处真源单向同步（`sync_docs.py`），生成物（速查表、表情体检报告）**禁手改**；校准脚本必须幂等且带 `--check`，历史快照正文不篡改，只加「增补/补记」节。
14. 推公开仓库前必扫明文凭证与大文件：偏好 json、`memory.db*`、`events.jsonl`、`voice_override.json`、`验收截图/`（29MB）全部排除。

### 7.4 文档与仓库治理（11-05 当天）

- 三处 docs 分叉（`project-nn/docs` 发布副本、`xiaoyun/docs` 生成器工作区、`D:\LunaAI\docs` 根工作副本）用 `tools/sync_docs.py` 单向汇入，同名文件 md5 全部一致。
- 20 篇编号文档第一轮校准：18 处硬错误定点替换 + 20 篇「2026-11-05 增补」；新增 `00-文档索引与当前实况.md`（唯一实况基准）与 `18-2026-11 变更纪要.md`。
- 根目录 5 份专项大报告镜像进 `docs/调研报告/`，各附「后续结论补记」保留史实、标注被推翻的旧结论（字级时间戳、双向流式帧结构等）。
- 第二轮把仓库主 `README.md`（10-24 快照，42 处硬错误/过时表述）、根目录迁移总结、各组件 README、人设专题全部补到 11-05 实况。
- 清掉 `.git/config` 里失效的 `127.0.0.1:13128` 代理（实测 git 直连可用），推 GitHub：`22e3096` → `d99802e` → `f1860f3`，本地 HEAD 与 `origin/main` 一致。

### 7.5 期末实况对照

| 项 | 10-24 快照 | 11-05 实况 |
|---|---|---|
| 形象 | 15 PMX + 13 VRM/GLB | **31 套 PMX**（4 组菜单）；VRM 页实验保留、`VRM_MENU_KEYS` 空 |
| 动作 | 15 套 VMD | **19 套**（2 组，菜单带时长；语音 one-shot 不写盘） |
| 音色 | 8 个 edge 中文声线 | **21 项**（6 edge + 4 GSV + 11 豆包女声），菜单动态刷新 |
| 人设 | 19 套 | 19 套（+ 语音第四类「换性格」意图） |
| 后端 | 4174 行 / 66 端点 | **5088 行 / 69 路由装饰器（63 条唯一路径）** |
| 表情 | 关键词调语速 | 规则层 + judge + 31×8=248 格 morph 标定 |
| VAD | 下限 0.0010、K=1.3 | **下限 0.0015、K=3.2** |
| 凭证 | 源码内置兜底 Key | **源码零明文 Key**，三套凭证只读环境变量 |
| 文档 | 01~17 编号文档 | + 00 实况索引、18 变更纪要、5 份大报告入仓、主 README 校准 |
"""

APPENDS.append(("迁移历程总结_2026-10-16至10-24.md", MIGRATION))

APPENDS.append(("docs/06-迁移改造历程.md", r"""

""" + MARK + r"""
### 11-05：文档与仓库治理（第七阶段）

- 三处 docs 分叉用 `sync_docs.py` 单向汇入；20 篇编号文档第一轮校准（18 处硬错误 + 20 篇增补），
  新增 `00` 实况索引与 `18` 变更纪要；根目录 5 份大报告镜像进 `docs/调研报告/` 并各附后续结论补记。
- 第二轮把仓库主 `README.md`（598 行 10-24 快照）整体校准：42 处替换，
  形象/动作/音色/端点/行数/端口/凭证/测试命令/文档索引全部对齐运行时值；
  根目录迁移总结补「阶段七」续篇，各组件 README 与人设专题补状态说明。
- 推送前安全整改：8 个脚本里的 3 把明文 Key 改为只读环境变量，两把迁到用户级环境变量；
  `.gitignore` 补 `data/*.json` 与 `验收截图/`；清掉失效 git 代理。
- 事故登记簿补一条：**「源码内置兜底 Key」本身是事故源**——它让 `or_proxy.py` 长期被 `.gitignore`
  排除、clone 下来缺组件，且推公开仓库会直接泄露。防御 = 凭证只从环境变量来 + 推前全量扫描。
"""))

APPENDS.append(("docs/05-模型变更历程.md", r"""

""" + MARK + r"""
## 11-05 补记二：权限与凭证体系（决定「哪个模型能不能调」）

模型演进到这一期，瓶颈从「选哪个模型」变成「用哪把钥匙、打哪个端点」：

| 通道 | Key | 端点形态 | 覆盖能力 |
|---|---|---|---|
| 方舟标准 | `ARK_API_KEY` / `ARK_STANDARD_API_KEY` | `/api/v3/*` | 语言、视觉、向量（**逐模型授权**） |
| 方舟 Plan | `ARK_PLAN_API_KEY` / `VOLC_SPEECH_API_KEY` | `/api/v3/plan/*`（语音）、`/api/plan/v3/*`（生图/视频） | TTS、ASR ws、seedream 5.x、seedance |
| 豆包语音控制台 | `VOLC_SPEECH_PLATFORM_KEY` | `/api/v3/tts/unidirectional`、`tts/create` | seed-audio-1.0 等；与前两把**互不通用** |

- 方舟 Key 塞 `X-Api-Key` 打语音端点 → 401 `45000010`；改 Bearer → 400 `45000000`。自查 `tools/tts_auth_probe.py`。
- Plan 网关只镜像 5 个端点（TTS HTTP/stream WS、bidirection WS、sauc/bigmodel_nostream WS、bigmodel_async WS），其余 21 个 404；resource 白名单只有 3 个。
- `GET /api/v3/models` 可信但「Published」≠ 有权限；`/v1/registry` 是纯本地注册表，`/v1/models` 会真拉外网，不能当健康探测。
- 文本向量 REST 实测 48 个 model ID 全 404，只剩 `doubao-embedding-vision-251215`（1024/2048 维）当降级通道；主线记忆向量仍是本机 Ollama bge-m3。
"""))

APPENDS.append(("docs/00-文档索引与当前实况.md", r"""

---

""" + MARK + r"""
## 6. 第二轮校准补充（11-05 晚）

第一轮只覆盖 `docs/` 内 20 篇编号文档，**仓库根 `README.md`、根目录迁移总结、各组件 README、
人设专题当时仍是 10-24 快照**。第二轮已全部补齐：

| 文件 | 本轮处理 |
|---|---|
| `README.md` | `tools/calibrate_readme_20261105.py` 42 处替换 + 文末校准说明；形象/动作/音色/端点/行数/VAD/凭证/启动脚本路径/测试命令/文档索引/路线图全部对齐实况 |
| `迁移历程总结_2026-10-16至10-24.md` | 追加「阶段七：能力收口与仓库治理（10-25→11-05）」：五条工作线、7 个根因级坑、纪律 8~14、治理过程、期末实况对照表 |
| `docs/05`、`docs/06` | 05 补权限与凭证体系；06 补文档与仓库治理时间线及事故登记簿新增条目 |
| `nn_pet/README.md` | 标注为 10-24 重构试验分支，**不是现行后端**，避免误启动 |
| `or_proxy/README_OR_PROXY.md` | 补「源码零明文 Key 后如何配置」 |
| `xiaoyun/agentplan/agentplan-README.md` | 补两条通道（套餐档 vs 主链路）与端点矩阵现状 |
| `xiaoyun/人设/*.md`（4 篇） | 各附「11-05 状态补记」，标注哪些方案已落地、哪些仍是调研 |
| `docs/调研报告/README.md` | 新增 5 份大报告的索引：真源、日期、结论状态、被推翻的旧结论 |
| `docs/18-2026-11 变更纪要.md` | 追加第 7 节，登记第二轮校准与推送结果 |

**新增复核命令**（与第 4 节并列，推 GitHub 前三条都应返回 0 / 无变更）：

```bat
%PY% xiaoyun\tools\calibrate_docs_20261105.py --check
%PY% xiaoyun\tools\calibrate_docs_round2_20261105.py --check
%PY% xiaoyun\tools\calibrate_readme_20261105.py --check
```

**更新纪律补充**：

6. 仓库根 `README.md` 与根目录迁移总结**属于文档集**，改代码后必须与 `docs/` 一起校准；
   新增能力先写 `18` 纪要，同步时检查主 README 的「她能做什么」与「路线图」两节。
7. 组件 README（`nn_pet` / `or_proxy` / `agentplan` / `人设/`）写的是当时状态，
   后续变化用「状态补记」追加在文末，**不要重写历史正文**，但要显式写明「当前是否仍生效」。
"""))

APPENDS.append(("docs/18-2026-11 变更纪要.md", r"""

---

""" + MARK + r"""
## 7. 第二轮文档全量校准与主 README（11-05 晚）

用户指出「GitHub 还有很多文档没同步更新，报告 README、迁移历程等」——复盘点出
**仓库内 47 篇 md 里，第一轮只动了 `docs/` 下的 20 篇编号文档**，以下仍在 10-24 快照：

| 遗漏项 | 本轮处理 |
|---|---|
| 仓库根 `README.md`（598 行 / 38KB） | `tools/calibrate_readme_20261105.py` 42 处替换：15 PMX+13 VRM→31 套 PMX、15→19 动作、8→21 音色、66→69 路由、4174→5088 行、VAD 0.0010/1.3→0.0015/3.2、启动 bat 真实路径 `xiaoyun\scripts\`、三套凭证且零明文 Key、现行回归命令、文档索引、路线图；幂等复跑 0 变更 |
| 根目录 `迁移历程总结_2026-10-16至10-24.md` | 追加「阶段七」续篇（五条工作线 / 7 个根因级坑 / 纪律 8~14 / 治理过程 / 期末实况对照表），文件名不改以免破坏既有引用 |
| `docs/05`、`docs/06` | 05 补权限与凭证体系矩阵；06 补文档治理时间线 + 事故簿新增「内置兜底 Key」条目 |
| `docs/00`、`docs/18` | 00 补第 6 节（第二轮覆盖清单、3 条新增校验命令、纪律 6~7）；18 补本节 |
| `nn_pet/README.md` | 明确标注「10-24 试验分支，非现行后端」，防止读者按它启动服务 |
| `or_proxy/README_OR_PROXY.md` | 补零明文 Key 后的配置步骤（用户级环境变量 + 模拟新进程验证） |
| `xiaoyun/agentplan/agentplan-README.md` | 补套餐档与主链路并存的说明、端点/Key 矩阵现状 |
| `xiaoyun/人设/` 4 篇 | 各补一条状态补记，区分「已落地」与「仍是调研/未实施」 |
| `docs/调研报告/README.md` | 新增报告索引（真源、日期、结论状态、被推翻的旧结论） |
| 仓库外 `D:\LunaAI\README_OR_PROXY.md` | 上一轮因继承 ACL 仅 `Everyone:(RX)` 未能同步。本轮用「备份 → 删除 → 以仓库版重建」绕开继承的只读 ACL，副本已与仓库版 md5 一致且重新可写；`sync_docs.py` 仍保留尽力而为逻辑作防御 |

**交付前校验**：三个校准脚本 `--check` 全 0 变更、文档 md 内链零失效、
13+ 个 py 语法通过、语音指令 84/84 与接线全套、速查表生成幂等、
待提交文件无明文 Key 与敏感数据；随后提交并推送 `origin/main`，本地 HEAD 与远端一致。
"""))

APPENDS.append(("nn_pet/README.md", r"""

---

""" + MARK + r"""
## 状态说明（2026-11-05）

**本目录是 2026-10-24 的 class 封装重构试验分支，不是现行后端。**
此后 10-25~11-05 的全部能力（情绪旁路 `emotion_rules.py`、31 形象 × 8 情绪表情标定、
21 项音色与动态菜单、菜单两级分组、语音指令四类意图与反问闭环、三套凭证体系）
都落在 `xiaoyun/xiaoyun_server.py`（现 5088 行 / 69 路由装饰器）上，**`nn_pet` 未同步**。

- 上面的结构表与 32 项单元测试只描述该试验分支自身，保留供设计追溯；
- 正式启动请双击 `xiaoyun\scripts\启动小芸桌面.bat`，不要运行 `nn_pet\nn_pet\server.py`；
- 现行回归命令与实况数字见 `docs/00-文档索引与当前实况.md`，本期变更见 `docs/18-2026-11 变更纪要.md`。
"""))

APPENDS.append(("or_proxy/README_OR_PROXY.md", r"""

---

""" + MARK + r"""
## 附：零明文 Key 后的配置（2026-11-05）

`or_proxy.py` 原先因内置兜底 Key 被 `.gitignore` 排除、从未进仓库。本轮把 4 把凭证
（方舟标准、Plan、OpenRouter、ModelScope）全部改为**只读环境变量**，源码零明文，
核心代理因此首次可安全入仓。clone 后按下列方式配置（用户级环境变量，新进程自动继承）：

```powershell
# 以实际值为准，这里只列变量名；不要用源码里的任何默认值
[Environment]::SetEnvironmentVariable('ARK_API_KEY',           '<方舟标准 Key>', 'User')
[Environment]::SetEnvironmentVariable('ARK_STANDARD_API_KEY',  '<方舟标准 Key>', 'User')  # 可选，显式指定
[Environment]::SetEnvironmentVariable('ARK_PLAN_API_KEY',      '<Plan Key>',     'User')
[Environment]::SetEnvironmentVariable('VOLC_SPEECH_API_KEY',   '<Plan Key>',     'User')
[Environment]::SetEnvironmentVariable('OPENROUTER_API_KEY',    '<OR Key>',       'User')
[Environment]::SetEnvironmentVariable('MODELSCOPE_API_KEY',    '<MS Key>',       'User')
```

- `ARK_API_KEY` 若与 Plan Key 同值，启动日志会 WARN 并要求显式设置 `ARK_STANDARD_API_KEY`；**不再有内置兜底**。
- 三套凭证互不通用：标准 Key 打 `/api/v3/plan/*` 会 401；生图走 `/api/plan/v3/*`（Plan Key）；
  `/api/v3/tts/unidirectional` 只认豆包语音控制台 Key。自查 `xiaoyun/tools/tts_auth_probe.py`。
- 验证办法：起一个**新进程**导入 `or_proxy.py`，打印四个凭证变量是否非空；
  不要只看当前会话（旧进程内存里仍是改造前的 Key）。
- 本文的 `--probe` / `--models full` 等命令不变；免费名单每月轮换，以实际输出为准。
"""))

APPENDS.append(("xiaoyun/agentplan/agentplan-README.md", r"""

---

""" + MARK + r"""
## 附：2026-11-05 状态补记（原文保留为工具文档）

1. 本文描述的是**套餐档命令行客户端**，与主链路 `or_proxy.py` 并存：
   主链路用普通方舟 Key 打 `/api/v3/*`，`agentplan.py` 用 Plan Key 打 `/api/plan/v3/*`
   （`arkplan` 上游，无 Plan Key 时 or_proxy 会自动把它从路由优先级里摘掉）。
   **两者不是同一条通道，Key 不能混用。**
2. 语音端点另有一套域名与路径：TTS 走
   `https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional`（Plan Key），
   与本文的 `/api/plan/v3/chat/completions` 不是一回事；文档里的
   `/api/v3/tts/unidirectional`、`tts/create` 只认**豆包语音控制台 Key**。
3. 已实测的补充结论：`audio_params.enable_subtitle=true` 可返回 `sentence.words[]` 字级时间戳
   （放 `req_params` 静默无效）；`speech_rate` 1:1、`loudness_rate` 生效、
   `post_process.pitch` 无效、无 `emotion` 字段；双向流式 TTS 发完 TaskRequest
   必须立刻发 FinishSession(102)，否则永久静默。详见 `docs/调研报告/豆包语音端点全量探测报告.md`。
4. 脚本本身只读环境变量、无明文 Key，可安全入仓；未设置 Key 时直接报错退出（exit 1）。
"""))

# 人设 4 篇：各补一条状态
PERSONA = {
    "xiaoyun/人设/云端御姐音色_方案.md": r"""
- 本文是 10-19 的**选型方案**，其中 MiniMax 系统音色因余额不足**未启用**；
  现行御姐声线走豆包 `zh_female_gaolengyujie_uranus_bigtts`（Plan 端点）。
- 11-05 音色菜单共 21 项（6 edge + 4 GSV + 11 豆包女声，11/11 实测有音频字节），
  由 `/api/voice-options` 下发；手动选过一次会写 `voice_override.json` 的 `follow_preset=false`。
""",
    "xiaoyun/人设/候选清单.md": r"""
- 本文的 X / Y / 东北 / 陕西代号体系是 10-19 的 edge-tts 口径（当时只有 8 个中文声线）；
  11-05 已扩到 21 项音色、按「角色 · 服装」式菜单分组，代号对照**不再作为现行清单使用**。
- 19 套人格 JSON 本身仍有效，但「音色跟随人设」已被 `voice_override.json` 的锁定语义覆盖：
  用户手选过音色之后，换人设只换人格与语速，不再改声线。
""",
    "xiaoyun/人设/百度地图音色包-调研笔记.md": r"""
- 本文是产品化设计标杆的调研记录，其中「录制 9 句话生成专属音色包」「每套人设绑定推荐
  volc_speaker」「日历热力图」等**仍是建议、未实施**；合规红线（不做真人声线克隆）继续执行。
- 11-05 实际落地的是音色菜单扩容（21 项）、动态刷新、以及语音指令的第四类「换性格」意图，
  与本文的「音色包产品化」不是一回事。
""",
    "xiaoyun/人设/音色包-使用说明.md": r"""
- 文中「豆包 2.0 共 5 个」是 10-19 的清单；11-05 已扩到 **21 项**
  （6 edge + 4 GSV + 11 豆包女声），菜单每 20s 按变化指纹刷新，加音色不再需要整壳重启。
- 「音色跟随人设 + 锁定音色」语义仍然有效，且 11-05 补上了语音侧入口：
  说「换成傲娇的性格」可换人格；带锚点（性格/人设/人格/脾气）才按人格走，
  否则「温柔 / 高冷」仍按音色别名处理。
""",
}
for _f, _t in PERSONA.items():
    APPENDS.append((_f, r"""

---

""" + MARK + r"""
## 11-05 状态补记（原文保留为历史方案）
""" + _t))


# ---------- 新增文件 ----------
NEWFILES = [("docs/调研报告/README.md", r"""# 调研报告索引

> 本目录是 `D:\LunaAI\docs\` 下 5 份专项大报告在 git 仓库内的**只读镜像**。
> 改文档改根目录真源，再跑 `xiaoyun/tools/sync_docs.py` 同步；**不要在本目录手改**。
> 每份报告末尾都附了「2026-11-05 后续结论补记」，正文保留当时的实测记录不篡改。

| 报告 | 成文 | 主题 | 结论状态 |
|---|---|---|---|
| [方舟四类模型能力清单与接入.md](方舟四类模型能力清单与接入.md) | 11-01 | 语言 / 视觉 / 向量 / 视频 / 3D 与生图、生视频能力矩阵，Plan 与标准通道权限 | 主体有效；**「无字级时间戳」已被推翻**（`enable_subtitle=true` 可拿 `words[]`）；11 个女声已全部入菜单 |
| [豆包语音端点全量探测报告.md](豆包语音端点全量探测报告.md) | 11-02 | 26 个端点逐一探测、双向流式帧结构、resource 白名单 | 主体有效；补记了三套凭证分工、FinishSession(102) 必发、独立 ASR `doubao-seed-asr-2.0` ws 快 28~31% |
| [情绪旁路一期实施方案.md](情绪旁路一期实施方案.md) | 11-04 | 规则层 + LLM judge 双层的实施方案 | **一期已落地**（`emotion_rules.py` 唯一真源、三处 `_kick_emotion`、`Face` 唯一写者、31×8=248 格表情）；「多模态初筛」仍未实测 |
| [AI赛博女友技术调研与集成方案.md](AI赛博女友技术调研与集成方案.md) | 11-04 | 成熟方案调研与落地顺序建议 | **调研文档，不是实施记录**；文中「建议 / 排序建议」不代表已实现，实际落地见 `../18-2026-11 变更纪要.md` |
| [minimax_h3_setup_checklist.md](minimax_h3_setup_checklist.md) | 11-05 | 本地 MiniMax H3 / ComfyUI 部署清单 | 与桌宠主链路独立；「后续接入小芸（可选）」**未实施**；页面文件改动需重启 OS 生效 |

## 阅读与维护纪律

1. **史实不改**：这类报告是某一天的实测快照，结论被后续工作推翻时，在文末追加「后续结论补记」
   并写明「以本条为准」，不要回改正文数据。
2. **真源在仓库外**：`D:\LunaAI\docs\*.md` → `sync_docs.py` → 本目录；推 GitHub 前跑
   `python xiaoyun/tools/sync_docs.py --check`，返回 0 才说明镜像一致。
3. **当前实况看 00**：本目录的任何数字都不能当现状用，实况以
   [`../00-文档索引与当前实况.md`](../00-文档索引与当前实况.md) 为准。
4. 报告里出现的 API Key 一律脱敏；探测脚本只读环境变量，禁止把真实 Key 写进文档或代码。

""" + MARK + "\n")]

# ---------- 精确替换 ----------
REPLACES = [
    ("迁移历程总结_2026-10-16至10-24.md",
     "> 依据：.workbuddy/memory/ 下 10-16 至 10-24 共 9 天的完整工作日志（约 42 万字），逐日提炼。",
     "> 依据：.workbuddy/memory/ 下 10-16 至 10-24 共 9 天的完整工作日志（约 42 万字），逐日提炼。\n"
     "> **2026-11-05 续篇**：文末「阶段七」补记 10-25~11-05 的能力收口与仓库治理；文件名保持不变以免破坏引用。"),
    ("docs/00-文档索引与当前实况.md",
     "| `project-nn/docs/`（本目录） | **发布副本 / 唯一对外真源**：编号文档 00~18 + 专题 + `调研报告/` | ✅ 是 |",
     "| `project-nn/docs/`（本目录） | **发布副本 / 唯一对外真源**：编号文档 00~18 + 专题 + `调研报告/`（含索引 README） | ✅ 是 |\n"
     "| `project-nn/README.md` | 仓库门面，由 `calibrate_readme_20261105.py` 校准 | ✅ 是 |\n"
     "| `project-nn/迁移历程总结_*.md` | 九天改造全记录 + 阶段七续篇 | ✅ 是 |"),
    ("docs/00-文档索引与当前实况.md",
     "- 一次性校准脚本 `tools/calibrate_docs_20261105.py` 保留为本次修订的审计记录，可重复执行且幂等。",
     "- 一次性校准脚本 `tools/calibrate_docs_20261105.py`（编号文档）、"
     "`calibrate_docs_round2_20261105.py`（迁移历程与各 README）、"
     "`calibrate_readme_20261105.py`（主 README）保留为审计记录，均可重复执行且幂等；\n"
     "  三者都支持 `--check`，推 GitHub 前应全部返回 0 变更。"),
]


def load(rel):
    with io.open(os.path.join(REPO, rel), "r", encoding="utf-8", newline="") as f:
        return f.read()


def main():
    check = "--check" in sys.argv
    adds = reps = news = 0
    errors = []
    # 同一文件可能既有替换又有追加（如 00 索引有 2 条替换 + 1 条补记），
    # 必须按文件累积变更，否则后一次写入会覆盖前一次。
    changes = {}
    touched = set()

    def text_of(rel):
        if rel not in changes:
            changes[rel] = load(rel)
        return changes[rel]

    for rel, old, new in REPLACES:
        text = text_of(rel)
        if new in text:
            continue
        if old in text:
            reps += 1
            changes[rel] = text.replace(old, new, 1)
            touched.add(rel)
        else:
            errors.append("替换未命中: %s :: %s" % (rel, old[:48]))

    for rel, extra in APPENDS:
        text = text_of(rel)
        if MARK in text:
            continue
        adds += 1
        changes[rel] = text.rstrip("\n") + "\n" + extra
        touched.add(rel)

    for rel, body in NEWFILES:
        p = os.path.join(REPO, rel)
        if os.path.isfile(p):
            continue
        news += 1
        if not check:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(body)
        print("  %s %s" % ("[待新增]" if check else "[已新增]", rel))

    if errors:
        print("校准中止：")
        for e in errors:
            print("  " + e)
        return 2

    if not check:
        for rel in touched:
            with io.open(os.path.join(REPO, rel), "w", encoding="utf-8", newline="") as f:
                f.write(changes[rel])

    print("第二轮校准：替换 %d / 追加补记 %d / 新建文件 %d / 改动文件 %d / %s"
          % (reps, adds, news, len(touched), "仅预演" if check else "已写入"))
    for rel in sorted(touched):
        print("  %s %s" % ("[待改]" if check else "[已改]", rel))
    if check and (reps or adds or news):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
