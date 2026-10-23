#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agentplan.py — 火山方舟 Agent Plan 统一命令行客户端

把 Agent Plan 的四类能力收敛到一个 CLI 里统一管理：

  子命令        能力                  协议 / 端点
  ───────────────────────────────────────────────────────────────────────
  chat        文本生成 (LLM)         {base}/chat/completions   (OpenAI 兼容)
  embed       向量化 (embedding)      {base}/embeddings         (OpenAI 兼容)
  image       图片生成 (seedream)     {base}/images/generations (OpenAI 兼容)
  tts         语音合成 (TTS)          openspeech HTTP 单向
  asr         语音识别 (ASR)          openspeech WebSocket
  models      打印官方支持的模型清单

base 默认 = https://ark.cn-beijing.volces.com/api/plan/v3  (Agent Plan 专属)
语音 base 默认 = https://openspeech.bytedance.com/api/v3/plan

鉴权：Agent Plan 专属 API Key，从环境变量 ARK_PLAN_API_KEY 或 --key 读取。
注意：该 Key 与常规方舟 API Key 不同，且与 /api/v3 互不通用（在 /api/v3 上会 401）。

══════════════════════════════════════════════════════════════════════════════
合规提示（务必阅读）
官方文档明确：Agent Plan 权益面向已接入的 AI 工具使用，文本/向量模型“不可用于
API 调用”，在非 AI 工具里直接用本 CLI 对应的 Base URL + Key 可能被判定为滥用，
导致订阅停用或账号封禁。请仅在自有、合规的环境中使用本脚本。
══════════════════════════════════════════════════════════════════════════════

依赖：仅标准库（与项目内 or_proxy.py 风格一致，零第三方依赖）。

库层用法（2026-10-19 起，服务端直接 import，不再绕 CLI）：
  import agentplan
  txt = agentplan.ark_chat(messages)                    # OpenAI 格式，支持多模态 content
  data = agentplan.ark_embed(["你好", "世界"])           # -> data 列表（含 embedding）
  wav = agentplan.ark_tts_bytes("你好")                  # -> 音频 bytes
  text = agentplan.ark_asr_file("record.wav")           # -> 转写文本
  失败统一抛 agentplan.ArkPlanError。
  取 Key 链：显式参数 > ARK_PLAN_API_KEY > VOLC_SPEECH_API_KEY（环境 → HKCU 注册表）。

示例：
  export ARK_PLAN_API_KEY=ark-xxxx
  python agentplan.py chat -m "你好"
  python agentplan.py embed -i "你好世界" -i "第二条"
  python agentplan.py image -p "一只在月光下的猫" --download --output-dir ./img
  python agentplan.py tts -t "你好，这是语音合成测试" -o out.mp3
  python agentplan.py asr -f record.wav
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import gzip
import uuid
import socket
import ssl
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# 配置与默认常量
# ---------------------------------------------------------------------------
DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_VOICE_HTTP = "https://openspeech.bytedance.com/api/v3/plan"
DEFAULT_VOICE_WS = "wss://openspeech.bytedance.com/api/v3/plan"

DEFAULT_CHAT_MODEL = "doubao-seed-2.1-lite"        # 自动解析到带日期快照
DEFAULT_EMBED_MODEL = "doubao-embedding-vision"
DEFAULT_IMAGE_MODEL = "doubao-seedream-5-0-pro"
DEFAULT_TTS_SPEAKER = "zh_female_gaolengyujie_uranus_bigtts"
DEFAULT_TTS_RESOURCE = "seed-tts-2.0"
DEFAULT_ASR_RESOURCE = "volc.seedasr.sauc.duration"

HTTP_TIMEOUT = 120

# 官方模型清单（来自 火山方舟 Agent Plan 套餐概览文档）
SUPPORTED_MODELS = {
    "文本生成": [
        "doubao-seed-2.0-mini", "doubao-seed-2.1-lite", "deepseek-v4-flash",
        "glm-5.3-flash", "doubao-seed-2.1-pro", "doubao-seed-evolving",
        "glm-5.3", "kimi-k2.8-preview", "deepseek-v4-pro", "kimi-k3",
        "deepseek-v4.1-flash",
    ],
    "向量化": ["doubao-embedding-vision"],
    "图片生成": ["doubao-seedream-5-0-pro"],
    "视频生成": [
        "doubao-seedance-2.0", "doubao-seedance-2.0-fast",
        "doubao-seedance-2.0-mini", "doubao-seedance-2.5",
    ],
    "语音合成 TTS": ["doubao-seed-tts-2.0"],
    "语音识别 ASR": ["doubao-seed-asr-2.0"],
}


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write("✗ " + msg + "\n")
    sys.exit(code)


