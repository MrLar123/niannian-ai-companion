#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
or_proxy.py — 多上游免费模型 → OpenAI 兼容本地代理

把本机变成一个 OpenAI 格式的模型提供商：
    http://127.0.0.1:8787/v1/chat/completions   （对话，支持流式 SSE / 工具调用）
    http://127.0.0.1:8787/v1/models             （模型清单，OpenAI 格式）
    http://127.0.0.1:8787/health                （健康检查）

上游优先级（2026-10-18/19 实测，见 tools/test_*.py / probe_*_result.json）：
  1. openrouter  OpenRouter 免费池。本日起升为首选（原兜底）。
                 **额度硬约束**：免费层 50 次/天，充值 10 credits 后 1000 次/天；
                 当日耗尽返回 429 free-models-per-day。探活同样扣额度，故限制
                 每轮探活数量（PROBE_OR_PER_ROUND）。
  2. ark         火山方舟豆包，国内直连。doubao-seed-2-1-lite 关思考后
                 TTFB≈1s、短对话≈1.7s。每模型 50 万 tokens 免费体验。
                 实测为当前唯一可持续主力（consec_fail=0）。
  3. arkplan     火山方舟 Agent Plan 套餐档（/api/plan/v3，订阅 Key）。
                 deepseek-v4.1-flash 实测 0.8s。仅作备援，不进探活池。
  4. modelscope  魔搭免费推理。**本日起禁用**：账户余额不足，全池返回 429
                 insufficient balance（连未调用过的模型也失败）。设环境变量
                 OR_DISABLE_UPSTREAMS= 即可重新启用。

设计要点：
  1. 零第三方依赖，只用标准库，任意 Python 3.8+ 直接跑。
  2. 密钥只写在脚本顶部或环境变量，不进日志。
  3. 跨上游自动回退：首选上游失败（429 限流/欠费/超时/下线）自动切下一个
     上游，失败原因记录在 X-Fallback 响应头。
  4. 思考模式强制关闭：魔搭 Qwen3.5 与方舟豆包默认开思考，一句话会先"想"
     2000+ token（7~25s）。代理按上游自动注入关闭参数，客户端无感。
  5. 工具调用修复：开启 --repair-tools 时自动注入约束提示。
  6. 别名：可用短名（-m ultra / qwen / doubao / fast 等）代替冗长模型 ID。
  7. auto 低延迟路由：model="auto" 时按实时延迟自动选择，候选链以国内
     上游优先；显式指定模型/别名的请求不受影响。

用法：
    python or_proxy.py                      # 默认 8787 端口启动
    python or_proxy.py --port 8000          # 指定端口
    python or_proxy.py --list               # 只拉取并打印可用免费模型（不启动服务）
    python or_proxy.py --models full        # 连同上下文/能力打印详情
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 强制直连：无论环境变量/系统代理怎么配，本代理一律不走代理。
# （实测：本机直连 openrouter.ai 可达；挂了死代理反而会 10061 拒绝连接。）
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 所有凭证只从环境变量读，源码不内置任何 Key（本文件会 push 到公开 GitHub）。
# 本机已把 OpenRouter / 魔搭的 Key 写入用户级环境变量；新机器按 README 配同名变量即可。
# 2026-11-05 迁移：原先这里硬编码了三把 Key，已在随文档推送前全部清掉。
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODELSCOPE_API_KEY = os.environ.get("MODELSCOPE_API_KEY", "")
# 方舟**标准通道**（/api/v3）的 Key。注意与 Agent Plan Key 是两把不同的钥匙，
# 不可混用（详见 _resolve_ark_key 注释）。仅在 ARK_API_KEY 与 Plan Key 撞车时用。
_ARK_FALLBACK_KEY = os.environ.get("ARK_STANDARD_API_KEY", "")

def _plan_api_key():
    """Agent Plan Key 解析链：环境变量 → 注册表（setx 写的值已启动进程读不到）。

    与 xiaoyun/agentplan/agentplan.py 的 get_plan_key() 保持同一链路：
    ARK_PLAN_API_KEY → VOLC_SPEECH_API_KEY（本机 Plan Key 的现存存放位）。
    """
    for name in ("ARK_PLAN_API_KEY", "VOLC_SPEECH_API_KEY"):
        k = (os.environ.get(name) or "").strip()
        if k:
            return k
    if os.name == "nt":
        try:
            import winreg
            for root, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                for name in ("ARK_PLAN_API_KEY", "VOLC_SPEECH_API_KEY"):
                    try:
                        with winreg.OpenKey(root, path) as key:
                            k = (winreg.QueryValueEx(key, name)[0] or "").strip()
                            if k:
                                return k
                    except OSError:
                        continue
        except Exception:                                    # noqa: BLE001
            pass
    return ""


PLAN_API_KEY = _plan_api_key()


def _env_or_reg(names):
    """按顺序取环境变量，再退回注册表（setx 写的值当前进程读不到）。"""
    for name in names:
        k = (os.environ.get(name) or "").strip()
        if k:
            return k
    if os.name == "nt":
        try:
            import winreg
            for root, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                for name in names:
                    try:
                        with winreg.OpenKey(root, path) as key:
                            k = (winreg.QueryValueEx(key, name)[0] or "").strip()
                            if k:
                                return k
                    except OSError:
                        continue
        except Exception:                                    # noqa: BLE001
            pass
    return ""


def _speech_platform_key():
    """豆包语音控制台（console.volcengine.com/speech/new/setting/apikeys）的 API Key。

    **与方舟 Key 是完全不同的凭证体系**。2026-11-05 实测 6 组组合：
      · openspeech 非 /plan 端点（/api/v3/tts/unidirectional、/api/v3/tts/create）
        对普通方舟 Key 和 Agent Plan Key 一律 401 code=45000010 "Invalid X-Api-Key"；
        不带 Key 则 400 code=45000000 "app key not found in header or query"；
        Bearer / Bearer; 头同样不被识别（仍是 45000000）。
      · 只有 Plan Key + /api/v3/plan/tts/unidirectional 能出声——
        该端点会回调 ark 校验，所以在报错里能看到 "call ark get status code:401"。

    也就是说：把方舟 Key 塞进 X-Api-Key 永远过不去，必须单独申请语音平台 Key。
    拿到后 TTS 走官方文档的普通端点，**不占 Agent Plan 额度**，按字符计费。
    """
    return _env_or_reg(("VOLC_SPEECH_PLATFORM_KEY", "DOUBAO_SPEECH_API_KEY"))


SPEECH_PLATFORM_KEY = _speech_platform_key()


def _resolve_ark_key() -> str:
    """解析方舟 **标准通道**（/api/v3）的 Key。

    2026-10-23 事故：本机 HKCU\\Environment 里 ARK_API_KEY 存放的其实是
    **Agent Plan 通道**的 Key（与 VOLC_SPEECH_API_KEY 同一个值 ark-064d...）。
    Plan Key 只能走 /api/plan/v3，拿去请求 /api/v3 一律 401
    AuthenticationError —— 于是 ark 整档 8 个模型全失败，代理静默回退到
    OpenRouter 免费池，桌宠被代码模型扮演（把思考过程当正文吐出）。

    因此不能盲目信任 ARK_API_KEY 环境变量：
      1. 若另设了 ARK_STANDARD_API_KEY → 优先用它（供用户显式指定）；
      2. 若 ARK_API_KEY 与 Plan Key 相同 → 判定为标准通道不可用，退回
         ARK_STANDARD_API_KEY（未配则为空，标准通道会明确报错，不会静默串道）；
      3. 否则用 ARK_API_KEY。
    """
    std = (os.environ.get("ARK_STANDARD_API_KEY") or "").strip()
    if std:
        return std
    env_key = (os.environ.get("ARK_API_KEY") or "").strip()
    if env_key and PLAN_API_KEY and env_key == PLAN_API_KEY:
        # 此处处于模块加载期，log() 尚未定义，用 sys.stderr 直写
        sys.stderr.write(
            "[init] WARN: ARK_API_KEY equals the Agent Plan Key, which cannot be "
            "used against /api/v3 -> set ARK_STANDARD_API_KEY to a standard "
            "channel key (or fix ARK_API_KEY)\n")
        return _ARK_FALLBACK_KEY
    return env_key or _ARK_FALLBACK_KEY


ARK_API_KEY = _resolve_ark_key()
UPSTREAM_BASE = "https://openrouter.ai/api/v1"
DEFAULT_PORT = 8787
HTTP_TIMEOUT = 120          # 单次上游请求超时（秒）；大参数免费模型容易慢
FALLBACK_TIMEOUT = 45       # 回退尝试时用更短的超时，避免雪崩

# 允许本机/局域网内的 OpenAI SDK 通过 api_key="sk-local" 之类占位串访问
LOCAL_KEY_PASSTHROUGH = True

# 模型池：按 agent 综合表现排序（依据见 README 说明）
#   ultra  → NVIDIA Nemotron 3 Ultra 550B：IFBench 82%(第一)、Ruler@1M 95%(第一)、
#            PinchBench 91%(并列第一)。推理/长上下文/指令遵循最强，1M 上下文。
#   super  → Nemotron 3 Super 120B (MoE 12B active)：能力均衡、延迟更可控。
#   coding → poolside laguna-s-2.1：Poolside 专门用 RL 训练软件工程/工具使用，
#            实测 tool-call 可靠性优于 Ultra（后者易把调用写成纯文本）。
#   fast   → liquid lfm-2.5-2.6b：2.6B 小模型，实测 1.68s 返回，做探活/低延迟任务。
#
# 已下线/不可达的候选根目录：
#   thinkingmachines/inkling:free —— 2026-10-19 实测恒 403「only available on
#   agentic harnesses」，官方限定只能通过其 coding agent / 生产力 App 调用，
#   经本代理的 OpenAI 兼容通道永远失败，故移出池（别名已同步删除）。
MODEL_POOL = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "poolside/laguna-s-2.1:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3.5-lightning:free",
    "liquid/lfm-2.5-2.6b:free",
]

# 短名别名，方便 -m 调用
ALIASES = {
    "ultra": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nemotron": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "super": "nvidia/nemotron-3-super-120b-a12b:free",
    "coding": "poolside/laguna-s-2.1:free",
    "poolside": "poolside/laguna-s-2.1:free",
    # thinkingmachines/inkling 已下线（403 only available on agentic harnesses）
    "cohere": "cohere/north-mini-code:free",
    "fast": "liquid/lfm-2.5-2.6b:free",
    "lfm": "liquid/lfm-2.5-2.6b:free",
    # 国内上游别名
    #   魔搭已禁用，qwen 别名暂时无实义（会在候选链里被跳过）；
    #   deepseek/glm 改指方舟实际在线的档位，避免指到已下线模型。
    "qwen": "Qwen/Qwen3.5-27B",
    "qwen3": "Qwen/Qwen3.5-27B",
    "deepseek": "deepseek-v4-1-flash-economy-261017",
    "glm": "glm-5-3-flash-260828",
    # 方舟额度池（2026-10-20 实测定档）
    "doubao": "doubao-seed-2-1-pro-260915",        # 豆包主力档（lite 额度告急，改指 Pro）
    "lite": "doubao-seed-2-1-lite-260915",         # 显式点名才用（仅剩 3.6 万 tokens）
    "eco": "deepseek-v4-1-flash-economy-261017",   # 359ms，最快
    "char": "doubao-seed-character-260628",        # 420ms，角色扮演，桌宠人设首选
    "v4pro": "deepseek-v4-pro-ga-260813",          # 强模型
    "ark": "doubao-seed-2-1-pro-260915",
    # Agent Plan 备援档别名
    "ds41": "deepseek-v4.1-flash",
    "ds4": "deepseek-v4-flash",
    "arkpro": "doubao-seed-2.1-pro",
}

