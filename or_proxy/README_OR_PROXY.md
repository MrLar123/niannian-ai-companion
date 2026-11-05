# OpenRouter 免费模型 → OpenAI 兼容本地代理

`or_proxy.py` 把本机变成一个标准 OpenAI 格式的模型提供商，后端走 OpenRouter 的免费模型。
零第三方依赖，Python 3.8+ 直接运行。

---

## 1. 启动

### 方式 A：桌面一键启动（推荐）

双击桌面上的 **`启动OpenRouter代理.bat`** 即可。

脚本会自动切换到 `D:\LunaAI` 并在 8787 端口拉起代理，窗口保持打开实时显示日志；
关闭该窗口即停止代理。若端口已被占用会直接报 `Address already in use`，此时先关掉
已有的代理窗口再双击。

> 该 bat 采用 **GBK 编码 + CRLF 行尾**，避免在 cmd 默认代码页下出现中文乱码或解析异常。

### 方式 B：命令行启动

```bash
python or_proxy.py                 # 默认 127.0.0.1:8787
python or_proxy.py --port 8000     # 换端口
python or_proxy.py --models full   # 只看模型清单，不启动服务
python or_proxy.py --probe         # 逐个探活模型实际可用性
```

启动后可用端点：

| 端点 | 说明 |
|---|---|
| `GET /v1/models` | OpenAI 格式模型列表（15 个免费模型） |
| `POST /v1/chat/completions` | 对话，支持流式 SSE 与工具调用 |
| `GET /health` | 健康检查与调用统计 |

---

## 2. 客户端接入（任何 OpenAI 兼容工具）

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8787/v1",
    api_key="sk-local",          # 任意值，代理本地放行
)

resp = client.chat.completions.create(
    model="coding",              # 可用别名，见下表
    messages=[{"role": "user", "content": "写一个快排"}],
)
print(resp.choices[0].message.content)
```

### curl

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-local" \
  -H "Content-Type: application/json" \
  -d '{"model":"super","messages":[{"role":"user","content":"hi"}]}'
```

> Windows 的 cmd / Git Bash 会以系统代码页发送中文 body，代理已内建
> `utf-8 → gb18030 → latin-1` 三级解码回退，不会出现中文丢失或解析失败。

### 可直接使用的上层工具

任何支持自定义 `base_url` 的客户端都能直接接入，例如 Cherry Studio、Cursor、
Continue、LangChain、LlamaIndex、OpenCode 等，填入上面的 Base URL 与任意 api_key 即可。

---

## 3. 模型别名

| 别名 | 实际模型 | 适用 |
|---|---|---|
| `fast` / `lfm` | `liquid/lfm-2.5-2.6b:free` | 探活、低延迟轻任务 |
| `coding` / `poolside` | `poolside/laguna-s-2.1:free` | 编码、工具调用（优先推荐） |
| `super` | `nvidia/nemotron-3-super-120b-a12b:free` | 综合均衡 |
| `ultra` / `nemotron` | `nvidia/nemotron-3-ultra-550b-a55b:free` | 复杂推理、超长上下文 1M |
| `inkling` | `thinkingmachines/inkling:free` | 1M 上下文 + 多模态 |
| `cohere` | `cohere/north-mini-code:free` | 代码任务 |

也可直接传完整模型 ID，如 `nvidia/nemotron-3-ultra-550b-a55b:free`。

---

## 4. Agent 能力选型依据

依据 NVIDIA 官方 agent 基准（Nemotron 3 Ultra 技术博客）：

| 基准 | 能力 | Nemotron 3 Ultra | 对比结果 |
|---|---|---|---|
| IFBench | 指令遵循 | **82%** | 全场第一 |
| Ruler @1M | 长上下文 | **95%** | 全场第一 |
| PinchBench | Agent 生产力 | 91% | 并列第一 |
| ProfBench | 专业搜索任务 | 56% | 并列第一 |
| Terminal-Bench 2.0 | 编码 | 54% | 低于 Kimi K2.6(67%) |