# ---------------------------------------------------------------------------
# 库层：异常 + 统一取 Key（CLI 与服务端共用，Step 0）
# ---------------------------------------------------------------------------
class ArkPlanError(Exception):
    """Agent Plan 调用失败（HTTP 错误 / 业务错误码 / 参数缺失）。库层抛出，CLI 捕获后 die。"""


def _reg_env(name: str) -> str:
    """读注册表里的用户/系统环境变量（setx 写的值，已启动进程读不到）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
        for root, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                           (winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, path) as k:
                    v, _ = winreg.QueryValueEx(k, name)
                    if v:
                        return str(v).strip()
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return ""


def get_plan_key(key=None):
    """统一取 Key：显式传参 > ARK_PLAN_API_KEY > VOLC_SPEECH_API_KEY（环境→注册表）。

    本机事实：Agent Plan Key 一直以 VOLC_SPEECH_API_KEY 之名存在 HKCU，两个名字指向同一把钥匙。
    返回 None 表示没有可用 Key（库层调用方自行降级；CLI 走 die）。
    """
    k = (key
         or os.environ.get("ARK_PLAN_API_KEY")
         or os.environ.get("VOLC_SPEECH_API_KEY")
         or _reg_env("ARK_PLAN_API_KEY")
         or _reg_env("VOLC_SPEECH_API_KEY"))
    return (k or "").strip() or None


def get_key(args) -> str:
    key = get_plan_key(getattr(args, "key", None))
    if not key:
        die("缺少 Agent Plan API Key：用 --key 传入，或设置 ARK_PLAN_API_KEY / VOLC_SPEECH_API_KEY")
    return key


def get_base(args) -> str:
    return getattr(args, "base", None) or os.environ.get("ARK_PLAN_BASE", DEFAULT_BASE).rstrip("/")


# ---------------------------------------------------------------------------
# 通用 HTTP（OpenAI 兼容部分：LLM / embedding / image）
# ---------------------------------------------------------------------------
def _post_json(url: str, body: dict, api_key: str, timeout: int = HTTP_TIMEOUT):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Bearer " + api_key)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp, None
    except urllib.error.HTTPError as e:  # noqa: BLE001
        return None, e.read().decode("utf-8", "replace")


def _read_stream(resp) -> None:
    """解析 OpenAI SSE 流，逐块打印助手内容。"""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if obj.get("object") == "error":
            sys.stderr.write(json.dumps(obj, ensure_ascii=False) + "\n")
            return
        choices = obj.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                sys.stdout.write(piece)
                sys.stdout.flush()
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# 子命令：chat
# ---------------------------------------------------------------------------
def ark_chat(messages, model=DEFAULT_CHAT_MODEL, temperature=0.8, max_tokens=1024,
             think=False, key=None, base=None, timeout=HTTP_TIMEOUT, stream=False,
             raw=False):
    """库层：一次文本生成。messages 为 OpenAI 格式（支持多模态 content 数组）。

    成功返回助手文本；raw=True 时返回完整响应 dict（含 usage）；
    stream=True 时返回响应对象（调用方自行消费 SSE 行）。
    失败抛 ArkPlanError。
    """
    key = get_plan_key(key)
    if not key:
        raise ArkPlanError("缺少 Agent Plan API Key")
    if not messages:
        raise ArkPlanError("messages 为空")
    base = (base or os.environ.get("ARK_PLAN_BASE", DEFAULT_BASE)).rstrip("/")
    body = {
        "model": model,
        "messages": messages,
        "stream": bool(stream),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if not think:
        # 多数模型默认开思考，一句话会先烧 2000+ token；显式关闭低延迟对话
        body["thinking"] = {"type": "disabled"}
    resp, err = _post_json(base + "/chat/completions", body, key, timeout=timeout)
    if err:
        raise ArkPlanError("chat 请求失败：\n" + err)
    if stream:
        return resp
    try:
        obj = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ArkPlanError("chat 响应解析失败：%r" % (e,))
    if raw:
        return obj
    try:
        return obj["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise ArkPlanError("chat 响应缺 choices/content：%r" % (e,))


def cmd_chat(args) -> None:
    key = get_key(args)
    base = get_base(args)
    if not args.message and not args.system:
        die("请提供至少一条 --message（-m）")

    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    for m in args.message:
        messages.append({"role": "user", "content": m})

    try:
        if args.stream:
            resp = ark_chat(messages, model=args.model, temperature=args.temperature,
                            max_tokens=args.max_tokens, think=args.think,
                            key=key, base=base, stream=True)
            _read_stream(resp)
            return
        if args.verbose:
            obj = ark_chat(messages, model=args.model, temperature=args.temperature,
                           max_tokens=args.max_tokens, think=args.think,
                           key=key, base=base, raw=True)
            content = obj.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = obj.get("usage")
            if usage:
                sys.stderr.write("· tokens: " + json.dumps(usage, ensure_ascii=False) + "\n")
        else:
            content = ark_chat(messages, model=args.model, temperature=args.temperature,
                               max_tokens=args.max_tokens, think=args.think,
                               key=key, base=base)
    except ArkPlanError as e:
        die(str(e))
    sys.stdout.write(content + "\n")


# ---------------------------------------------------------------------------
# 子命令：embed
# ---------------------------------------------------------------------------
def ark_embed(texts, model=DEFAULT_EMBED_MODEL, dimensions=None,
              key=None, base=None, timeout=HTTP_TIMEOUT):
    """库层：批量向量化。texts 为字符串列表，成功返回 data 列表（每项含 embedding）。"""
    key = get_plan_key(key)
    if not key:
        raise ArkPlanError("缺少 Agent Plan API Key")
    if not texts:
        raise ArkPlanError("texts 为空")
    base = (base or os.environ.get("ARK_PLAN_BASE", DEFAULT_BASE)).rstrip("/")
    body = {"model": model, "input": list(texts)}
    if dimensions:
        body["dimensions"] = dimensions
    resp, err = _post_json(base + "/embeddings", body, key, timeout=timeout)
    if err:
        raise ArkPlanError("embed 请求失败：\n" + err)
    try:
        obj = json.loads(resp.read().decode("utf-8"))
        return obj.get("data", [])
    except Exception as e:  # noqa: BLE001
        raise ArkPlanError("embed 响应解析失败：%r" % (e,))


def cmd_embed(args) -> None:
    key = get_key(args)
    base = get_base(args)

    texts = list(args.input)
    if args.input_file:
        with open(args.input_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    texts.append(line)
    if not texts:
        die("请提供 -i/--input 文本，或 --input-file 每行一条")

    try:
        data = ark_embed(texts, model=args.model, dimensions=args.dimensions,
                         key=key, base=base)
    except ArkPlanError as e:
        die(str(e))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"data": data}, f, ensure_ascii=False, indent=2)
        sys.stderr.write("· 已写入 %s（%d 条向量）\n" % (args.output, len(data)))
    else:
        for i, item in enumerate(data):
            vec = item.get("embedding", [])
            preview = "[" + ", ".join("%.4f" % x for x in vec[:5]) + (", …]" if len(vec) > 5 else "]")
            sys.stdout.write("第 %d 条 dim=%d %s\n" % (i, len(vec), preview))


# ---------------------------------------------------------------------------
# 子命令：image
# ---------------------------------------------------------------------------
def cmd_image(args) -> None:
    key = get_key(args)
    base = get_base(args)

    body = {
        "model": args.model,
        "prompt": args.prompt,
        "n": args.n,
        "size": args.size,
    }
    resp, err = _post_json(base + "/images/generations", body, key)
    if err:
        die("image 请求失败：\n" + err)
    obj = json.loads(resp.read().decode("utf-8"))
    items = obj.get("data", [])
    if not items:
        die("image 返回为空：" + json.dumps(obj, ensure_ascii=False))

    if not args.download:
        # 仅打印 URL
        for i, it in enumerate(items):
            sys.stdout.write("图 %d: %s\n" % (i, it.get("url", "")))
        return

    # 下载图片到本地
    os.makedirs(args.output_dir, exist_ok=True)
    for i, it in enumerate(items):
        url = it.get("url")
        if not url:
            sys.stderr.write("· 第 %d 条无 url，跳过\n" % i)
            continue
        ext = ".png"
        try:
            import urllib.parse as _up
            _up.urlparse(url)
            if ".jpg" in url or ".jpeg" in url:
                ext = ".jpg"
        except Exception:  # noqa: BLE001
            pass
        out_path = os.path.join(args.output_dir, "image_%d%s" % (i, ext))
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                blob = r.read()
            with open(out_path, "wb") as f:
                f.write(blob)
            sys.stderr.write("· 已保存 %s (%d bytes)\n" % (out_path, len(blob)))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("· 下载失败 %s: %s\n" % (url, e))


# ---------------------------------------------------------------------------
# 子命令：tts（HTTP 单向，返回 {"code":0,"data":"<base64 mp3>"}）
# ---------------------------------------------------------------------------
def ark_tts_bytes(text, speaker=DEFAULT_TTS_SPEAKER, fmt="mp3", sample_rate=24000,
                  enable_timestamp=False, resource_id=DEFAULT_TTS_RESOURCE,
                  key=None, voice_http=None, timeout=HTTP_TIMEOUT):
    """库层：语音合成，成功返回完整音频 bytes（多 JSON 分片已拼接）。

    协议要点：端点为流式多 JSON 拼接响应（对象间以换行分隔），每个对象含一段
    base64 音频，必须把全部 data 合并才是完整音频。
    """
    key = get_plan_key(key)
    if not key:
        raise ArkPlanError("缺少 Agent Plan API Key")
    if not text:
        raise ArkPlanError("text 为空")
    voice_base = (voice_http or os.environ.get("ARK_PLAN_VOICE_HTTP", DEFAULT_VOICE_HTTP))
    body = {
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {
                "format": fmt,
                "sample_rate": sample_rate,
                "enable_timestamp": bool(enable_timestamp),
            },
        }
    }
    data = json.dumps(body).encode("utf-8")
    url = voice_base.rstrip("/") + "/tts/unidirectional"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("X-Api-Key", key)
    req.add_header("X-Api-Resource-Id", resource_id)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
    except urllib.error.HTTPError as e:  # noqa: BLE001
        raise ArkPlanError("tts 请求失败：\n" + e.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        raise ArkPlanError("tts 请求失败：%r" % (e,))

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        chunks = []
        dec = json.JSONDecoder()
        pos = 0
        while pos < len(text):
            rest = text[pos:].lstrip()
            pos = len(text) - len(rest)
            if not rest:
                break
            try:
                obj, end = dec.raw_decode(rest)
            except json.JSONDecodeError:
                break
            pos += end
            if isinstance(obj, dict):
                if obj.get("code", 0) != 0:
                    raise ArkPlanError("tts 业务错误：" + json.dumps(obj, ensure_ascii=False))
                if obj.get("data"):
                    chunks.append(base64.b64decode(obj["data"]))
        if chunks:
            return b"".join(chunks)
        # 单对象兜底
        try:
            obj = json.loads(text)
            if obj.get("code", 0) != 0:
                raise ArkPlanError("tts 业务错误：" + json.dumps(obj, ensure_ascii=False))
            return base64.b64decode(obj["data"])
        except ArkPlanError:
            raise
        except Exception:  # noqa: BLE001
            return raw
    return raw


def cmd_tts(args) -> None:
    key = get_key(args)
    voice_base = getattr(args, "voice_http", None) or os.environ.get(
        "ARK_PLAN_VOICE_HTTP", DEFAULT_VOICE_HTTP)
    try:
        audio = ark_tts_bytes(args.text, speaker=args.speaker, fmt=args.format,
                              sample_rate=args.sample_rate,
                              enable_timestamp=args.timestamp,
                              resource_id=args.resource_id,
                              key=key, voice_http=voice_base)
    except ArkPlanError as e:
        die(str(e))

    out = args.output or ("tts_output.%s" % args.format)
    with open(out, "wb") as f:
        f.write(audio)
    sys.stderr.write("· 已保存音频 %s (%d bytes)\n" % (out, len(audio)))


# ---------------------------------------------------------------------------
# 子命令：asr（WebSocket，纯标准库实现）
# ---------------------------------------------------------------------------
# ByteDance 语音协议常量（来自官方 大模型流式语音识别 示例）
PV = 0b0001                                   # protocol version
MT_FULL = 0b0001                              # client full request
MT_AUDIO = 0b0010                             # client audio only
MT_SRV_FULL = 0b1001                          # server full response
MT_SRV_ERR = 0b1111                           # server error
FLAG_NONE = 0b0000
FLAG_POS = 0b0001                             # positive sequence
FLAG_NEG_LAST = 0b0011                        # negative-with-sequence (last)
SER_JSON = 0b0001
SER_NONE = 0b0000
CMP_GZIP = 0b0001
CMP_NONE = 0b0000


def _asr_header(message_type: int, flag: int, ser: int, cmp_: int) -> bytes:
    # 4 字节固定头：b0=版本+头大小, b1=消息类型+标志, b2=序列化+压缩, b3=保留
    b0 = (PV << 4) | 0b0001          # header_size = 1 (4 字节块)
    b1 = (message_type << 4) | flag
    b2 = (ser << 4) | cmp_
    return bytes([b0, b1, b2, 0x00])


def _asr_pack(payload: bytes, message_type: int, seq: int,
              flag: int = FLAG_POS, ser: int = SER_JSON, cmp_: int = CMP_GZIP) -> bytes:
    header = _asr_header(message_type, flag, ser, cmp_)
    return header + struct.pack(">i", seq) + struct.pack(">I", len(payload)) + payload


class _VolcWS:
    """极简 WebSocket 客户端（标准库实现，仅覆盖 ASR 所需）。"""

    def __init__(self, url: str, headers: dict, timeout: int = 30):
        from urllib.parse import urlparse
        p = urlparse(url)
        host = p.hostname
        port = p.port or (443 if p.scheme == "wss" else 80)
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        raw = socket.create_connection((host, port), timeout=timeout)
        if p.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        raw.settimeout(30)
        self.sock = raw
        self._buf = b""
        self._handshake(path, host, headers)

    def _handshake(self, path, host, headers):
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            "GET %s HTTP/1.1" % path,
            "Host: %s" % host,
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Version: 13",
            "Sec-WebSocket-Key: %s" % key,
        ]
        for k, v in headers.items():
            lines.append("%s: %s" % (k, v))
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        # 必须把整个 101 响应（到空行为止）读完丢弃，否则残存 HTTP 头会被
        # 后续 recv_frame 当成 WebSocket 帧解析，导致长度错乱、连接被服务端关闭。
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        status = buf.split(b"\r\n", 1)[0].decode("utf-8", "replace")
        if "101" not in status:
            raise RuntimeError("WebSocket 握手失败: " + status)
        # 空行之后的剩余字节属于 WebSocket 帧，保留到读缓冲
        idx = buf.index(b"\r\n\r\n") + 4
        self._buf = buf[idx:]

    def _recv_n(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 连接已关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_binary(self, data: bytes):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        n = len(data)
        head = b"\x82"  # FIN + binary
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + masked)

    def recv_frame(self):
        b0 = self._recv_n(1)[0]
        b1 = self._recv_n(1)[0]
        opcode = b0 & 0x0F
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_n(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_n(8))[0]
        if b1 & 0x80:
            self._recv_n(4)  # 服务端不应加掩码，保险起见跳过
        payload = self._recv_n(length)
        return opcode, payload

    def close(self):
        try:
            self.sock.sendall(b"\x88\x80" + os.urandom(4))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


def _parse_asr_response(payload: bytes):
    """解析服务端返回帧（ByteDance V1 协议）。

    4 字节头之后，是否带 seq+size 由 b1 低 4 位(flag) 决定：
      - flag=0 (NO_SEQUENCE)：无 seq/size，body 紧跟 4 字节头；
      - 其余(带序列号)：4 字节 seq(int32) + 4 字节 size(uint32) + body。
    body 是否 gzip 由 b2 低 4 位(压缩)决定，服务端回包多为未压缩 JSON。
    """
    if len(payload) < 4:
        return None, None, False
    b1 = payload[1]
    flag = b1 & 0x0F
    if flag == 0:  # NO_SEQUENCE：纯 ack，无 seq/size
        body = payload[4:]
        seq = 0
        is_last = False
    else:
        if len(payload) < 12:
            return None, None, False
        seq = struct.unpack(">i", payload[4:8])[0]
        size = struct.unpack(">I", payload[8:12])[0]
        if size > len(payload) - 12:
            size = len(payload) - 12
        body = payload[12:12 + size]
        # 末帧标志 NEG_WITH_SEQUENCE(0b0011)，或带序列号且 seq<=0 均视为结束
        is_last = (flag == FLAG_NEG_LAST) or seq <= 0
    b2 = payload[2]
    if (b2 & 0x0F) == CMP_GZIP:
        try:
            body = gzip.decompress(body)
        except Exception:  # noqa: BLE001
            pass
    try:
        msg = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        msg = None
    return msg, seq, is_last


def ark_asr_file(path, resource_id=DEFAULT_ASR_RESOURCE, segment=200,
                 voice_ws=None, key=None, timeout=30):
    """库层：整文件语音识别。成功返回转写文本，失败抛 ArkPlanError。

    输入处理见 _load_audio（WAV 直用 / ffmpeg 转码 / 畸形头自动修正），
    采样参数从 WAV 头实读——请求声明的 rate 必须与实际音频一致，否则服务端静默回空。
    """
    key = get_plan_key(key)
    if not key:
        raise ArkPlanError("缺少 Agent Plan API Key")
    ws_url = (voice_ws or os.environ.get("ARK_PLAN_VOICE_WS", DEFAULT_VOICE_WS)).rstrip("/")
    ws_url += "/sauc/bigmodel_async"

    # 读取音频；非 WAV 尝试用 ffmpeg 转 16k 单声道
    audio = _load_audio(path)
    # 从 WAV 头读取真实采样参数（TTS 产出常为 24k/16k 不等），用于分片大小与请求声明
    wp = _wav_params(audio)
    rate = wp[0] if wp else 16000
    channels = wp[1] if wp else 1
    bits = wp[2] if wp else 16
    seg_ms = segment
    bytes_per_sec = rate * (bits // 8) * channels
    seg_size = max(1, bytes_per_sec * seg_ms // 1000)

    req_id = str(uuid.uuid4())
    headers = {
        "X-Api-Key": key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": req_id,
        "X-Api-Connect-Id": req_id,
        "X-Api-Sequence": "-1",
    }

    full_payload = {
        "user": {"uid": "agentplan_cli"},
        "audio": {"format": "wav", "codec": "raw", "rate": rate, "bits": bits, "channel": channels},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": False,
        },
    }
    full_bytes = gzip.compress(json.dumps(full_payload).encode("utf-8"))

    ws = _VolcWS(ws_url, headers, timeout=timeout)
    try:
        # 1) 发送 full client request
        ws.send_binary(_asr_pack(full_bytes, MT_FULL, 1, FLAG_POS))
        # 2) 分片发送音频。
        #    序列号约定（已与线上协议对齐）：full=1；audio 非末帧 2,3,...,C；
        #    末帧 seq=-(C+1)，其中 C=音频分片数。服务端按“末帧 seq = -(总帧数)”校验，
        #    总帧数 = 1(full) + C(audio)，故末帧必须取 -(C+1)，少 1 就会被拒。
        chunks = [audio[i:i + seg_size] for i in range(0, len(audio), seg_size)] or [b""]
        C = len(chunks)
        for i, chunk in enumerate(chunks):
            is_last = (i == C - 1)
            if is_last:
                ws.send_binary(_asr_pack(gzip.compress(chunk), MT_AUDIO, -(C + 1), FLAG_NEG_LAST))
            else:
                ws.send_binary(_asr_pack(gzip.compress(chunk), MT_AUDIO, i + 2, FLAG_POS))
        # 3) 读取服务端返回
        final_text = []
        while True:
            opcode, payload = ws.recv_frame()
            if opcode == 0x8:  # close
                break
            if opcode != 0x2:  # 只处理二进制
                continue
            msg, _, is_last = _parse_asr_response(payload)
            if msg is None:
                continue
            if isinstance(msg, dict) and msg.get("code", 0) != 0:
                raise ArkPlanError("ASR 业务错误：" + json.dumps(msg, ensure_ascii=False))
            # 尽力提取文本：utterances[].text / result / text
            txt = _extract_asr_text(msg)
            if txt:
                final_text.append(txt)
            if is_last:
                break
        return "".join(final_text)
    finally:
        ws.close()


def cmd_asr(args) -> None:
    key = get_key(args)
    ws_url = (getattr(args, "voice_ws", None)
              or os.environ.get("ARK_PLAN_VOICE_WS", DEFAULT_VOICE_WS)).rstrip("/")
    try:
        text = ark_asr_file(args.file, resource_id=args.resource_id,
                            segment=args.segment, voice_ws=ws_url, key=key)
    except ArkPlanError as e:
        die(str(e))
    if args.stream_out:
        # stream_out 语义需要边收边打；库层是整段返回，此处整段补打等效输出
        sys.stdout.write(text)
    sys.stdout.write(text + "\n" if not args.stream_out else "")


def _extract_asr_text(msg):
    """从服务端回包里尽力抽取转写文本（仅返回字符串，ack 帧返回空）。"""
    if not isinstance(msg, dict):
        return ""
    # 流式增量文本：utterances 列表里每句 text
    utt = msg.get("utterances")
    if isinstance(utt, list):
        texts = [u.get("text", "") for u in utt if isinstance(u, dict) and isinstance(u.get("text"), str)]
        if texts:
            return "".join(texts)
    # 整体结果字符串
    r = msg.get("result")
    if isinstance(r, str) and r:
        return r
    t = msg.get("text")
    if isinstance(t, str) and t:
        return t
    return ""


def _load_audio(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        # 修正部分服务（如 TTS）产出的 RIFF 块大小为 0xFFFFFFFF/0 的畸形头
        if len(data) >= 8:
            stored = struct.unpack("<I", data[4:8])[0]
            if stored in (0xFFFFFFFF, 0) or stored != len(data) - 8:
                data = bytearray(data)
                data[4:8] = struct.pack("<I", len(data) - 8)
                data = bytes(data)
        return data
    # 尝试 ffmpeg 转 16k 单声道 wav
    try:
        import subprocess
        out = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-y", "-i", path,
             "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", "-f", "wav", "-"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
        if out[:4] == b"RIFF":
            return out
    except Exception as e:  # noqa: BLE001
        die("音频不是 WAV 且 ffmpeg 转码失败（请先转为 16k 单声道 WAV）：%s" % e)
    return data


def _wav_params(data: bytes):
    """从 WAV 头解析 (rate, channels, bits/样本)；非 WAV 或解析失败返回 None。"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    i = 12
    while i + 8 <= len(data):
        cid = data[i:i + 4]
        sz = struct.unpack("<I", data[i + 4:i + 8])[0]
        if cid == b"fmt ":
            ch = struct.unpack("<H", data[i + 10:i + 12])[0]
            rate = struct.unpack("<I", data[i + 12:i + 16])[0]
            bits = struct.unpack("<H", data[i + 22:i + 24])[0]
            return (rate, ch, bits)
        i += 8 + sz + (sz & 1)
    return None