# ---------------------------------------------------------------------------
# 图片生成：Agent Plan Seedream（2026-10-21 接入）
#   POST /v1/images/generations → /api/plan/v3/images/generations
#   两个模型（2026-10-21 实测均 200）：
#     doubao-seedream-5.0-pro    质量档，1K 约 15s/张
#     doubao-seedream-5-0-flash  速度档，1K 约 9s/张
#   兼容 OpenAI 风格 body，另接受 reference_images / image 做图生图；
#   参考图元素可为 http(s) URL、data URI 或本机文件路径（自动转 base64）。
# ---------------------------------------------------------------------------
IMAGE_BASE = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_IMAGE_MODEL = "doubao-seedream-5.0-pro"
IMAGE_MODELS = {
    "doubao-seedream-5.0-pro": "doubao-seedream-5.0-pro",
    "doubao-seedream-5-0-pro": "doubao-seedream-5.0-pro",
    "doubao-seedream-5-0-flash": "doubao-seedream-5-0-flash",
    "doubao-seedream-5.0-flash": "doubao-seedream-5-0-flash",
    "seedream-pro": "doubao-seedream-5.0-pro",
    "seedream-flash": "doubao-seedream-5-0-flash",
    "seedream": "doubao-seedream-5.0-pro",
    # 4.0 —— 普通 Key（/api/v3），1K 约 4~8s，比 5.0-flash 的 8~9s 更快；
    # 支持 1K/2K/4K 与单图/多图参考，走 ARK_API_KEY 的独立额度（不占 Plan）。
    "doubao-seedream-4-0-20260415": "doubao-seedream-4-0-20260415",
    "doubao-seedream-4-0": "doubao-seedream-4-0-20260415",
    "seedream-4": "doubao-seedream-4-0-20260415",
    "seedream-4.0": "doubao-seedream-4-0-20260415",
}
# 这些模型走普通 Key 的 /api/v3，而不是 Plan 的 /api/plan/v3
ARK_IMAGE_MODELS = {"doubao-seedream-4-0-20260415"}


def _image_ref_to_uri(ref: str) -> str:
    """把参考图归一化成 API 可接受的形式（URL / data URI）。"""
    ref = str(ref).strip()
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    if os.path.isfile(ref):
        import base64
        import mimetypes
        mime = mimetypes.guess_type(ref)[0] or "image/png"
        with open(ref, "rb") as fh:
            return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode("ascii"))
    raise ValueError("参考图无法解析（既非 URL 也不是存在的文件）: " + ref[:120])


def generate_image(body: dict):
    """生图，返回 (OpenAI 风格响应 dict, HTTP 状态码)。

    两条后端，**Key 与路径都不同**（实测 2026-11-05）：
      · Seedream 5.0 pro/flash → /api/plan/v3，Plan Key，8~15s
      · Seedream 4.0           → /api/v3，普通 Key，4~8s，参考图字段是 image/images
    出错时返回 {"error": {...}} 并带非 200 状态码，调用方无需再传 status。
    """
    raw_model = str(body.get("model") or "").strip()
    model = IMAGE_MODELS.get(raw_model, DEFAULT_IMAGE_MODEL)
    use_ark = model in ARK_IMAGE_MODELS

    if use_ark:
        if not ARK_API_KEY:
            return ({"error": {"message": "缺少普通方舟 Key（ARK_API_KEY）",
                               "type": "authentication_error"}}, 401)
        base, key = ARK_BASE, ARK_API_KEY
    else:
        if not PLAN_API_KEY:
            return ({"error": {"message": "缺少 Agent Plan Key（ARK_PLAN_API_KEY / VOLC_SPEECH_API_KEY）",
                               "type": "authentication_error"}}, 401)
        base, key = IMAGE_BASE, PLAN_API_KEY

    payload = {"model": model, "prompt": body.get("prompt") or ""}
    for field in ("size", "output_format", "response_format", "watermark",
                  "optimize_prompt_mode", "background", "count", "sequential", "stream"):
        if body.get(field) is not None:
            payload[field] = body[field]
    payload.setdefault("size", "2K")
    payload.setdefault("response_format", "url")
    # 生图产物默认不带水印（桌宠/建模输入素材不能有角标）
    payload.setdefault("watermark", False)

    refs = body.get("reference_images") or body.get("image") or body.get("images")
    if refs:
        if isinstance(refs, str):
            refs = [refs]
        try:
            uris = [_image_ref_to_uri(r) for r in refs]
        except ValueError as exc:
            return ({"error": {"message": str(exc), "type": "invalid_request_error"}}, 400)
        if use_ark:
            # /api/v3 的 4.0 只认 image（单图）/ images（多图），没有 reference_images
            if len(uris) == 1:
                payload["image"] = uris[0]
            else:
                payload["images"] = uris
        else:
            payload["reference_images"] = uris

    url = base + "/images/generations"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
        log("生图成功 [%s] %.1fs" % (model, time.time() - t0))
        return (obj, 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        log("生图失败 [%s] HTTP %s → %s" % (model, exc.code, detail[:200]))
        return ({"error": {"message": detail, "type": "upstream_error",
                           "upstream_status": exc.code, "model": model}}, 502)
    except Exception as exc:                                  # noqa: BLE001
        log("生图异常 [%s] → %s" % (model, exc))
        return ({"error": {"message": str(exc)[:300], "type": "upstream_error",
                           "model": model}}, 502)


# ---------------------------------------------------------------------------
# 方舟四类能力：语言 / 视觉 / 向量 / 语音（2026-11-05 全量实测）
#
#   目录  GET  /api/v3/models                      55 个条目，带 domain/modalities/features
#   语言  POST /api/v3/chat/completions            13 个 LLM + 15 个 VLM
#   视觉  POST /api/v3/chat/completions            content 里 image_url 传 data URI
#   ASR   WS   wss://openspeech.../api/v3/plan/sauc/bigmodel_nostream  ← 首选，约 1.1s
#         POST /api/v3/chat/completions content type=input_audio       ← 回落，约 2.3s
#   向量  POST /api/v3/embeddings/multimodal       文本/图像/视频 → 同一向量空间
#   TTS   POST openspeech.../tts/unidirectional    Plan Key + resource=seed-tts-2.0
#
# 实测踩过的坑（别再试）：
#   · `/embeddings`（纯文本）本账号 404 ModelNotOpen，目录里 6 个文本向量模型
#     全是 Retiring 且未开通，**只有多模态那个能用**；
#   · 多模态向量只接受 dimensions=2048 / 1024，1536、768、512、256 一律 400；
#   · input 传多段只会返回**一条融合向量**（data 是 dict 不是 list），不是 batch，
#     要批量必须逐条发；
#   · `/audio/transcriptions`、`/audio/speech`、`/rerank` 在方舟 /api/v3 全是 404；
#     语音识别要走 openspeech 的 **WebSocket** 二进制协议（plan/sauc/bigmodel_*），
#     资源名是 `volc.seedasr.sauc.duration`（seedasr，不是 bigasr），必须 Plan Key，
#     且只吃 wav/16bit/单声道；本代理已把它做成 /v1/audio/transcriptions 的首选后端；
#   · ASR 的 `audio_url` 形式被拒（messages.content.type 非法），只认 `input_audio`；
#   · TTS 用普通 ARK Key 会 401，必须 Plan Key；资源只开通 seed-tts-2.0，
#     1.0 与 volc.service_type.10029 均 403；音色只认 uranus/saturn 系，
#     moon 系报「resource ID is mismatched with speaker related resource」；
#   · 生图在 /api/plan/v3 可用（8s/张），视频在 plan 下 UnsupportedModel —— 未订阅。
# ---------------------------------------------------------------------------
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"

EMBED_MODEL = "doubao-embedding-vision-251215"
EMBED_ALIASES = {
    "doubao-embedding-vision": EMBED_MODEL,
    "doubao-embedding": EMBED_MODEL,
    "embedding": EMBED_MODEL,
    "embed": EMBED_MODEL,
    # 兼容既有调用方写死的本地模型名：语义等价时直接换后端，不报错
    "bge-m3": EMBED_MODEL,
    "nomic-embed-text": EMBED_MODEL,
}
EMBED_DIMS = (2048, 1024)
EMBED_DEFAULT_DIMS = 1024          # 与 memory_store 现网维度一致，避免重建索引

# 实测可看图（content 里塞 image_url）的模型快照，按首字延迟排序。
# 只用于 /v1/registry 展示，不参与路由决策（路由仍由 candidates_for 决定）。
VISION_MODELS = [
    "doubao-seed-character-260628",        # 1455ms，看图准，桌宠同款
    "deepseek-v4-1-flash-261017",          # 1934ms
    "doubao-seed-2-0-mini-260428",         # 2982ms
    "doubao-seed-2-1-pro-260915",          # 多模态最全（含音频输入）
    "glm-5-3-flash-260828",                # 9520ms，慢
    "kimi-k3-1-pro-261024",                # 1M 上下文
]

ASR_MODEL = "doubao-seed-2-0-mini-260428"
ASR_ALIASES = {
    "asr": ASR_MODEL,
    "whisper": ASR_MODEL,
    "whisper-1": ASR_MODEL,        # OpenAI 兼容名
    "seed-asr": ASR_MODEL,
}

TTS_MODEL = "seed-tts-2.0"
TTS_DEFAULT_VOICE = "zh_female_gaolengyujie_uranus_bigtts"
TTS_VOICES = {
    # —— 女性 ——
    "zh_female_gaolengyujie_uranus_bigtts": "高冷御姐（桌宠默认音色）",
    "zh_female_vv_uranus_bigtts": "Vv 甜美女声",
    "zh_female_shuangkuaisisi_uranus_bigtts": "爽快思思",
    "zh_female_meilinvyou_uranus_bigtts": "美女友",
    "zh_female_kailangjiejie_uranus_bigtts": "开朗姐姐",
    "zh_female_linjianvhai_uranus_bigtts": "邻家女孩",
    "zh_female_xiaohe_uranus_bigtts": "小何",
    "zh_female_roumeinvyou_uranus_bigtts": "柔美女友",
    "zh_female_tianmeixiaoyuan_uranus_bigtts": "甜美小源",
    # —— 男性 ——
    "zh_male_yangguangqingnian_uranus_bigtts": "阳光青年",
    "zh_male_shaonianzixin_uranus_bigtts": "少年子辛",
    "zh_male_ruyayichen_uranus_bigtts": "儒雅逸辰",
    # —— saturn 系（另一档音色，音质不同）——
    "saturn_zh_female_keainvsheng_tob": "可爱女生（saturn）",
    "saturn_zh_female_tiaopigongzhu_tob": "调皮公主（saturn）",
    "saturn_zh_male_shuanglangshaonian_tob": "爽朗少年（saturn）",
}
TTS_VOICE_ALIASES = {
    "yujie": TTS_DEFAULT_VOICE,
    "gaoleng": TTS_DEFAULT_VOICE,
    "default": TTS_DEFAULT_VOICE,
    "sisi": "zh_female_shuangkuaisisi_uranus_bigtts",
    "vv": "zh_female_vv_uranus_bigtts",
    "keai": "saturn_zh_female_keainvsheng_tob",
    "tiaopi": "saturn_zh_female_tiaopigongzhu_tob",
}
# 语音合成两条通道（2026-11-05 实测矩阵见 _speech_platform_key 注释）：
#   platform → X-Api-Key = 语音控制台 API Key，官方文档端点，不吃 Plan 额度
#   plan     → X-Api-Key = Agent Plan Key，/plan 端点（本机当前唯一可用通道）
TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
TTS_URL_PLATFORM = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
# 音频生成（seed-audio-1.0）：参考音频克隆音色 / 参考图片 / 纯文本描述，
# 单次最长 120s，返回 base64 音频。同样只认语音平台 Key。
AUDIO_GEN_URL = "https://openspeech.bytedance.com/api/v3/tts/create"
AUDIO_GEN_MODEL = "seed-audio-1.0"


def _ark_post(path: str, payload: dict, timeout: int = 60, key: str = ""):
    """方舟 /api/v3 通用 POST，返回 (obj, status)。失败时 obj 是 error 字典。"""
    url = ARK_BASE + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + (key or ARK_API_KEY))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        try:
            code = json.loads(detail)["error"]["code"]
        except Exception:                                    # noqa: BLE001
            code = ""
        return ({"error": {"message": detail, "type": "upstream_error",
                           "upstream_status": exc.code, "code": code}}, exc.code)
    except Exception as exc:                                 # noqa: BLE001
        return ({"error": {"message": str(exc)[:300], "type": "upstream_error"}}, 502)


def _to_content_parts(inp) -> list:
    """把 OpenAI 风格的 input 归一化成方舟多模态 content 数组。"""
    if isinstance(inp, str):
        return [{"type": "text", "text": inp}]
    if isinstance(inp, dict):
        inp = [inp]
    parts = []
    for it in inp or []:
        if isinstance(it, str):
            parts.append({"type": "text", "text": it})
        elif isinstance(it, dict):
            if it.get("type") in ("text", "image_url", "video_url", "input_audio"):
                parts.append(it)
            elif "text" in it:
                parts.append({"type": "text", "text": it["text"]})
            elif "image_url" in it:
                parts.append({"type": "image_url", "image_url": it["image_url"]})
            else:
                parts.append({"type": "text", "text": json.dumps(it, ensure_ascii=False)})
    return parts