**但实测存在矛盾信号**，这直接影响默认路由设计：

第三方实测（cotera.co）显示 Nemotron 3 Ultra 是同系列中**工具调用最脆弱**的模型
——倾向把 tool call 写成纯文本而非结构化 JSON，5 个 agent 任务失败 4 个。
本次联调也复现了类似现象（Ultra 返回 404 工具函数错误，自动回退后才成功）。

因此代理采用以下策略：

1. **默认** `ultra`（综合推理最强）
2. 请求**携带 `tools` 时，候选链自动把 `poolside/laguna-s-2.1` 提到次位**
   ——Poolside 专门用 RL 训练软件工程与工具使用，可靠性更好
3. `--repair-tools`（默认开启）自动注入约束提示，抑制纯文本工具调用
4. 失败自动沿候选链回退，原因记录在服务端日志

---

## 5. 方舟四类能力端点（2026-11-05 新增）

除对话外，代理现在把方舟的**向量 / 语音识别 / 语音合成**也统一成 OpenAI 兼容路径。
完整清单与实测数据见 `D:/LunaAI/docs/方舟四类模型能力清单与接入.md`。

| 端点 | 能力 | 上游 | 默认 |
|---|---|---|---|
| `POST /v1/chat/completions` | 语言 + 视觉（`image_url`） | ark 多模型 | `auto` |
| `POST /v1/embeddings` | 向量（多模态同空间） | `/embeddings/multimodal` | 1024 维，可传 2048 |
| `POST /v1/audio/transcriptions` | 语音识别 | **WS 流式 ASR**（约快 30%），失败回落 chat + `input_audio` | `seed-asr-2.0`（ws） |
| `POST /v1/audio/speech` | 语音合成 | openspeech：**平台 Key 优先**（`/api/v3/tts/unidirectional`），无则回落 Plan Key（`/api/v3/plan/tts/unidirectional`） | `zh_female_gaolengyujie_uranus_bigtts` |
| `POST /v1/audio/generate` | 音频生成（克隆音色/音效创作，≤120s） | `/api/v3/tts/create`，**仅平台 Key** | `seed-audio-1.0` |
| `POST /v1/images/generations` | 生图 | `seedream-4*` → 普通 Key `/api/v3`（4s）<br>其余 → Plan `/api/plan/v3`（8–15s） | `doubao-seedream-5.0-pro` |
| `GET /v1/registry` | 本机能力地图（离线） | — | 含全部模型/音色/维度/后端 |

```bash
B=http://127.0.0.1:8787; H='-H Authorization:Bearer_local -H Content-Type:application/json'

# 向量（默认降到 1024 维，与现网 bge-m3 向量库兼容，不用重建索引）
curl -s $B/v1/embeddings $H -d '{"input":"小芸在写代码","dimensions":1024}'

# 稀疏向量（仅纯文本输入支持，可做稠密+稀疏混合检索）
curl -s $B/v1/embeddings $H -d '{"input":"小芸在写代码","sparse_embedding":{"type":"enabled"}}'

# 语音识别：audio 支持本机路径 / data URI / 裸 base64；默认 ws，非 wav 自动走 chat
curl -s $B/v1/audio/transcriptions $H -d '{"audio":"D:/tmp/a.wav","language":"zh"}'
curl -s $B/v1/audio/transcriptions $H -d '{"audio":"D:/tmp/a.wav","backend":"chat"}'   # 强制对照

# 语音合成 → mp3 / wav（wav 是本地补头，不是裸 PCM）
curl -s $B/v1/audio/speech $H -d '{"input":"今天天气不错","voice":"yujie"}' -o out.mp3
curl -s $B/v1/audio/speech $H -d '{"input":"今天天气不错","voice":"yujie","response_format":"wav"}' -o out.wav

# 音频生成（需语音平台 Key）：参考音频克隆音色 / 参考图片配音 / 纯文本音效
curl -s $B/v1/audio/generate $H -d '{"text":"一声清脆的风铃声，带一点回声"}' -o sfx.mp3
curl -s $B/v1/audio/generate $H -d '{"text":"@音频1 说出这句话：今天也要开心","audio":"D:/ref.wav"}' -o clone.mp3
curl -s $B/v1/audio/generate $H -d '{"text":"欢迎来到直播间","voice":"yujie","meta":true}'   # 带时长/字幕

# 生图快通道：4.0 走普通 Key，4~5s；参考图字段由代理自动切换
curl -s $B/v1/images/generations $H -d '{"model":"seedream-4","prompt":"一只橘猫","size":"1K"}'

# 视觉：image_url 可以直接写本机文件路径，代理会自动内联成 data URI
curl -s $B/v1/chat/completions $H -d '{"model":"doubao-seed-character-260628","messages":[
  {"role":"user","content":[{"type":"image_url","image_url":{"url":"D:/a.png"}},
                            {"type":"text","text":"描述这张图"}]}]}'
```

