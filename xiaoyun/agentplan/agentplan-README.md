# agentplan.py 使用文档

火山方舟 **Agent Plan** 统一命令行客户端。把套餐里的四类能力收敛到一个入口，零第三方依赖（仅标准库），可直接丢进任何 Python 3.9+ 环境跑。

---

## 1. 能力矩阵

| 子命令 | 能力 | 协议 / 端点 | 默认模型 |
|---|---|---|---|
| `chat` | 文本生成 | `{base}/chat/completions`（OpenAI 兼容） | `doubao-seed-2.1-lite` |
| `embed` | 向量化 | `{base}/embeddings`（OpenAI 兼容） | `doubao-embedding-vision` |
| `image` | 图片生成 | `{base}/images/generations`（OpenAI 兼容） | `doubao-seedream-5-0-pro` |
| `tts` | 语音合成 | `openspeech` HTTP 单向 | `doubao-seed-tcs-2.0`（resource-id `seed-tts-2.0`） |
| `asr` | 语音识别 | `openspeech` WebSocket | `doubao-seed-asr-2.0`（resource-id `volc.seedasr.sauc.duration`） |
| `models` | 打印官方模型清单 | 本地硬编码，不发请求 | — |

默认 Base URL：

- LLM / Embedding / Image → `https://ark.cn-beijing.volces.com/api/plan/v3`
- 语音 HTTP → `https://openspeech.bytedance.com/api/v3/plan`
- 语音 WS → `wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async`

---

## 2. 鉴权

Agent Plan 有**专属 API Key**，与常规方舟 API Key 不同，且**与 `/api/v3` 互不通用**（拿 Plan Key 打 `/api/v3` 会返回 401）。

```bash
# 推荐：环境变量
export ARK_PLAN_API_KEY=ark-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
python agentplan.py models
```

```bash
# 或临时传参（注意 --key 必须放在子命令之前）
python agentplan.py --key ark-xxxx models
```

未设置 Key 时脚本直接报错退出（exit code 1），不会发出无意义的请求。

### 环境变量一览

| 变量 | 作用 | 默认值 |
|---|---|---|
| `ARK_PLAN_API_KEY` | 鉴权 Key | 无（必填） |
| `ARK_PLAN_BASE` | LLM/Embed/Image 的 Base URL | `.../api/plan/v3` |
| `ARK_PLAN_VOICE_HTTP` | TTS 的 HTTP Base | `https://openspeech.bytedance.com/api/v3/plan` |
| `ARK_PLAN_VOICE_WS` | ASR 的 WebSocket Base | `wss://openspeech.bytedance.com/api/v3/plan` |

---

## 3. 快速上手

```bash
export ARK_PLAN_API_KEY=ark-xxxx

python agentplan.py chat -m "用一句话介绍你自己"
python agentplan.py embed -i "你好世界" -i "第二条"
python agentplan.py image -p "一只在月光下的猫" --download --output-dir ./img
python agentplan.py tts -t "你好，这是语音合成测试" -o out.mp3
python agentplan.py asr -f record.wav
python agentplan.py models
```

全局参数（`--key` / `--base`）必须写在子命令**之前**：

```bash
python agentplan.py --base https://ark.cn-beijing.volces.com/api/plan/v3 chat -m "hi"
```

---

## 4. 子命令详解

### 4.1 `chat` —— 文本生成

```
python agentplan.py chat [选项]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-m, --message` | — | 用户消息，可重复传多次（按顺序拼成多轮 user） |
| `-s, --system` | — | 系统提示词 |
| `--model` | `doubao-seed-2.1-lite` | 模型名（Lite/Pro 等会自动解析到带日期快照） |
| `--max-tokens` | `1024` | 最大输出 token |
| `--temperature` | `0.8` | 采样温度 |
| `--think` / `--no-think` | `--no-think` | 思考模式开关，默认**关闭**（低延迟） |
| `--stream` | 关 | 流式输出（SSE 增量打印） |
| `-v, --verbose` | 关 | 额外打印 usage 到 stderr |

示例：

```bash
# 带系统人设
python agentplan.py chat -s "你是毒舌助手" -m "今天心情不好"

# 流式 + 开思考
python agentplan.py chat -m "鸡兔同笼，头35脚94，各几只？" --think --stream
```

