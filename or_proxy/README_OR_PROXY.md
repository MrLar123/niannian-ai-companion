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

## 5. 已知限制

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