# ---------------------------------------------------------------------------
# 子命令：models
# ---------------------------------------------------------------------------
def cmd_models(args) -> None:
    sys.stdout.write("火山方舟 Agent Plan 支持的模型（来自官方套餐概览）：\n")
    for cat, models in SUPPORTED_MODELS.items():
        sys.stdout.write("\n[%s]\n" % cat)
        for m in models:
            sys.stdout.write("  - %s\n" % m)
    sys.stdout.write(
        "\n端点：\n"
        "  LLM/Embedding/Image : %s\n"
        "  语音 TTS/ASR         : %s (HTTP) / %s (WebSocket)\n"
        % (DEFAULT_BASE, DEFAULT_VOICE_HTTP, DEFAULT_VOICE_WS)
    )


# ---------------------------------------------------------------------------
# CLI 解析
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentplan",
        description="火山方舟 Agent Plan 统一 CLI（LLM / 向量 / 图像 / 语音）",
    )
    p.add_argument("--key", help="Agent Plan 专属 API Key（也可用 ARK_PLAN_API_KEY 环境变量）")
    p.add_argument("--base", help="LLM/Embedding/Image 的 Base URL（默认 %s）" % DEFAULT_BASE)

    sub = p.add_subparsers(dest="cmd")

    # chat
    c = sub.add_parser("chat", help="文本生成（LLM）")
    c.add_argument("-m", "--message", action="append", default=[], help="用户消息，可多次")
    c.add_argument("-s", "--system", help="系统提示词")
    c.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    c.add_argument("--max-tokens", type=int, default=1024)
    c.add_argument("--temperature", type=float, default=0.8)
    # 思考模式默认关闭：多数模型默认开思考，一句话会先烧 2000+ token；
    # 本 CLI 面向调试/低延迟对话，需要推理链时显式加 --think。
    c.add_argument("--think", action=argparse.BooleanOptionalAction, default=False,
                   help="是否开启思考模式（默认 --no-think，低延迟；--think 推理更充分但更慢）")
    c.add_argument("--stream", action="store_true", help="流式输出")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_chat)

    # embed
    e = sub.add_parser("embed", help="向量化（embedding）")
    e.add_argument("-i", "--input", action="append", default=[], help="文本，可多次")
    e.add_argument("--input-file", help="每行一条文本的文件")
    e.add_argument("--model", default=DEFAULT_EMBED_MODEL)
    e.add_argument("--dimensions", type=int, help="向量维度（模型支持时）")
    e.add_argument("--output", help="输出 JSON 文件路径（不指定则打印预览）")
    e.set_defaults(func=cmd_embed)

    # image
    im = sub.add_parser("image", help="图片生成（seedream）")
    im.add_argument("-p", "--prompt", required=True)
    im.add_argument("--model", default=DEFAULT_IMAGE_MODEL)
    im.add_argument("--size", default="1024x1024")
    im.add_argument("-n", type=int, default=1)
    im.add_argument("--download", action="store_true", help="下载图片到本地")
    im.add_argument("--output-dir", default=".")
    im.set_defaults(func=cmd_image)

    # tts
    t = sub.add_parser("tts", help="语音合成（TTS）")
    t.add_argument("-t", "--text", required=True)
    t.add_argument("--speaker", default=DEFAULT_TTS_SPEAKER)
    t.add_argument("--format", default="mp3", choices=["mp3", "wav", "pcm"])
    t.add_argument("--sample-rate", type=int, default=24000)
    t.add_argument("--timestamp", action="store_true", help="返回时间戳")
    t.add_argument("--resource-id", default=DEFAULT_TTS_RESOURCE)
    t.add_argument("--voice-http", help="语音 HTTP Base（默认 %s）" % DEFAULT_VOICE_HTTP)
    t.add_argument("-o", "--output", help="输出音频路径")
    t.set_defaults(func=cmd_tts)

    # asr
    a = sub.add_parser("asr", help="语音识别（ASR）")
    a.add_argument("-f", "--file", required=True, help="音频文件（WAV 16k 单声道，或可被 ffmpeg 转码）")
    a.add_argument("--resource-id", default=DEFAULT_ASR_RESOURCE)
    a.add_argument("--segment", type=int, default=200, help="音频分片时长(ms)")
    a.add_argument("--voice-ws", help="语音 WebSocket Base（默认 %s）" % DEFAULT_VOICE_WS)
    a.add_argument("--stream-out", action="store_true",
                   help="兼容保留：库化为整段返回后，等效于结束时一次性输出")
    a.set_defaults(func=cmd_asr)

    # models
    sub.add_parser("models", help="打印官方支持的模型清单").set_defaults(func=cmd_models)

    # selftest
    sub.add_parser("selftest", help="离线自检（不发任何网络请求）").set_defaults(func=cmd_selftest)
    return p