> 思考模式默认关闭是刻意的：多数模型默认开思考，一句话会先烧 2000+ token。需要推理链时显式加 `--think`。

---

### 4.2 `embed` —— 向量化

```
python agentplan.py embed [选项]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-i, --input` | — | 一条文本，可重复传 |
| `--input-file` | — | 每行一条文本的文件 |
| `--model` | `doubao-embedding-vision` | 向量模型 |
| `--dimensions` | 模型默认 | 指定向量维度（模型支持时） |
| `--output` | — | 输出 JSON 路径；不指定则打印预览 |

示例：

```bash
python agentplan.py embed -i "你好" -i "世界"
# 第 0 条 dim=2048 [-0.0001, -0.0028, -0.0214, 0.0192, -0.0168, …]

python agentplan.py embed --input-file corpus.txt --output vectors.json
# · 已写入 vectors.json（128 条向量）
```

---

### 4.3 `image` —— 图片生成

```
python agentplan.py image -p <提示词> [选项]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-p, --prompt` | **必填** | 画面描述 |
| `--model` | `doubao-seedream-5-0-pro` | 图片模型 |
| `--size` | `1024x1024` | 尺寸 |
| `-n` | `1` | 生成张数 |
| `--download` | 关 | 下载到本地；不加则只打印 URL |
| `--output-dir` | `.` | 下载目录（文件名自动为 `image_0.png` / `image_1.jpg` …） |

示例：

```bash
# 只拿 URL（签名地址，有效期约 24h）
python agentplan.py image -p "赛博朋克风格的重庆夜景"

# 直接下载
python agentplan.py image -p "水墨风格的锦鲤" -n 2 --download --output-dir ./out
```

> 注意：`image` 子命令**没有** `-o` 参数，落盘请用 `--download` + `--output-dir`。

---

### 4.4 `tts` —— 语音合成

```
python agentplan.py tts -t <文本> [选项]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-t, --text` | **必填** | 待合成文本 |
| `--speaker` | `zh_female_gaolengyujie_uranus_bigtts` | 音色 ID |
| `--format` | `mp3` | `mp3` / `wav` / `pcm` |
| `--sample-rate` | `24000` | 采样率 |
| `--timestamp` | 关 | 返回字级时间戳 |
| `--resource-id` | `seed-tts-2.0` | 资源 ID，一般不用改 |
| `--voice-http` | 默认 Base | 覆盖语音 HTTP 端点 |
| `-o, --output` | `tts_output.<format>` | 输出路径 |

示例：

```bash
python agentplan.py tts -t "你好，今天天气不错。" -o hello.mp3
# · 已保存音频 hello.mp3 (30705 bytes)

python agentplan.py tts -t "测试时间戳" --format wav --timestamp -o ts.wav
```

**协议要点**：该端点是**流式多 JSON 拼接**响应（对象之间以换行分隔），每个对象带一段 base64 音频分片，必须把全部 `data` 合并才是完整音频。脚本已正确处理。另外它产出的 WAV 头部 `RIFF` 块大小字段常为 `0xFFFFFFFF` 畸形值，脚本在读取音频时会自动修正。

**已知限制**：`seed-tts-2.0` 无字级时间戳返回（即使带 `enable_timestamp`）；免费额度有限，耗尽后返回 `code: 20000000`。

---

### 4.5 `asr` —— 语音识别

```
python agentplan.py asr -f <音频文件> [选项]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-f, --file` | **必填** | 音频文件（WAV 最佳；其它格式需系统有 `ffmpeg`） |
| `--resource-id` | `volc.seedasr.sauc.duration` | 资源 ID |
| `--segment` | `200` | 音频分片时长（ms） |
| `--voice-ws` | 默认 Base | 覆盖语音 WS 端点 |
| `--stream-out` | 关 | 实时打印增量文本；关则在结束时整段输出 |

示例：

```bash
python agentplan.py asr -f record.wav
python agentplan.py asr -f record.wav --stream-out
```

**输入处理**（`_load_audio` 自动完成，无需手动预处理）：

1. 文件是标准 WAV → 直接使用；
2. 不是 WAV → 调用 `ffmpeg` 转成 16k / 单声道 / `pcm_s16le` 的 WAV（没有 ffmpeg 会明确报错）；
3. WAV 头 `RIFF` 块大小畸形（`0xFFFFFFFF` / `0` / 与文件长度不符）→ 自动修正；
4. **从 WAV 头解析真实采样率/声道/位深**，用于分片大小与请求声明。