def _inline_local_media(body: dict) -> int:
    """把 messages 里的本地文件路径就地转成 data URI，返回转换个数。

    上游只认 URL 或 base64。桌宠的 perception 截图、测试用的 png 都是本地文件，
    不转的话上游会把路径当成坏 URL —— 表现是**静默失败后回退到别的模型**，
    日志里只看到「回退生效」，很难定位。
    """
    n = 0
    for m in body.get("messages") or []:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for part in c:
            if not isinstance(part, dict):
                continue
            for key in ("image_url", "video_url"):
                holder = part.get(key)
                if not isinstance(holder, dict):
                    continue
                u = holder.get("url")
                if isinstance(u, str) and not u.startswith(("http://", "https://", "data:")) \
                        and os.path.isfile(u):
                    try:
                        holder["url"] = _image_ref_to_uri(u)
                        n += 1
                    except ValueError:
                        pass
            ia = part.get("input_audio")
            if isinstance(ia, dict) and isinstance(ia.get("data"), str) \
                    and os.path.isfile(ia["data"]):
                try:
                    b64, fmt = _audio_to_b64(ia["data"])
                    ia["data"] = b64
                    ia.setdefault("format", fmt)
                    n += 1
                except ValueError:
                    pass
    return n


def ark_embed(body: dict):
    """OpenAI 兼容 /v1/embeddings → 方舟 /embeddings/multimodal。

    返回值统一成 OpenAI 形状（data 是 list、带 index），并把方舟的
    「一条融合向量」如实呈现 —— 多段输入不会被拆成多条。
    """
    raw_model = str(body.get("model") or "").strip()
    model = EMBED_ALIASES.get(raw_model, raw_model or EMBED_MODEL)
    if model not in (EMBED_MODEL,):
        # 目录里其它向量模型已 Retiring 且未开通，直接指到唯一可用那个
        log("向量模型 %s 不可用，回落到 %s" % (model, EMBED_MODEL))
        model = EMBED_MODEL

    payload = {"model": model, "input": _to_content_parts(body.get("input"))}
    if not payload["input"]:
        return ({"error": {"message": "input 不能为空", "type": "invalid_request_error"}}, 400)
    dims = body.get("dimensions") or body.get("dim")
    if not dims:
        # 默认降到 1024：与 memory_store 现网 bge-m3 向量库对齐，换后端不用重建索引。
        # 要原生 2048 必须显式传 dimensions=2048。
        dims = EMBED_DEFAULT_DIMS
    if dims:
        try:
            dims = int(dims)
        except Exception:                                    # noqa: BLE001
            return ({"error": {"message": "dimensions 必须是整数", "type": "invalid_request_error"}}, 400)
        if dims not in EMBED_DIMS:
            return ({"error": {"message": "dimensions 只支持 %s（其它值方舟返回 400）"
                                          % (list(EMBED_DIMS),),
                               "type": "invalid_request_error"}}, 400)
        payload["dimensions"] = dims

    # 其余官方字段原样透传（文档 82379/1523520）：
    #   encoding_format float|base64|null
    #   instructions     推理提示词（不传则由输入模态决定默认值）
    #   sparse_embedding 稀疏向量开关，**仅纯文本输入**支持（混合检索有用）
    #   multi_embedding  多向量输出，开启后额外返回 data.multi_embedding
    for f in ("encoding_format", "instructions", "sparse_embedding", "multi_embedding"):
        if body.get(f) is not None:
            payload[f] = body[f]

    t0 = time.time()
    obj, st = _ark_post("/embeddings/multimodal", payload, timeout=60)
    if st != 200:
        log("向量失败 [%s] %s → %s" % (model, st, str(obj)[:160]))
        return (obj, 502 if st >= 500 else st)

    d = obj.get("data") or {}
    emb = d.get("embedding") or []
    item = {"object": "embedding", "index": 0, "embedding": emb}
    if d.get("multi_embedding"):
        item["multi_embedding"] = d["multi_embedding"]
    if d.get("sparse_embedding"):
        # 稀疏向量（仅纯文本可开）：可做稠密+稀疏混合检索，比纯稠密召回强
        item["sparse_embedding"] = d["sparse_embedding"]
    return ({
        "object": "list",
        "model": model,
        "data": [item],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
        # 方舟对多段 input 只回一条融合向量，调用方需知悉
        "fusion": len(payload["input"]) > 1,
        "latency_ms": int((time.time() - t0) * 1000),
    }, 200)