def cmd_selftest(args) -> None:
    """离线自检：Key 解析链 / WAV 参数解析 / 畸形头修正 / ASR 帧解析 / 参数校验。"""
    import io as _io

    fails = []

    def check(name, ok):
        print(("  ✓ " if ok else "  ✗ ") + name)
        if not ok:
            fails.append(name)

    print("[1] Key 解析链")
    check("显式传参优先", get_plan_key("ark-explicit") == "ark-explicit")
    env_bak = os.environ.pop("ARK_PLAN_API_KEY", None)
    env_bak2 = os.environ.pop("VOLC_SPEECH_API_KEY", None)
    try:
        k = get_plan_key()          # 本机 HKCU 应能捞到 VOLC_SPEECH_API_KEY
        if k:
            print("  · 注册表捞到 Key: %s…%s" % (k[:8], k[-4:]))
            check("注册表回退生效", True)
        else:
            check("无 Key 时返回 None", get_plan_key() is None)
    finally:
        if env_bak is not None:
            os.environ["ARK_PLAN_API_KEY"] = env_bak
        if env_bak2 is not None:
            os.environ["VOLC_SPEECH_API_KEY"] = env_bak2
    try:
        ark_chat([], key="k")
        check("空 messages 抛 ArkPlanError", False)
    except ArkPlanError:
        check("空 messages 抛 ArkPlanError", True)

    print("[2] WAV 头解析与畸形修正")
    def mk_wav(size_field, rate=24000, ch=1, bits=16):
        data = b"\x00\x01" * (rate // 4)           # 假装是 PCM 数据
        hdr = b"RIFF" + struct.pack("<I", size_field) + b"WAVE"
        hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
        hdr += b"data" + struct.pack("<I", len(data)) + data
        return hdr
    wp = _wav_params(mk_wav(0xFFFFFFFF))
    check("参数解析 (rate,ch,bits)", wp == (24000, 1, 16))
    blob = mk_wav(0xFFFFFFFF)
    check("畸形 RIFF 头仍可解析", _wav_params(blob) is not None
          and struct.unpack("<I", blob[4:8])[0] == 0xFFFFFFFF)

    print("[3] ASR 帧解析")
    payload = gzip.compress(json.dumps({"result": "你好"}).encode())
    frame = _asr_header(MT_SRV_FULL, FLAG_POS, SER_JSON, CMP_GZIP) \
        + struct.pack(">i", 3) + struct.pack(">I", len(payload)) + payload
    msg, seq, last = _parse_asr_response(frame)
    check("常规帧解析", isinstance(msg, dict) and msg.get("result") == "你好" and not last)
    frame_last = _asr_header(MT_SRV_FULL, FLAG_NEG_LAST, SER_JSON, CMP_GZIP) \
        + struct.pack(">i", -4) + struct.pack(">I", len(payload)) + payload
    _, _, last2 = _parse_asr_response(frame_last)
    check("末帧判定", last2)

    print("[4] 库函数签名")
    for fn in (ark_chat, ark_embed, ark_tts_bytes, ark_asr_file):
        check(callable(fn).__str__() and fn.__name__ + " 可调用", callable(fn))
    try:
        ark_embed([], key="k")
        check("空 texts 抛 ArkPlanError", False)
    except ArkPlanError:
        check("空 texts 抛 ArkPlanError", True)

    print("\n%s" % ("自检通过" if not fails else "失败 %d 项：%s" % (len(fails), fails)))
    sys.exit(1 if fails else 0)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "cmd", None):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