> 最后一条很关键：ASR 请求里的 `rate` 必须与实际音频一致。早期版本硬编码 16000，遇到 TTS 产出的 24k 音频时服务端会静默返回空文本，排查了很久。

---

### 4.6 `models` —— 模型清单

```bash
python agentplan.py models
```

Agent Plan **没有公开的模型列表接口**（`/api/plan/v3/models` 返回 404），所以清单来自官方套餐概览文档，硬编码在 `SUPPORTED_MODELS` 里，本地打印、不发请求。

覆盖：文本生成（11 个，含 doubao-seed 2.0/2.1、deepseek-v4 系列、glm-5.3、kimi-k2.8/k3）、向量化、图片生成、视频生成（seedance 2.0/2.5）、语音合成、语音识别。

> 视频生成（seedance）目前**未实现**子命令，仅在清单里列出。

---

## 5. 组合场景

### 5.1 TTS → ASR 回环（验证语音链路）

```bash
export ARK_PLAN_API_KEY=ark-xxxx
python agentplan.py tts -t "今天天气真好，我们去公园散步吧。" --format wav -o loop.wav
python agentplan.py asr -f loop.wav
# 期望输出：今天天气真好，我们去公园散步吧。
```

### 5.2 批量向量化入库

```bash
python agentplan.py embed --input-file docs.txt --output embeds.json
# 输出为标准 OpenAI embeddings 响应 JSON，data[i].embedding 即第 i 条向量
```

### 5.3 多轮对话

```bash
python agentplan.py chat -s "你是资深前端工程师" -m "React 18 的并发特性" -m "讲讲 useTransition"
```

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `✗ 缺少 Agent Plan API Key` | 未设置 `ARK_PLAN_API_KEY` 也没传 `--key` | 设置环境变量，或 `--key` 放子命令前 |
| chat/embed 返回 401 | 拿 Plan Key 打了常规 `/api/v3` | 确认 Base URL 是 `/api/plan/v3`；两套通道**不通用** |
| image 报 `UnsupportedModel` | 模型名写错（如 `seedream-3.0`） | 用 `doubao-seedream-5-0-pro` |
| tts 返回 403 | 缺 `X-Api-Resource-Id` 头 | 脚本已带；若用 `--voice-http` 自定义端点需确认协议一致 |
| tts 返回 `code: 20000000` | 免费额度耗尽 | 等额度恢复或改用付费通道 |
| asr 一发帧就断连 | 末帧 seq 不等于 −(总帧数) | 脚本已按 `full=1, audio=2..C, 末帧=−(C+1)` 实现，改分片逻辑时需同步 |
| asr 返回空文本 | 请求声明的 `rate` 与实际音频不符，或音频本身无有效语音 | 脚本已自动从 WAV 头读真实参数；确认音频有人声 |
| `音频不是 WAV 且 ffmpeg 转码失败` | 非 WAV 输入且系统无 ffmpeg | 先手动转 16k 单声道 WAV，或装 ffmpeg |

---

## 7. 退出码

- `0` —— 成功
- `1` —— 参数缺失、HTTP 失败、业务错误码（均通过 `die()` 输出到 stderr）

---

## 8. 合规提示（务必阅读）

官方文档明确（火山方舟《套餐概览》「额度使用限制」一节）：

> 同一订阅套餐可在所有支持的工具中使用，额度共享。
> **文本生成模型及向量化模型不可用于 API 调用**，在非 AI 工具中使用 Agent Plan 权益对应的 Base URL 和 API Key 有可能被识别为滥用/违规，会导致**订阅停用或账号封禁**。

来源：<https://www.volcengine.com/docs/82379/2366394>（Agent Plan 个人版套餐概览）；企业版同款条款见 <https://docs.volcengine.com/docs/ark/agent-plan-enterprise-plan-overview>。

原文只点名「文本生成模型及向量化模型」——语音（TTS/ASR）不在禁用清单内，属套餐本意；而本 CLI 的 chat/embed/image 子命令（以及桌宠把对话/视觉接在 plan 端点上的用法）都落在「文本生成模型」的禁用范围里，**是直接违反条款，不只是灰色地带**。

请仅在自有、合规的环境中使用本脚本，并自行承担相应风险。