def _audio_to_b64(spec: str) -> tuple:
    """把 audio 字段解析成 (base64, format)。支持本机路径 / data URI / 裸 base64。"""
    import base64
    s = str(spec or "").strip()
    if not s:
        raise ValueError("缺少 audio")
    if s.startswith("data:"):
        head, _, b64 = s.partition(",")
        fmt = "mp3"
        if "/" in head:
            fmt = head.split("/")[1].split(";")[0]
        return b64, fmt
    if os.path.isfile(s):
        ext = os.path.splitext(s)[1].lower().lstrip(".") or "mp3"
        with open(s, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii"), ext
    # 看起来像路径但文件不存在 → 本地就报清楚，别把路径当 base64 发给上游
    # （上游只会回 "InvalidParameter messages[i]"，看不出真实原因）
    if "/" in s or "\\" in s or \
            os.path.splitext(s)[1].lower() in (".wav", ".mp3", ".m4a", ".ogg", ".pcm", ".flac"):
        raise ValueError("音频文件不存在: " + s[:160])
    return s, "mp3"


ASR_WS_URL = "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream"
ASR_WS_RESOURCE = "volc.seedasr.sauc.duration"     # 豆包流式语音识别 2.0（小时版）


def _wav_info(data: bytes):
    """解析 WAV 头，返回 {format, channels, rate, bits}；非 WAV 返回 None。"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"fmt " and pos + 24 <= len(data):
            return {"format": int.from_bytes(data[pos + 8:pos + 10], "little"),
                    "channels": int.from_bytes(data[pos + 10:pos + 12], "little"),
                    "rate": int.from_bytes(data[pos + 12:pos + 16], "little"),
                    "bits": int.from_bytes(data[pos + 22:pos + 24], "little")}
        pos += 8 + size
    return None


def _asr_ws(wav: bytes, rate: int, timeout: float = 30.0) -> str:
    """调方舟流式语音识别（WebSocket 二进制协议），返回识别文本。

    协议要点：4 字节头 + seq(int32) + payload_len(uint32) + gzip(JSON/音频)，
    首帧是 CLIENT_FULL_REQUEST(0b0001) 描述音频格式，之后每帧 CLIENT_AUDIO_ONLY
    (0b0010)，最后一帧 seq 取负值并把 flags 置 NEG_WITH_SEQUENCE(0b0011)。
    """
    import asyncio
    import gzip
    import struct
    import uuid

    try:
        import aiohttp
    except ImportError as exc:                                # noqa: BLE001
        raise RuntimeError("缺少 aiohttp，无法使用流式 ASR") from exc

    def _hdr(mtype: int, flags: int) -> bytes:
        # version=1,header_size=1 | message_type<<4|flags | json<<4|gzip | reserved
        return bytes([(1 << 4) | 1, (mtype << 4) | flags, (1 << 4) | 1, 0])

    def _parse(buf: bytes):
        if len(buf) < 4:
            return None
        mtype = buf[1] >> 4
        flags = buf[1] & 0x0F
        comp = buf[2] & 0x0F
        pos = 4
        if flags & 0x01:
            pos += 4
        if pos + 4 > len(buf):
            return None
        size = struct.unpack(">I", buf[pos:pos + 4])[0]
        pos += 4
        payload = buf[pos:pos + size]
        if comp == 0x01:
            try:
                payload = gzip.decompress(payload)
            except Exception:                                # noqa: BLE001
                pass
        text = ""
        try:
            j = json.loads(payload.decode("utf-8"))
            res = j.get("result") or {}
            if isinstance(res, dict):
                text = res.get("text") or ""
                if not text:
                    text = "".join(u.get("text", "") for u in (res.get("utterances") or []))
            if not text and j.get("message"):
                raise RuntimeError("ASR 上游 %s %s" % (j.get("code"), j.get("message")))
        except json.JSONDecodeError:
            pass
        return {"type": mtype, "text": text}

    async def _run() -> str:
        headers = {"X-Api-Key": PLAN_API_KEY, "X-Api-Resource-Id": ASR_WS_RESOURCE,
                   "X-Api-Request-Id": str(uuid.uuid4()),
                   "X-Api-Connect-Id": str(uuid.uuid4()), "X-Api-Sequence": "-1"}
        text = ""
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(ASR_WS_URL, headers=headers, timeout=timeout) as ws:
                meta = {"user": {"uid": "or_proxy"},
                        "audio": {"format": "wav", "codec": "raw", "rate": rate,
                                  "bits": 16, "channel": 1},
                        "request": {"model_name": "bigmodel", "enable_itn": True,
                                    "enable_punc": True, "show_utterances": True}}
                p = gzip.compress(json.dumps(meta).encode())
                await ws.send_bytes(_hdr(0b0001, 0b0001) + struct.pack(">i", 1)
                                    + struct.pack(">I", len(p)) + p)
                frame = rate * 2 * 200 // 1000          # 200ms 一帧
                chunks = [wav[i:i + frame] for i in range(0, len(wav), frame)]
                seq = 2
                for i, ch in enumerate(chunks):
                    last = (i == len(chunks) - 1)
                    c = gzip.compress(ch)
                    await ws.send_bytes(
                        _hdr(0b0010, 0b0011 if last else 0b0001)
                        + struct.pack(">i", -seq if last else seq)
                        + struct.pack(">I", len(c)) + c)
                    seq += 1
                    await asyncio.sleep(0.005)
                deadline = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=8)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        r = _parse(msg.data)
                        if r and r["text"]:
                            text = r["text"]
                        if r and r["type"] == 0b1111:
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                await ws.close()
        return text

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


def ark_transcribe(body: dict):
    """语音识别：优先方舟流式 ASR（WS，约 1.1s），失败回落到 chat+input_audio。

    实测（2026-11-05）：同一个 3s 样本，WS 路线 1.12s、chat 路线 2.1~2.5s；
    WS 只吃 wav，且必须用 Plan Key（普通 Key 报 acquire failed 401）。
    非 wav 或 `backend=chat` 时直接走 chat，避免多一层失败重试。
    """
    raw_model = str(body.get("model") or "").strip()
    model = ASR_ALIASES.get(raw_model, raw_model or ASR_MODEL)
    spec = body.get("audio") or body.get("file") or body.get("input_audio")
    if isinstance(spec, dict):
        spec = spec.get("data") or spec.get("path")
    try:
        b64, fmt = _audio_to_b64(spec)
    except ValueError as exc:
        return ({"error": {"message": str(exc), "type": "invalid_request_error"}}, 400)

    backend = str(body.get("backend") or "").strip().lower()
    if fmt == "wav" and PLAN_API_KEY and backend != "chat":
        import base64
        try:
            raw = base64.b64decode(b64)
            info = _wav_info(raw)
            if info and info["channels"] == 1 and info["bits"] == 16:
                t0 = time.time()
                text = _asr_ws(raw, info["rate"])
                if text:
                    log("ASR(ws) 成功 %.2fs %d 字" % (time.time() - t0, len(text)))
                    return ({"text": text, "model": "seed-asr-2.0", "backend": "ws",
                             "latency_ms": int((time.time() - t0) * 1000)}, 200)
                log("ASR(ws) 返回空文本，回落到 chat 路线")
        except Exception as exc:                             # noqa: BLE001
            log("ASR(ws) 失败，回落到 chat 路线 → %s" % str(exc)[:160])

    lang = body.get("language")
    prompt = "把这段音频逐字转成文字，只输出文字，不要加任何说明。"
    if lang and lang != "zh":
        prompt = "Transcribe this audio verbatim. Output the text only."
    payload = {"model": model, "max_tokens": int(body.get("max_tokens") or 2048),
               "messages": [{"role": "user", "content": [
                   {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                   {"type": "text", "text": prompt}]}]}
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]

    t0 = time.time()
    obj, st = _ark_post("/chat/completions", payload, timeout=180)
    if st != 200:
        log("ASR 失败 [%s] %s → %s" % (model, st, str(obj)[:160]))
        return (obj, 502 if st >= 500 else st)
    text = (obj["choices"][0]["message"].get("content") or "").strip()
    return ({"text": text, "model": model, "backend": "chat",
             "latency_ms": int((time.time() - t0) * 1000)}, 200)


def _pick_tts_channel():
    """返回 (url, key, channel) 或 (None, None, reason)。

    platform 优先：不占 Agent Plan 额度，按字符计费，且是官方文档路径。
    """
    if SPEECH_PLATFORM_KEY:
        return TTS_URL_PLATFORM, SPEECH_PLATFORM_KEY, "platform"
    if PLAN_API_KEY:
        return TTS_URL, PLAN_API_KEY, "plan"
    return None, None, "未配置任何语音 Key"


def ark_speech(body: dict):
    """语音合成 → openspeech 流式端点。返回 (bytes, mime) 或 (err_dict, status)。

    双通道（见 _pick_tts_channel）：语音平台 Key 优先，否则回落 Agent Plan Key。
    只支持 seed-tts-2.0 + uranus/saturn 音色（见文件头实测结论）。
    """
    url, key, channel = _pick_tts_channel()
    if not url:
        return ({"error": {"message": "缺少语音 Key：普通通道需 VOLC_SPEECH_PLATFORM_KEY"
                                      "（豆包语音控制台 API Key），Plan 通道需 "
                                      "ARK_PLAN_API_KEY / VOLC_SPEECH_API_KEY",
                           "type": "authentication_error"}}, 401)
    raw_voice = str(body.get("voice") or body.get("speaker") or "").strip()
    voice = TTS_VOICE_ALIASES.get(raw_voice, raw_voice or TTS_DEFAULT_VOICE)
    if voice not in TTS_VOICES:
        return ({"error": {"message": "未知音色 %r，可用音色见 /v1/registry 的 tts_voices"
                                      % voice, "type": "invalid_request_error"}}, 400)
    text = str(body.get("input") or body.get("text") or "").strip()
    if not text:
        return ({"error": {"message": "input 不能为空", "type": "invalid_request_error"}}, 400)

    fmt = (body.get("response_format") or body.get("format") or "mp3").lower()
    want_wav = fmt == "wav"
    if fmt not in ("mp3", "pcm", "ogg_opus", "ogg", "wav"):
        fmt = "mp3"
    # 上游只出 mp3/pcm/ogg；要 wav 就先取 pcm，本地补 44 字节头
    # （不补的话调用方拿到的是裸 PCM，ASR/播放器会报 "not valid wav format"）
    up_fmt = "pcm" if want_wav else fmt
    rate = int(body.get("sample_rate") or 24000)
    ap = {"format": up_fmt, "sample_rate": rate,
          "speech_rate": int(body.get("speed_rate") or body.get("speech_rate") or 0)}
    payload = {"user": {"uid": body.get("uid") or "or_proxy"},
               "req_params": {"text": text, "speaker": voice, "audio_params": ap}}
    # 上一轮实测：该音色静默忽略 emotion 参数，所以不转发，避免制造"已生效"错觉

    import uuid
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", key)
    req.add_header("X-Api-Resource-Id", TTS_MODEL)
    req.add_header("X-Api-Request-Id", str(uuid.uuid4()))
    t0 = time.time()
    buf = bytearray()
    err = ""
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for line in resp:
                s = line.decode("utf-8", "ignore").strip()
                if not s:
                    continue
                if s.startswith("data:"):
                    s = s[5:].strip()
                try:
                    j = json.loads(s)
                except Exception:                            # noqa: BLE001
                    continue
                d = j.get("data")
                if isinstance(d, str):
                    import base64
                    try:
                        buf.extend(base64.b64decode(d))
                    except Exception:                        # noqa: BLE001
                        pass
                if j.get("message"):
                    err = str(j["message"])[:200]
    except urllib.error.HTTPError as exc:
        # 非 /plan 端点鉴权失败是 HTTP 401 + body 里 45000010 Invalid X-Api-Key；
        # 只回 "HTTP Error 401" 看不出是 Key 体系不对，必须把 body 带出来
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:400]
        except Exception:                                    # noqa: BLE001
            pass
        log("TTS 上游 %s [%s/%s] → %s" % (exc.code, channel, voice, detail or exc.reason))
        return ({"error": {"message": "上游 %s：%s" % (exc.code, detail or exc.reason),
                           "channel": channel, "type": "upstream_error"}}, 502)
    except Exception as exc:                                 # noqa: BLE001
        log("TTS 异常 [%s] → %s" % (voice, exc))
        return ({"error": {"message": str(exc)[:300], "type": "upstream_error"}}, 502)

    if not buf:
        log("TTS 无音频 [%s] %s" % (voice, err))
        return ({"error": {"message": err or "上游未返回音频（音色与资源档不匹配？）",
                           "type": "upstream_error", "voice": voice}}, 502)

    if want_wav:
        import io
        import wave
        wb = io.BytesIO()
        with wave.open(wb, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(buf))
        buf = bytearray(wb.getvalue())

    mime = {"mp3": "audio/mpeg", "pcm": "audio/pcm",
            "ogg": "audio/ogg", "ogg_opus": "audio/ogg",
            "wav": "audio/wav"}.get(fmt, "audio/mpeg")
    log("TTS 成功 [%s] %s %.1fs %d bytes (channel=%s)"
        % (voice, fmt, time.time() - t0, len(buf), channel))
    return (bytes(buf), mime)


def ark_audio_generate(body: dict):
    """音频生成（seed-audio-1.0）→ POST /api/v3/tts/create。

    与 TTS 的区别：这是"创作"而非"朗读"——可按参考音频克隆音色、按参考图片
    生成配音、按自然语言描述造音效，单次最长 120 秒。
    只认语音平台 Key（普通/Plan 方舟 Key 均 401，见 _speech_platform_key）。

    请求（OpenAI 风格包装，字段直通上游）：
      {"text": "...", | "text_prompt": "...", "voice": "<音色ID>",
       "audio": "<本机路径|dataURI|base64>", "audio_url": "https://...",
       "image": "<本机路径|dataURI|base64>", "image_url": "https://...",
       "format": "mp3", "sample_rate": 24000, "speech_rate": 0,
       "loudness_rate": 0, "pitch_rate": 0, "subtitle": false}
    返回：默认音频字节流；body.meta=true 时返回 JSON（含 base64/url/时长）。
    """
    if not SPEECH_PLATFORM_KEY:
        return ({"error": {"message": "音频生成需要豆包语音控制台 API Key"
                                      "（环境变量 VOLC_SPEECH_PLATFORM_KEY）。"
                                      "方舟普通 Key / Plan Key 在非 /plan 语音端点一律 401 "
                                      "code=45000010，2026-11-05 实测。",
                           "type": "authentication_error",
                           "console": "https://console.volcengine.com/speech/new/setting/apikeys"}},
                401)

    text = str(body.get("text_prompt") or body.get("text") or body.get("input") or "").strip()
    if not text:
        return ({"error": {"message": "text/text_prompt 不能为空",
                           "type": "invalid_request_error"}}, 400)

    refs = []
    voice = str(body.get("voice") or body.get("speaker") or "").strip()
    if voice:
        refs.append({"speaker": TTS_VOICE_ALIASES.get(voice, voice)})
    else:
        # audio_data / audio_url 与 speaker 互斥，逐个尝试（只取第一个非空）
        spec = body.get("audio") or body.get("audio_data")
        if spec:
            try:
                b64, _fmt = _audio_to_b64(spec)
            except ValueError as exc:
                return ({"error": {"message": str(exc), "type": "invalid_request_error"}}, 400)
            refs.append({"audio_data": b64})
        elif body.get("audio_url"):
            refs.append({"audio_url": str(body["audio_url"])})

    img_spec = body.get("image") or body.get("image_data")
    if img_spec:
        if refs:
            return ({"error": {"message": "图片参考不能与音频/音色参考混用（上游限制）",
                               "type": "invalid_request_error"}}, 400)
        try:
            uri = _image_ref_to_uri(str(img_spec))
        except ValueError as exc:
            return ({"error": {"message": str(exc), "type": "invalid_request_error"}}, 400)
        refs.append({"image_data": uri.split(",", 1)[-1]})
    elif body.get("image_url"):
        if refs:
            return ({"error": {"message": "图片参考不能与音频/音色参考混用（上游限制）",
                               "type": "invalid_request_error"}}, 400)
        refs.append({"image_url": str(body["image_url"])})

    fmt = (body.get("response_format") or body.get("format") or "mp3").lower()
    if fmt not in ("wav", "mp3", "pcm", "ogg_opus", "ogg"):
        fmt = "mp3"
    ac = {"format": "ogg_opus" if fmt == "ogg" else fmt,
          "sample_rate": int(body.get("sample_rate") or 24000)}
    for src, dst in (("speed_rate", "speech_rate"), ("speech_rate", "speech_rate"),
                     ("loudness_rate", "loudness_rate"), ("pitch_rate", "pitch_rate")):
        v = body.get(src)
        if v not in (None, ""):
            ac[dst] = int(v)
    if body.get("subtitle") or body.get("enable_subtitle"):
        ac["enable_subtitle"] = True

    payload = {"model": str(body.get("model") or AUDIO_GEN_MODEL),
               "text_prompt": text, "audio_config": ac}
    if refs:
        payload["references"] = refs

    import uuid
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(AUDIO_GEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", SPEECH_PLATFORM_KEY)
    req.add_header("X-Api-Request-Id", str(uuid.uuid4()))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:400]
        except Exception:                                    # noqa: BLE001
            pass
        log("音频生成上游 %s → %s" % (exc.code, detail or exc.reason))
        return ({"error": {"message": "上游 %s：%s" % (exc.code, detail or exc.reason),
                           "type": "upstream_error"}}, 502)
    except Exception as exc:                                 # noqa: BLE001
        log("音频生成异常 → %s" % exc)
        return ({"error": {"message": str(exc)[:300], "type": "upstream_error"}}, 502)

    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:                                        # noqa: BLE001
        return ({"error": {"message": "上游返回非 JSON：%s"
                                      % raw[:200].decode("utf-8", "replace"),
                           "type": "upstream_error"}}, 502)

    code = obj.get("code")
    if code not in (0, None):
        return ({"error": {"message": str(obj.get("message") or "上游失败")[:300],
                           "code": code, "type": "upstream_error"}}, 502)

    b64 = obj.get("audio") or ""
    audio = b""
    if b64:
        import base64
        try:
            audio = base64.b64decode(b64)
        except Exception:                                    # noqa: BLE001
            audio = b""

    meta = {"duration": obj.get("duration"), "original_duration": obj.get("original_duration"),
            "url": obj.get("url"), "format": ac["format"],
            "sample_rate": ac["sample_rate"], "latency_ms": int((time.time() - t0) * 1000),
            "model": payload["model"], "channel": "platform",
            "has_audio": bool(audio), "bytes": len(audio)}
    if obj.get("subtitle"):
        meta["subtitle"] = obj["subtitle"]

    if body.get("meta") or body.get("return_meta"):
        if audio:
            import base64
            meta["audio_b64"] = base64.b64encode(audio).decode("ascii")
        return (meta, 200 if audio or meta.get("url") else 502)

    if not audio:
        return ({"error": {"message": "上游未返回音频（仅返回了 url）",
                           "url": obj.get("url"), "type": "upstream_error"}}, 502)

    mime = {"mp3": "audio/mpeg", "pcm": "audio/pcm", "ogg_opus": "audio/ogg",
            "ogg": "audio/ogg", "wav": "audio/wav"}.get(fmt, "audio/mpeg")
    log("音频生成成功 %s %s %.1fs %d bytes (dur=%s)"
        % (payload["model"], fmt, time.time() - t0, len(audio), obj.get("duration")))
    return (audio, mime)

# ---------------------------------------------------------------------------
# 上游注册表：按优先级排列（第一个 = 首选）
#   no_thinking —— 注入到请求体的"关闭思考模式"参数（None = 不注入）。
#     魔搭 Qwen3.5 / 方舟豆包默认开思考，一句话先烧 2000+ token（7~25s），
#     必须显式关闭（实测见 tools/test_modelscope_thinking.py / test_ark_full.py）。
#     客户端若已自带同名字段则以客户端为准，不覆盖。
# ---------------------------------------------------------------------------
UPSTREAMS = {
    "modelscope": {
        "base": "https://api-inference.modelscope.cn/v1",
        "key": MODELSCOPE_API_KEY,
        "models": [
            "Qwen/Qwen3.5-27B",          # 主力对话，关思考后 ~1s
            "deepseek-ai/DeepSeek-V3",
            "ZhipuAI/GLM-5.2",
        ],
        "no_thinking": {"enable_thinking": False},
    },
    "ark": {
        "base": "https://ark.cn-beijing.volces.com/api/v3",
        "key": ARK_API_KEY,
        # 2026-10-20 全量实测：本 Key 实际可用 17 个（旧注释「仅此一个开通」是错的）。
        # 方舟给每个模型 **独立的 50 万 tokens 免费推理额度**，因此多模型轮换
        # 等于把可用额度放大 N 倍 —— 这是解决「豆包额度不足」的核心手段。
        #
        # 顺序即回退顺序，按「剩余额度 × 实测中位延迟」排：快的在前，
        # 额度已见底的 lite 放最后只作兜底（实测剩 36,485/500,000）。
        # 全量 17 个 ≈ 850 万 tokens 独立额度；额度耗尽由 TRACKER 熔断跳过，
        # 所以链长不影响稳态延迟（熔断后不再撞死模型）。
        "models": [
            "deepseek-v4-1-flash-economy-261017",  # 510ms
            "doubao-seed-2-0-mini-260428",         # 734ms
            "doubao-seed-character-251128",        # 786ms  角色扮演
            "doubao-seed-character-260628",        # 875ms  角色扮演，桌宠人设首选
            "deepseek-v4-flash-ga-260731",         # 1035ms
            "doubao-seed-2-1-pro-260915",          # 1281ms 豆包主力档
            "doubao-seed-2-1-pro-260628",          # 1303ms
            "deepseek-v4-1-flash-260910",          # 1310ms
            "deepseek-v4-1-flash-261017",          # 1470ms 额度 499,995
            "deepseek-v4-pro-ga-260813",           # 1475ms 强模型 额度 499,912
            "doubao-seed-2-0-lite-260428",         # 1684ms
            "glm-5-3-flash-260828",                # 1888ms 额度 499,892
            "kimi-k3-1-pro-261024",                # 2177ms 1M 上下文，2026-11-05 实测新增
            "doubao-seed-evolving",                # 3107ms
            "doubao-seed-2-0-code-preview-260215", # 3215ms 代码向
            "doubao-seed-2-1-turbo-260628",        # 3451ms
            "glm-5-2-260617",                      # 3928ms
            "doubao-seed-2-1-lite-260915",         # 693ms  但仅剩 3.6 万 → 兜底
        ],
        "no_thinking": {"thinking": {"type": "disabled"}},
    },
    # Agent Plan 档（2026-10-19 实测接入）：套餐订阅 Key（ARK_PLAN_API_KEY →
    # VOLC_SPEECH_API_KEY），端点 /api/plan/v3。只放实测通过"关思考"参数的模型
    # （glm-5.3-flash 不认 thinking.type=disabled，且默认开思考会吃掉短回复，
    # 故不入链）。定位是备援加深：主链（魔搭/方舟）挂了才轮到它，探活池不含
    # 本档，避免 30 分钟一次的探活空烧套餐配额。
    "arkplan": {
        "base": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "key": PLAN_API_KEY,
        "models": [
            "deepseek-v4.1-flash",           # 实测 0.8s，备援里最快
            "deepseek-v4-flash",             # 实测 2.0s
            "doubao-seed-2.1-pro",           # 实测 1.3s
        ],
        "no_thinking": {"thinking": {"type": "disabled"}},
    },
    "openrouter": {
        "base": UPSTREAM_BASE,
        "key": API_KEY,
        "models": MODEL_POOL,
        "no_thinking": None,
    },
}
# 优先级（2026-10-23 修订）：
#   ark      标准通道，每模型独立 50 万 tokens，共 17 个 ≈ 850 万 → 主力
#   arkplan  套餐订阅通道，Plan Key 有效，配额独立 → 备援
#   openrouter 免费层仅 50 次/天，且池内多是代码/推理模型（人设不符）→ 兜底
#   modelscope 账户欠费，默认禁用
UPSTREAM_PRIORITY = ["ark", "arkplan", "openrouter", "modelscope"]
if not PLAN_API_KEY and "arkplan" in UPSTREAM_PRIORITY:
    # 无 Plan Key 时整档下线：否则该档 401 会白白增加一次失败往返
    UPSTREAM_PRIORITY.remove("arkplan")

# 禁用某几档上游：从候选链中剔除，但 MODEL_OWNER 映射仍保留（便于一键恢复）。
# 默认禁用魔搭 —— 2026-10-19 起该账户全池返回 429 insufficient balance，
# 继续留在链首只会给每次请求白白增加一次 ~170ms 的失败往返。
DISABLED_UPSTREAMS = {
    s.strip()
    for s in os.environ.get("OR_DISABLE_UPSTREAMS", "modelscope").split(",")
    if s.strip()
} & set(UPSTREAMS)


def _active_upstreams() -> list:
    """当前真正参与请求的上游，顺序沿用 UPSTREAM_PRIORITY。"""
    act = [n for n in UPSTREAM_PRIORITY if n not in DISABLED_UPSTREAMS]
    return act or list(UPSTREAM_PRIORITY)


ACTIVE_UPSTREAMS = _active_upstreams()

# model id → 所属上游名
#   遍历"全部"上游而非仅启用的：即使某档被禁用，客户端显式指定其模型时仍能
#   正确定位归属（真正剔除发生在 candidates_for），不会被误判成 openrouter
#   模型去挨 400。
MODEL_OWNER = {}
for _up in UPSTREAMS:
    for _mid in UPSTREAMS[_up]["models"]:
        MODEL_OWNER[_mid] = _up

# 非流式/流式字段白名单：国内上游（OpenAI 原生格式）不认识 OpenRouter 扩展字段
BASE_FIELDS = (
    "messages", "temperature", "top_p", "max_tokens", "max_completion_tokens",
    "stop", "stream", "tools", "tool_choice", "response_format",
    "frequency_penalty", "presence_penalty", "seed", "user", "logit_bias",
    # 2026-10-20 加入：流式默认不返回 usage，客户端需靠 stream_options
    # 索取。此前本字段被白名单过滤掉，导致流式请求永远拿不到 token 消耗。
    "stream_options",
    # 2026-11-05 加入：思考开关。此前被过滤掉，客户端显式传 thinking 等于白传，
    # 而 build_payload 的注入条件 `k not in body` 又因为 body 里确实有它而跳过，
    # 结果"想关关不掉、想开开不了"。带思维链的模型在 max_tokens=8 的短任务里
    # 会把预算全花在 reasoning_content 上（情绪判定因此 0% 命中，且 HTTP 200）。
    "thinking", "reasoning_effort",
)

# ---------------------------------------------------------------------------
# 低延迟自动路由（auto 模式）
#   仅当请求 model="auto" 时启用；桌宠对话用该模式，其余调用方不受影响。
#   路由依据 = 后台定时探活 + 真实请求的被动延迟采样（EMA 指数滑动平均）。
#   候选池以国内上游优先（魔搭/方舟都是 ~1s），OpenRouter 只做兜底；
#   探活间隔默认 30 分钟且会跳过近期有真实流量的模型，尽量不烧额度。
# ---------------------------------------------------------------------------
AUTO = "auto"

# auto 模式候选池：按"对话延迟"排序，选中延迟最低者
#   2026-10-19 调整：移除 Qwen/Qwen3.5-27B（魔搭整档已禁用）；方舟实测
#   consec_fail=0 是当前唯一可持续主力，放首位作为无 EMA 数据时的默认值。
#
#   额度提醒：OpenRouter 免费层只有 50 次/天，探活也会消耗它 —— 故每轮
#   最多探活 PROBE_OR_PER_ROUND 个 OpenRouter 候选（见 latency_prober）。
LATENCY_POOL = [
    # 方舟档：额度充足且实测最快的一批（每个模型独立 50 万 tokens 免费额度）
    "deepseek-v4-1-flash-economy-261017",  # 359ms  额度 499,891
    "doubao-seed-character-260628",        # 420ms  人设最贴桌宠
    "doubao-seed-2-0-mini-260428",         # 437ms
    "deepseek-v4-1-flash-261017",          # 1045ms 额度 499,995
    # OpenRouter 兜底（免费层仅 50 次/天，见 PROBE_OR_PER_ROUND）
    "nvidia/nemotron-3-super-120b-a12b:free",
    "liquid/lfm-2.5-2.6b:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-lightning:free",
]
# 注意：doubao-seed-2-1-lite-260915 已从本池移除。它额度仅剩 7.3%，若继续
# 参与 auto 择优会迅速归零；现只保留在 ark 档候选链末尾作最后兜底。
PROBE_INTERVAL = 1800      # 探活轮次间隔（秒）
PROBE_TIMEOUT = 12         # 单次探活超时（秒）
FRESH_WINDOW = 600         # 模型在 N 秒内有过真实成功请求则视为数据新鲜，跳过探活
PROBE_OR_PER_ROUND = 1     # 每轮最多探活几个 OpenRouter 候选（其免费层仅 50 次/天）
EMA_ALPHA = 0.35           # EMA 平滑系数：越大越看重最新一次延迟
MAX_CONSEC_FAIL = 2        # 连续失败 N 次的模型暂时移出可选
QUOTA_COOLDOWN = 1800      # 额度类失败后的熔断时长（秒）：期间不再被任何链选中

# 额度耗尽类错误特征。命中即熔断该模型 —— 这类失败是"确定性"的（当天额度
# 不会自己长回来），与瞬时故障不同：继续把它放在链首，每次请求都会白付一个
# 往返延迟去买同一个 429。
QUOTA_HINTS = (
    "insufficient", "quota", "balance", "overdue", "arrears",
    "notopen", "not activated", "per-day", "per day", "daily",
)


def quota_cooldown_secs(err) -> int:
    """额度/限流类失败应冷却多久；0 表示不是这类错误。

    分级很重要：日额度耗尽要按小时级屏蔽，而 RPM 限流只是瞬时拥塞，
    若一律按 30 分钟熔断，会把一个健康模型误杀半小时。
    """
    if not err:
        return 0
    low = str(err).lower()
    if any(h in low for h in QUOTA_HINTS):
        return QUOTA_COOLDOWN           # 1800s：当日额度/未开通
    if err.startswith("429") or " 429" in low:
        return 60                       # RPM 限流：短冷却
    return 0


class LatencyTracker:
    """每模型延迟 EMA 统计（线程安全）。"""

    def __init__(self, alpha: float = EMA_ALPHA):
        self._alpha = alpha
        self._lock = threading.Lock()
        self._ema = {}        # model -> ema 秒
        self._consec_fail = {}
        self._last_ok = {}    # model -> 上次成功时间戳
        self._samples = {}    # model -> 采样数
        self._quota_until = {}  # model -> 额度熔断截止时间戳

    def mark_quota(self, model: str, seconds: float | None = None) -> None:
        """标记模型额度耗尽，冷却期内不参与任何候选链。"""
        seconds = QUOTA_COOLDOWN if seconds is None else seconds
        with self._lock:
            self._quota_until[model] = time.time() + seconds

    def quota_blocked(self, model: str) -> bool:
        with self._lock:
            until = self._quota_until.get(model)
            if until is None:
                return False
            if time.time() >= until:
                self._quota_until.pop(model, None)   # 到期自动解除
                return False
            return True

    def quota_snapshot(self) -> dict:
        """剩余熔断秒数，供 /health 观察。"""
        now = time.time()
        with self._lock:
            return {m: int(u - now) for m, u in self._quota_until.items() if u > now}

    def record(self, model: str, latency: float, ok: bool) -> None:
        with self._lock:
            if not ok:
                self._consec_fail[model] = self._consec_fail.get(model, 0) + 1
                return
            self._consec_fail[model] = 0
            self._quota_until.pop(model, None)   # 能成功就说明额度没耗尽，自愈
            self._last_ok[model] = time.time()
            self._samples[model] = self._samples.get(model, 0) + 1
            if model not in self._ema:
                self._ema[model] = latency
            else:
                self._ema[model] = self._alpha * latency + (1 - self._alpha) * self._ema[model]

    def fresh(self, model: str) -> bool:
        """近期有过真实成功流量，探活可跳过。"""
        with self._lock:
            return model in self._last_ok and (time.time() - self._last_ok[model]) < FRESH_WINDOW

    def best(self, pool=None) -> str:
        """返回当前 EMA 最低且可用的模型；跳过额度熔断与连续失败者。"""
        pool = pool or LATENCY_POOL
        now = time.time()
        with self._lock:
            def blocked(m: str) -> bool:
                u = self._quota_until.get(m)
                return u is not None and now < u

            avail = [
                (self._ema[m], m) for m in pool
                if m in self._ema
                and self._consec_fail.get(m, 0) < MAX_CONSEC_FAIL
                and not blocked(m)
            ]
            if avail:
                return min(avail)[1]
            for m in pool:
                if self._consec_fail.get(m, 0) == 0 and not blocked(m):
                    return m
            for m in pool:                 # 全被熔断时仍返回一个，避免 None 崩下游
                if not blocked(m):
                    return m
            return pool[0]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                m: {
                    "ema_s": round(self._ema[m], 3),
                    "samples": self._samples.get(m, 0),
                    "consec_fail": self._consec_fail.get(m, 0),
                    "last_ok_ago_s": (int(time.time() - self._last_ok[m])
                                      if m in self._last_ok else None),
                }
                for m in self._ema
            }


TRACKER = LatencyTracker()


def probe_model(mid: str) -> bool:
    """一次最小探活请求（会消耗该模型 1 次免费额度）。"""
    up = MODEL_OWNER.get(mid, "openrouter")
    payload = {"model": mid,
               "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 8}
    no_think = UPSTREAMS[up].get("no_thinking")
    if no_think:
        payload.update(no_think)
    t0 = time.time()
    try:
        resp, err = try_upstream(payload, PROBE_TIMEOUT, False, up)
    except Exception:                                        # noqa: BLE001
        TRACKER.record(mid, 0, False)
        return False
    if err is not None:
        TRACKER.record(mid, 0, False)
        return False
    try:
        resp.read()
        resp.close()
    except Exception:                                        # noqa: BLE001
        pass
    TRACKER.record(mid, time.time() - t0, True)
    return True


def latency_prober(interval: int) -> None:
    """后台探活线程：定期刷新各候选模型延迟，供 auto 路由择优。"""
    pool = [m for m in LATENCY_POOL if MODEL_OWNER.get(m) not in DISABLED_UPSTREAMS]
    log("auto 路由探活线程已启动（间隔 %ds，池 %d 个候选，OR 每轮上限 %d）"
        % (interval, len(pool), PROBE_OR_PER_ROUND))
    or_cands = [m for m in pool if MODEL_OWNER.get(m, "openrouter") == "openrouter"]
    rr = 0                         # 轮转游标：多轮之间错开被探的 OpenRouter 模型
    while True:
        # OpenRouter 免费层只有 50 次/天，探活本身也算请求 —— 每轮仅放行
        # PROBE_OR_PER_ROUND 个 OR 候选（并逐轮轮转），额度优先留给真实对话。
        allow = set()
        if or_cands:
            n = min(PROBE_OR_PER_ROUND, len(or_cands))
            allow = {or_cands[(rr + i) % len(or_cands)] for i in range(n)}
        for mid in pool:
            if TRACKER.fresh(mid):
                continue          # 近期有真实流量，数据已新鲜，省一次额度
            if mid in or_cands and mid not in allow:
                continue
            ok = probe_model(mid)
            log("探活 %s → %s" % (mid.split("/")[-1], "OK" if ok else "FAIL"))
            time.sleep(2)         # 探活之间留间隔，避免并发踩限流
        rr = (rr + 1) % max(1, len(or_cands))
        time.sleep(interval)

# 注入给易发纯文本工具调用的模型的约束提示
TOOL_REPAIR_HINT = (
    "\n\n[工具调用格式约束] 当你需要调用工具时，必须输出结构化的 tool_calls "
    "（JSON 函数名 + 参数）。严禁把工具调用写成普通文本、代码块或伪 JSON。"
)

# 统计
STATS = {"requests": 0, "fallbacks": 0, "errors": 0, "images": 0,
         "embeddings": 0, "asr": 0, "tts": 0, "started": time.time()}
_stat_lock = threading.Lock()


class UsageLedger:
    """按模型累计 token 消耗（额度感知用）。

    方舟每个模型有独立的 50 万 tokens 免费额度，但**除控制台外没有任何查询
    接口**（/usage、/account、/balance 均 404）。要实时知道还剩多少，只能
    自己记账。流式响应默认不带 usage，靠注入 stream_options 才能拿到。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ledger = {}      # model -> {prompt, completion, total, calls}

    def add(self, model: str, usage) -> None:
        if not isinstance(usage, dict) or not model:
            return
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))
        if not t:
            return
        with self._lock:
            e = self._ledger.setdefault(
                model, {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
            e["prompt"] += p
            e["completion"] += c
            e["total"] += t
            e["calls"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {m: dict(v) for m, v in self._ledger.items()}


USAGE = UsageLedger()


def usage_from_sse(line) -> dict | None:
    """从 SSE 帧里抓 usage——只有流式末帧（带 include_usage 时）才有。"""
    try:
        s = line.decode("utf-8", "replace").strip() if isinstance(line, (bytes, bytearray)) else str(line).strip()
        if not s.startswith("data:"):
            return None
        body = s[5:].strip()
        if not body or body == "[DONE]":
            return None
        u = json.loads(body).get("usage")
        return u if isinstance(u, dict) and u.get("total_tokens") else None
    except Exception:                                        # noqa: BLE001
        return None


def log(msg: str) -> None:
    """带时间戳 stderr 日志（Windows 下避免 GBK 编码崩溃）。"""
    ts = time.strftime("%H:%M:%S")
    try:
        sys.stderr.write("[%s] %s\n" % (ts, msg))
        sys.stderr.flush()
    except UnicodeEncodeError:
        sys.stderr.write("[%s] %s\n" % (ts, msg.encode("ascii", "replace").decode()))
        sys.stderr.flush()


def _bump(key: str, n: int = 1) -> None:
    with _stat_lock:
        STATS[key] = STATS.get(key, 0) + n


# ---------------------------------------------------------------------------
# 上游调用
# ---------------------------------------------------------------------------

def upstream_headers(up_name: str = "openrouter", stream: bool = False) -> dict:
    up = UPSTREAMS[up_name]
    h = {
        "Authorization": "Bearer " + up["key"],
        "Content-Type": "application/json",
    }
    if up_name == "openrouter":
        # OpenRouter 推荐头，用于排行榜归属；其他上游不认识也不碍事，但不发更干净
        h["HTTP-Referer"] = "http://127.0.0.1"
        h["X-Title"] = "OR-Local-Proxy"
    if stream:
        h["Accept"] = "text/event-stream"
        h["Cache-Control"] = "no-cache"
    return h


def fetch_free_models(timeout: int = 30) -> list:
    """拉取当前账号真实可调用的免费模型（:free 后缀）。"""
    req = urllib.request.Request(
        UPSTREAM_BASE + "/models", headers=upstream_headers()
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))["data"]
    return [m for m in data if m["id"].endswith(":free")]


def list_models_openai_format() -> dict:
    """以 OpenAI /v1/models 兼容格式返回（合并所有上游可用模型）。"""
    data = []
    # 国内上游：静态注册表（方舟模型清单是固定的，不必每次拉取）。
    # 只列当前启用档，避免客户端看到已被禁用的模型。
    for name in ACTIVE_UPSTREAMS:
        if name == "openrouter":
            continue
        for mid in UPSTREAMS[name]["models"]:
            data.append({
                "id": mid,
                "object": "model",
                "created": int(time.time()),
                "owned_by": name,
                "context_length": 0,
            })
    # OpenRouter：动态拉取 :free 清单，失败则回退内置池
    try:
        free = fetch_free_models()
    except Exception as e:                                   # noqa: BLE001
        log("拉取 OpenRouter 模型清单失败(%s)，回退到内置池" % e)
        free = [{"id": m, "context_length": 0} for m in MODEL_POOL]
    for m in free:
        data.append({
            "id": m["id"],
            "object": "model",
            "created": int(time.time()),
            "owned_by": "openrouter",
            "context_length": m.get("context_length", 0),
        })
    return {
        "object": "list",
        "data": sorted(data, key=lambda x: x["id"]),
    }


def resolve_model(name: str, default: str) -> str:
    """解析模型名：auto → 走低延迟路由；别名 → 全名；未知则回退到默认模型。"""
    if name == AUTO:
        return AUTO
    if not name:
        return default
    if name in ALIASES:
        return ALIASES[name]
    if (name in MODEL_POOL or name in MODEL_OWNER or name.endswith(":free")):
        return name
    return default


def build_payload(body: dict, model: str, repair_tools: bool, up_name: str = "openrouter") -> dict:
    """构造转发给上游的 payload：按上游选白名单 + 思考模式注入 + 工具修复。"""
    if up_name == "openrouter":
        keep = BASE_FIELDS + (
            "reasoning", "top_k", "repetition_penalty",   # OpenRouter 扩展字段
        )
    else:
        keep = BASE_FIELDS
    payload = {k: v for k, v in body.items() if k in keep}
    payload["model"] = model

    # 流式默认不带 usage（实测方舟/OpenRouter 均如此），客户端只有传
    # stream_options.include_usage 才能拿到。代理统一补上，客户端无感；
    # 客户端自己传了则以客户端为准。
    if payload.get("stream") and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}

    # 思考模式强制关闭（客户端没显式给该字段时才注入）
    no_think = UPSTREAMS[up_name].get("no_thinking")
    if no_think:
        payload.update({k: v for k, v in no_think.items() if k not in body})

    # 工具修复：对已知的"纯文本工具调用"倾向模型注入额外约束
    if repair_tools and payload.get("tools"):
        msgs = list(payload.get("messages") or [])
        if msgs and msgs[0].get("role") == "system":
            msgs = [dict(msgs[0]), *msgs[1:]]
            msgs[0]["content"] = (msgs[0].get("content") or "") + TOOL_REPAIR_HINT
        else:
            msgs.insert(0, {"role": "system", "content": "You are a helpful assistant." + TOOL_REPAIR_HINT})
        payload["messages"] = msgs
    return payload


def _open(url: str, payload: dict, timeout: int, stream: bool, up_name: str = "openrouter"):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers=upstream_headers(up_name, stream=stream), method="POST"
    )
    return urllib.request.urlopen(req, timeout=timeout)