**五条硬约束**（都是实测踩出来的，不是文档抄的）：

1. 向量维度**只支持 1024 / 2048**，其它值上游返回 400；
2. 多段 `input` 只回**一条融合向量**，不是 batch——要 N 条就发 N 次；
3. **语音有两套互不相通的凭证体系**：方舟 Plan Key 只能调 `/api/v3/plan/tts/unidirectional`（与 `TTS_URL` 一致）；官方文档的 `/api/v3/tts/unidirectional` 和 `/api/v3/tts/create` 只认**豆包语音控制台的 API Key**（`VOLC_SPEECH_PLATFORM_KEY`）。把方舟 Key 塞进 `X-Api-Key` 一定 401 `45000010 Invalid X-Api-Key`，换 `Bearer` / `Bearer;` 头则是 400 `45000000 app key not found`。只认 `uranus` / `saturn` 系音色；
4. 生图 5.x 走 **Plan Key**，而 **4.0 / 视频 / 3D 反过来走普通 Key**——两把 Key 的权限是逐模型的；
5. ASR(ws) **只吃 wav/16bit/单声道**；mp3 会自动走 chat 路线，不报错但慢一倍；
6. **ASR(ws) 同样只走 Plan 端点**；音频生成 `seed-audio-1.0` 与 TTS 的平台通道共享同一把 `VOLC_SPEECH_PLATFORM_KEY`。

语音平台 Key 申请：<https://console.volcengine.com/speech/new/setting/apikeys> → 建 API Key → 写进环境变量
`VOLC_SPEECH_PLATFORM_KEY`（`setx` 后需重启代理）。配好后 `GET /v1/registry` 的 `tts_channel` 会从
`plan` 变成 `platform`，TTS 即不再占用 Agent Plan 额度、按字符计费。

切换凭证后先跑一遍自检：

```bash
python probe_ark_capabilities.py        # 目录/向量/TTS/ASR双后端/生图/视频/3D
python probe_ark_capabilities.py --all  # 追加 29 个对话模型连通性普查
```

---

## 6. 已知限制

1. **配额**：free tier 通常为 50 次/天，超限返回 429。可用 `--probe` 查看实时可用性。
2. **付费模型不可用**：余额为 0，调用带 `:free` 以外的模型返回 402。
3. **免费名单每月轮换**：第三方免费模型指南（如推荐的 `gpt-oss-120b`、`qwen3-coder`）
   多数已失效。请以 `--models full` 的实际输出为准。
4. **推理模型回填**：nemotron、lfm 等把正文放在 `reasoning` 字段导致 `content` 为 null，
   多数 SDK 会因此报错。代理默认把 reasoning 回填到 content（响应头带
   `X-Reasoning-Fallback: 1`），可用 `--no-reasoning-fallback` 关闭。
5. **WorkBuddy 接入限制**：WorkBuddy 客户端的模型列表由云端下发，目前**不支持**
   自定义 OpenAI `base_url`，因此无法把本代理直接注册为它的模型来源。
   但它可被上述任何其他 OpenAI 兼容客户端与你自己的代码使用。


---

<!-- round2-20261105 -->
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