def try_upstream(payload: dict, timeout: int, stream: bool, up_name: str = "openrouter"):
    """向指定上游的 /chat/completions 发起一次请求。返回 (resp, err)。"""
    base = UPSTREAMS[up_name]["base"]
    try:
        return _open(base + "/chat/completions", payload, timeout, stream, up_name), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # noqa: BLE001
            pass
        return None, "%d %s %s" % (e.code, e.reason, detail)
    except Exception as e:                                   # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


def candidates_for(primary: str, body: dict) -> list:
    """构造候选链 [(upstream, model), ...]：主模型优先，随后跨上游降级。

    auto 模式：主模型为实时选出的最低延迟者，后接延迟池与其余模型，保证
    首选失败（如免费额度当日耗尽）时快速回退。

    全局过滤：任何属于 DISABLED_UPSTREAMS 的候选一律剔除——包括客户端显式
    指定了该档模型的情况，否则禁用只生效一半。
    """

    def keep(u: str, m: str = "") -> bool:
        """候选是否可用：上游未禁用，且模型不在额度熔断期。"""
        return u not in DISABLED_UPSTREAMS and not (m and TRACKER.quota_blocked(m))

    def fallback_chain() -> list:
        n = ACTIVE_UPSTREAMS[0]
        return [(n, UPSTREAMS[n]["models"][0])]

    if primary == AUTO:
        best = TRACKER.best()
        log("auto 路由 → %s（当前最低延迟）" % best)
        order = [best]
        order += [m for m in LATENCY_POOL if m not in order]
        order += [m for m in MODEL_POOL if m not in order]
        chain, seen = [], set()
        for m in order:
            up = MODEL_OWNER.get(m, "openrouter")
            if m not in seen and keep(up, m):
                chain.append((up, m))
                seen.add(m)
        return chain or fallback_chain()

    up = MODEL_OWNER.get(primary, "openrouter")
    chain = [(up, primary)]
    # 带 tools 的请求把 poolside（tool-call 更可靠）提到次位
    if body.get("tools") and "poolside/laguna-s-2.1:free" != primary:
        chain.append(("openrouter", "poolside/laguna-s-2.1:free"))
    for name in ACTIVE_UPSTREAMS:
        for m in UPSTREAMS[name]["models"]:
            if (name, m) not in chain:
                chain.append((name, m))
    # OpenRouter 全池兜底
    for m in MODEL_POOL:
        if ("openrouter", m) not in chain:
            chain.append(("openrouter", m))
    chain = [(u, m) for (u, m) in chain if keep(u, m)]
    return chain or fallback_chain()


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------

class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ORProxy/1.0"

    # 静默标准访问日志，改用自己的 log()
    def log_message(self, fmt, *args):  # noqa: D102
        pass

    # 探活客户端（桌面壳/前端）经常在响应写完前就断开（WinError 10054/10053）。
    # 这是常态而非故障，静默关闭即可，避免每个请求刷一段异常栈。
    _CLIENT_GONE = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def handle(self) -> None:
        try:
            super().handle()
        except self._CLIENT_GONE:
            pass
        except TimeoutError:
            log("客户端读超时，关闭连接")

    def finish(self) -> None:
        try:
            super().finish()
        except self._CLIENT_GONE:
            pass

    # ---- 工具方法 -------------------------------------------------------
    def _json(self, obj: dict, code: int = 200, extra=None):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _raw(self, data: bytes, mime: str, code: int = 200, extra=None):
        """发送二进制响应（TTS 音频用）。"""
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            log("请求无 Content-Length，body 视为空")
            return {}
        try:
            raw = self.rfile.read(n)
        except Exception as e:                               # noqa: BLE001
            log("读取 body 失败: %s" % e)
            return {}
        text = None
        for enc in ("utf-8", "gb18030", "latin-1"):
            # Windows cmd/Git Bash 的 curl 常按系统代码页(GBK)发中文，
            # 仅按 utf-8 解码会抛异常并被吞掉，导致 messages 整个丢失。
            try:
                text = raw.decode(enc)
                if enc != "utf-8":
                    log("body 实际编码为 %s，已正确解码" % enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", "replace")
            log("body 编码无法识别，已做替换解码")
        try:
            return json.loads(text)
        except Exception as e:                               # noqa: BLE001
            log("JSON 解析失败: %s | 原文前200字节: %r" % (e, text[:200]))
            return {}

    def _check_auth(self) -> bool:
        if not LOCAL_KEY_PASSTHROUGH:
            return True
        auth = self.headers.get("Authorization", "")
        # 本地代理放行任意 key；若需要鉴权请把 LOCAL_KEY_PASSTHROUGH 设为 False
        return auth.startswith("Bearer ")

    # ---- 路由 -----------------------------------------------------------
    def do_GET(self):                                        # noqa: N802
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path in ("/v1/models", "/models"):
            self._json(list_models_openai_format())
        elif path in ("/v1/registry", "/registry"):
            # 纯本地注册表快照（不访问外网）。给 xiaoyun 设置页 / 托盘菜单判断
            # "哪些模型现在真的可选"用：/v1/models 会真去 OpenRouter 拉 :free
            # 清单（超时 30s），外网一抖动就把健康探测拖死，不能当探活用。
            self._json({
                "object": "registry",
                "active_upstreams": ACTIVE_UPSTREAMS,
                "disabled_upstreams": sorted(DISABLED_UPSTREAMS),
                "models": {n: list(UPSTREAMS[n]["models"]) for n in UPSTREAMS},
                "default_model": DEFAULT_MODEL,
                # 生图模型（Seedream）：走 /v1/images/generations，不与对话模型混在
                # /v1/models 里，避免聊天客户端把生图模型当文本模型试调。
                "image_models": sorted(set(IMAGE_MODELS.values())),
                "default_image_model": DEFAULT_IMAGE_MODEL,
                "image_backends": {
                    "plan": {"key": "plan", "models": ["doubao-seedream-5.0-pro",
                                                       "doubao-seedream-5-0-flash"],
                             "note": "8~15s/张，占 Agent Plan 额度"},
                    "ark": {"key": "ark", "models": sorted(ARK_IMAGE_MODELS),
                            "note": "4~8s/张，占普通 Key 额度；参考图字段 image/images"},
                },
                # 向量：只有多模态这一个可用；三款纯文本向量控制台显示已开通，
                # 但 API 侧 48 个候选 ID 全 404，本账号调不到
                "embedding_models": [EMBED_MODEL],
                "default_embedding_model": EMBED_MODEL,
                "embedding_dims": list(EMBED_DIMS),
                "default_embedding_dim": EMBED_DEFAULT_DIMS,
                # 语音：ASR 优先 WS 流式（约 1.1s），回落 chat+input_audio（约 2.3s）；
                #       TTS 双通道 —— 语音平台 Key（官方端点，不吃 Plan 额度）优先，
                #       否则回落 Plan Key；两把 Key 的凭证体系互不相通（实测矩阵见
                #       _speech_platform_key 注释，可用 tools/tts_auth_probe.py 复验）
                "asr_models": [ASR_MODEL],
                "default_asr_model": ASR_MODEL,
                "asr_backends": {
                    "ws": {"key": "plan", "model": "seed-asr-2.0",
                           "resource_id": ASR_WS_RESOURCE, "endpoint": ASR_WS_URL,
                           "note": "仅 wav/16bit/单声道；约 1.1s"},
                    "chat": {"key": "ark", "model": ASR_MODEL,
                             "note": "wav/mp3 均可；约 2.1~2.5s"},
                },
                "tts_models": [TTS_MODEL],
                "tts_voices": TTS_VOICES,
                "default_tts_voice": TTS_DEFAULT_VOICE,
                "tts_channel": ("platform" if SPEECH_PLATFORM_KEY else
                                ("plan" if PLAN_API_KEY else "none")),
                "tts_urls": {"platform": TTS_URL_PLATFORM, "plan": TTS_URL},
                "tts_platform_key_set": bool(SPEECH_PLATFORM_KEY),
                # 音频生成：seed-audio-1.0 —— 只在语音平台 Key 下可用；
                # 参考音频克隆音色 / 参考图片 / 纯文本音效，单次 ≤120s
                "audio_gen_models": [AUDIO_GEN_MODEL],
                "audio_gen_note": "需要 VOLC_SPEECH_PLATFORM_KEY；"
                                  "方舟普通/Plan Key 在 /api/v3/tts/create 一律 401",
                # 视觉：原生多模态对话模型（看图/看视频），接口同 chat/completions
                "vision_models": VISION_MODELS,
                # 生成类媒体：**两把 Key 的权限是逐模型的，不是按产品线**
                #   生图 → Plan Key（普通 Key 全 ModelNotOpen）
                #   视频 → 只有 1.0-pro-fast 走普通 Key（2.x 全未开通；plan 一律 UnsupportedModel）
                #   3D   → 普通 Key（plan 不支持）
                "media_models": {
                    "image": {"key": "plan / ark",
                              "models": sorted(set(IMAGE_MODELS.values())),
                              "endpoint": "/v1/images/generations"},
                    "video": {"key": "ark", "models": ["doubao-seedance-1-0-pro-fast-251015"],
                              "endpoint": "/api/v3/contents/generations/tasks",
                              "note": "任务式，返回 id 后需轮询；2.x 全部未开通"},
                    "3d": {"key": "ark",
                           "models": ["doubao-seed3d-2-0-260328",
                                      "hyper3d-gen2-260112",
                                      "hitem3d-2-0-251223"],
                           "endpoint": "/api/v3/contents/generations/tasks",
                           "note": "任务式，image_to_3D（纯文本提交会报 InvalidParameter）；"
                                   "seed3d 单张 >4min"},
                },
                "endpoints": {
                    "chat": "/v1/chat/completions",
                    "vision": "/v1/chat/completions",
                    "image": "/v1/images/generations",
                    "embedding": "/v1/embeddings",
                    "asr": "/v1/audio/transcriptions",
                    "tts": "/v1/audio/speech",
                    "audio_generate": "/v1/audio/generate",
                },
                # 豆包语音端点全量探测结果（2026-11-05，26 个端点 × 3 种 Key 变体
                # × 14 个 resource，详见 docs/豆包语音端点全量探测报告.md）
                # 核心事实：Plan 网关只镜像了 5 个端点，账号只授权了 3 个 resource
                "voice_capabilities": {
                    "usable_now": {
                        "tts_http": "/api/v3/plan/tts/unidirectional",
                        "tts_bidirectional_ws": "/api/v3/plan/tts/bidirection",
                        "asr_unidirectional_ws": "/api/v3/plan/sauc/bigmodel_nostream",
                        "asr_bidirectional_ws": "/api/v3/plan/sauc/bigmodel_async",
                    },
                    "hooked_but_unused": ["/api/v3/plan/tts/unidirectional/stream"],
                    "plan_resources_granted": ["seed-tts-2.0",
                                               "volc.seedasr.sauc.duration",
                                               "volc.seedasr.auc"],
                    "needs_platform_key": [
                        "音频生成 /api/v3/tts/create",
                        "声音复刻 /api/v3/tts/voice_clone",
                        "音色设计 /api/v3/tts/voice_design",
                        "音色查询 /api/v3/tts/get_voice",
                        "音色升级 /api/v3/tts/upgrade_voice",
                        "录音文件识别 /api/v3/auc/bigmodel/submit",
                        "会议纪要 /api/v3/auc/lark/submit",
                        "语音播客 /api/v3/podcast/sse",
                        "同声传译 /api/v4/ast/v2/translate",
                        "机器翻译 /api/v3/machine_translation/matx_translate",
                        "端到端 Seeduplex /api/v3/duplex/realtime/dialogue",
                    ],
                    "known_broken": {
                        "/api/v3/plan/auc/bigmodel/submit":
                            "resource 已授权但网关报 UnsupportedHarnessKey（缺 vendor 配置），"
                            "客户端无解，别照文档再试",
                        "/api/v3/plan/sauc/bigmodel": "网关无此路径（v3 新文档路径不存在）",
                    },
                },
            })
        elif path in ("/health", ""):
            up = get_stats()
            up["status"] = "ok"
            up["auto_routing"] = {
                "enabled": True,
                "best_now": TRACKER.best(),
                "latency": TRACKER.snapshot(),
                "pool": LATENCY_POOL,
            }
            self._json(up)
        else:
            self._json({"error": {"message": "not found: " + path}}, 404)

    def do_POST(self):                                       # noqa: N802
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        # 向量：/v1/embeddings 与 /v1/embeddings/multimodal 都归到方舟多模态端点
        if path in ("/v1/embeddings", "/embeddings", "/v1/embeddings/multimodal"):
            if not self._check_auth():
                self._json({"error": {"message": "missing bearer token"}}, 401)
                return
            body = self._read_body()
            _bump("embeddings")
            obj, code = ark_embed(body)
            self._json(obj, code)
            return
        # 语音识别（OpenAI 兼容路径）
        if path in ("/v1/audio/transcriptions", "/audio/transcriptions"):
            if not self._check_auth():
                self._json({"error": {"message": "missing bearer token"}}, 401)
                return
            body = self._read_body()
            _bump("asr")
            obj, code = ark_transcribe(body)
            self._json(obj, code)
            return
        # 语音合成（OpenAI 兼容路径，返回音频字节流）
        if path in ("/v1/audio/speech", "/audio/speech"):
            if not self._check_auth():
                self._json({"error": {"message": "missing bearer token"}}, 401)
                return
            body = self._read_body()
            _bump("tts")
            out, mime = ark_speech(body)
            if isinstance(out, bytes):
                self._raw(out, mime, 200)
            else:
                code = mime if isinstance(mime, int) else 502
                self._json(out, code)
            return
        # 音频生成（seed-audio-1.0：参考音频克隆 / 参考图片 / 音效创作，≤120s）
        if path in ("/v1/audio/generate", "/audio/generate"):
            if not self._check_auth():
                self._json({"error": {"message": "missing bearer token"}}, 401)
                return
            body = self._read_body()
            _bump("tts")
            out, mime = ark_audio_generate(body)
            if isinstance(out, bytes):
                self._raw(out, mime, 200)
            else:
                code = mime if isinstance(mime, int) else 502
                self._json(out, code)
            return
        # 生图：Agent Plan Seedream（不参与对话回退链，单独直转）
        if path in ("/v1/images/generations", "/images/generations"):
            if not self._check_auth():
                self._json({"error": {"message": "missing bearer token"}}, 401)
                return
            body = self._read_body()
            _bump("images")
            obj, code = generate_image(body)
            self._json(obj, code)
            return
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._json({"error": {"message": "not found: " + path}}, 404)
            return
        if not self._check_auth():
            self._json({"error": {"message": "missing bearer token"}}, 401)
            return

        body = self._read_body()
        _bump("requests")
        n_media = _inline_local_media(body)
        if n_media:
            log("已内联 %d 个本地媒体（路径 → data URI）" % n_media)
        stream = bool(body.get("stream"))
        primary = resolve_model(body.get("model"), self.server.default_model)
        repair = self.server.repair_tools
        chain = candidates_for(primary, body)

        attempts = []
        bad_up = set()          # 本轮已认证失败的上游，跳过其剩余候选
        t0 = time.time()
        for idx, (up_name, model) in enumerate(chain):
            if up_name in bad_up:
                continue
            payload = build_payload(body, model, repair, up_name)
            timeout = HTTP_TIMEOUT if idx == 0 else FALLBACK_TIMEOUT
            resp, err = try_upstream(payload, timeout, stream, up_name)

            if err is None and resp is not None:
                if idx > 0:
                    _bump("fallbacks")
                    log("回退生效 → [%s] %s（前序失败: %s）"
                        % (up_name, model, "; ".join(attempts[-1:])))
                self._proxy_response(resp, model, stream, t0, up_name)
                return

            TRACKER.record(model, 0, False)
            cd = quota_cooldown_secs(err)
            if cd:
                TRACKER.mark_quota(model, cd)
                log("额度/限流熔断 [%s] %s → %d 秒内跳过，换下一个模型"
                    % (up_name, model, cd))
            attempts.append("[%s]%s: %s" % (up_name, model.split("/")[-1], (err or "")[:120]))
            log("上游失败 [%s] %s → %s" % (up_name, model, (err or "")[:160]))
            if _upstream_auth_failed(err):
                # 同档同 Key，换模型必然同样 401：本轮跳过该档，但**不终止整条链**
                bad_up.add(up_name)
                log("认证失败 [%s] → 本次请求跳过该档其余 %d 个候选，继续尝试其它上游"
                    % (up_name, sum(1 for u, _ in chain if u == up_name)))
            if _should_stop(err):
                break

        _bump("errors")
        self._json(
            {
                "error": {
                    "message": "所有候选模型均失败",
                    "type": "upstream_error",
                    "attempts": attempts,
                    "hint": "免费额度通常为 50 次/天，超限或上游限流均会失败",
                }
            },
            502,
        )

    # ---- 响应转发 -------------------------------------------------------
    def _normalize_content(self, raw: bytes):
        """保证 OpenAI 兼容性。

        部分推理模型（实测 liquid/lfm-2.5-2.6b 最典型）把正文写在 reasoning
        字段，choices[0].message.content 为 null，多数 OpenAI SDK 会直接报错。
        开启时把 reasoning 回填进 content，并置 X-Reasoning-Fallback 响应头。
        """
        if not getattr(self.server, "reasoning_fallback", False):
            return raw, False
        try:
            d = json.loads(raw.decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            return raw, False
        patched = False
        for c in d.get("choices") or []:
            msg = c.get("message") or {}
            if msg.get("content") is None and msg.get("reasoning"):
                msg["content"] = msg["reasoning"]
                patched = True
        if patched:
            return json.dumps(d, ensure_ascii=False).encode("utf-8"), True
        return raw, False

    def _patch_stream_frame(self, line: bytes) -> bytes:
        """流式推理模型的 delta 同样把正文放在 reasoning，回填以保证客户端可读出内容。"""
        if not getattr(self.server, "reasoning_fallback", False):
            return line
        if not line.startswith(b"data:"):
            return line
        body = line[5:].strip()
        if body in (b"[DONE]", b""):
            return line
        try:
            d = json.loads(body.decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            return line
        changed = False
        for c in d.get("choices") or []:
            delta = c.get("delta") or {}
            if delta.get("content") is None and delta.get("reasoning"):
                delta["content"] = delta["reasoning"]
                changed = True
        if changed:
            return b"data: " + json.dumps(d, ensure_ascii=False).encode("utf-8") + b"\n\n"
        return line

    def _proxy_response(self, resp, model: str, stream: bool, t0: float = 0.0,
                        up_name: str = "openrouter") -> None:
        try:
            if stream:
                self.send_response(resp.status)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("X-Upstream-Model", model)
                self.send_header("X-Upstream", up_name)
                self.send_header("X-Proxy", "or-local-proxy")
                self.end_headers()
                # 逐帧透传 SSE：上游已解除 chunked，原样写入即为标准 SSE 帧
                first = True
                for line in resp:
                    if line:
                        if first:
                            # 对话体感延迟 = 首字节到达时间（TTFB），据此计入统计
                            TRACKER.record(model, time.time() - t0, True)
                            first = False
                        line = self._patch_stream_frame(line)
                        _u = usage_from_sse(line)
                        if _u:
                            USAGE.add(model, _u)
                        self.wfile.write(b"%X\r\n" % len(line) + line + b"\r\n")
                        self.wfile.flush()
                if first:
                    TRACKER.record(model, time.time() - t0, True)
                self.wfile.write(b"0\r\n\r\n")          # chunked 终止块
                self.wfile.flush()
            else:
                raw, patched = self._normalize_content(resp.read())
                try:
                    USAGE.add(model, json.loads(raw.decode("utf-8")).get("usage"))
                except Exception:                            # noqa: BLE001
                    pass
                TRACKER.record(model, time.time() - t0, True)
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Upstream-Model", model)
                self.send_header("X-Upstream", up_name)
                self.send_header("X-Proxy", "or-local-proxy")
                if patched:
                    self.send_header("X-Reasoning-Fallback", "1")
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                resp.close()
            except Exception:                                # noqa: BLE001
                pass


def _should_stop(err, up_name: str = "") -> bool:
    """是否终止整条回退链。

    2026-10-23 事故教训：原先"401 就 break"过于粗暴。方舟标准通道 Key 失效时
    （Agent Plan Key 被误用于 /api/v3），整条链被立刻掐断，连 OpenRouter 兜底
    都没机会尝试，桌宠直接 502 无响应。
    正确语义：**某一档 401 → 只跳过该档其余模型**（同 Key 同端点，
    换模型也是 401），但必须继续尝试其它上游。

    只有「全局性」故障（账户欠费、余额不足到影响全部端点）才整体终止。
    """
    if not err:
        return False
    low = str(err).lower()
    if "accountoverdue" in low or "arrears" in low:
        return True
    return False


def _upstream_auth_failed(err) -> bool:
    """该档是否为认证失败（同档其余模型不必再试）。"""
    if not err:
        return False
    low = str(err).lower()
    return (err.startswith("401") or "authenticationerror" in low
            or "invalidapikey" in low or "api key" in low and "invalid" in low)
    return False


# 实际生效的默认模型：main() 里按 --model / auto 决定，供 /health 展示
DEFAULT_MODEL = AUTO


def get_stats() -> dict:
    with _stat_lock:
        return {
            "requests": STATS["requests"],
            "fallbacks": STATS["fallbacks"],
            "errors": STATS["errors"],
            "uptime_sec": int(time.time() - STATS["started"]),
            "default_model": DEFAULT_MODEL,
            "upstream_priority": ACTIVE_UPSTREAMS,
            "disabled_upstreams": sorted(DISABLED_UPSTREAMS),
            "quota_cooldown": TRACKER.quota_snapshot(),
            "token_usage": USAGE.snapshot(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_list(detail: bool) -> None:
    try:
        free = fetch_free_models()
    except Exception as e:                                   # noqa: BLE001
        log("拉取失败: %s" % e)
        return
    print("\n当前账号可用免费模型：共 %d 个\n" % len(free))
    if detail:
        print("%-52s %-10s %-6s %s" % ("模型 ID", "上下文", "工具", "输入模态"))
        print("-" * 104)
        for m in sorted(free, key=lambda x: x["id"]):
            a = m.get("architecture", {})
            sp = m.get("supported_parameters", [])
            tool = "YES" if "tools" in sp else "-"
            mods = ",".join(a.get("input_modalities") or [])
            print("%-52s %-10s %-6s %s" % (m["id"], str(m.get("context_length", "?")), tool, mods))
    else:
        for m in sorted(free, key=lambda x: x["id"]):
            print("  " + m["id"])
    print()


def cmd_probe() -> None:
    """逐个探活所有上游的模型，标记实际可用性（免费层常有 429/超时）。"""
    all_models = [(name, mid) for name in ACTIVE_UPSTREAMS
                  for mid in UPSTREAMS[name]["models"]]
    print("\n探活中（每个模型一次最小请求，可能较慢）...\n")
    print("%-14s %-40s %-8s %-8s %s" % ("上游", "模型 ID", "状态", "耗时", "说明"))
    print("-" * 110)
    for name, mid in all_models:
        payload = {"model": mid, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16}
        no_think = UPSTREAMS[name].get("no_thinking")
        if no_think:
            payload.update(no_think)
        t0 = time.time()
        try:
            resp, err = try_upstream(payload, 40, False, name)
            if err is None:
                raw = json.loads(resp.read().decode("utf-8"))
                txt = (raw.get("choices", [{}])[0].get("message", {}).get("content") or "")[:24]
                print("%-14s %-40s %-8s %-8s %s" % (name, mid, "OK", "%.1fs" % (time.time() - t0), txt.replace("\n", " ")))
            else:
                print("%-14s %-40s %-8s %-8s %s" % (name, mid, "FAIL", "%.1fs" % (time.time() - t0), err[:40]))
        except Exception as e:                               # noqa: BLE001
            print("%-14s %-40s %-8s %-8s %s" % (name, mid, "FAIL", "-", str(e)[:40]))
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="多上游免费模型 → OpenAI 兼容本地代理")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 %d）" % DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    ap.add_argument("--model", default="auto",
                    help="默认模型（可用别名 auto/qwen/doubao/ultra/coding/fast 等，默认 auto=低延迟路由）")
    ap.add_argument("--list", action="store_true", help="只列出可用免费模型")
    ap.add_argument("--models", choices=["id", "full"], help="列出模型清单（id=简略 full=详情）")
    ap.add_argument("--probe", action="store_true", help="逐个探活模型实际可用性")
    ap.add_argument("--probe-interval", type=int, default=PROBE_INTERVAL,
                    help="auto 路由后台探活间隔秒数（默认 %d）" % PROBE_INTERVAL)
    ap.add_argument("--no-probe", action="store_true",
                    help="关闭 auto 路由后台探活（仅靠真实请求被动采样延迟）")
    ap.add_argument("--no-repair-tools", action="store_true", help="关闭工具调用提示注入")
    ap.add_argument("--no-reasoning-fallback", action="store_true",
                    help="关闭 reasoning 回填 content（默认开启以保证 OpenAI 兼容）")
    args = ap.parse_args()

    if args.models:
        cmd_list(args.models == "full")
        return
    if args.list:
        cmd_list(False)
        return
    if args.probe:
        cmd_probe()
        return

    # 客户端未指定 model 时默认走 auto（延迟择优），而不是死盯某个上游：
    # 上游额度是分模型的，择优 + 熔断才能把多模型额度池真正用起来。
    global DEFAULT_MODEL
    default_model = resolve_model(args.model, AUTO)
    DEFAULT_MODEL = default_model
    server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    server.daemon_threads = True
    server.default_model = default_model
    server.repair_tools = not args.no_repair_tools
    server.reasoning_fallback = not args.no_reasoning_fallback

    if not args.no_probe:
        threading.Thread(target=latency_prober, args=(args.probe_interval,),
                         daemon=True).start()

    print("=" * 68)
    print(" 多上游免费模型 → OpenAI 兼容代理已启动")
    print("=" * 68)
    print("  Base URL  : http://%s:%d/v1" % (args.host, args.port))
    print("  Models    : http://%s:%d/v1/models" % (args.host, args.port))
    print("  Health    : http://%s:%d/health" % (args.host, args.port))
    print("  上游优先级: %s" % " → ".join(ACTIVE_UPSTREAMS))
    if DISABLED_UPSTREAMS:
        print("  已禁用    : %s（设 OR_DISABLE_UPSTREAMS= 可恢复）"
              % ", ".join(sorted(DISABLED_UPSTREAMS)))
    print("  默认模型  : %s" % default_model)
    total_candidates = len(candidates_for(AUTO, {}))
    print("  回退链    : %d 个候选（跨上游）" % total_candidates)
    print("  auto 路由 : %s（model=\"auto\" 时选延迟最低模型；探活%s）"
          % ("开", ("间隔 %ds" % args.probe_interval) if not args.no_probe else "关闭"))
    print("  工具修复  : %s" % ("开" if server.repair_tools else "关"))
    print("  推理回填  : %s" % ("开" if server.reasoning_fallback else "关"))
    print("=" * 68)
    print("\n任何 OpenAI 兼容客户端把 base_url 指向上面的 Base URL 即可，")
    print("api_key 填任意值（如 sk-local）都会被放行。Ctrl+C 停止。\n")
    log("listening on %s:%d" % (args.host, args.port))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。统计：%s" % get_stats())


if __name__ == "__main__":
    main()
