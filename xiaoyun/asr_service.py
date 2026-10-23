#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
小芸 ASR 服务（本地语音识别）
端口 8091，只负责"听"和"转写成文字"；理解与发声仍由 xiaoyun_server.py(8090) 负责。

设计要点：
  · 麦克风采集用 sounddevice（自带 PortAudio，无需额外驱动）
  · "说完了没有"用能量 VAD 判断（纯 numpy，不依赖 silero，避免再去外网下模型）
  · 转写用 faster-whisper，默认 device=cpu + int8 —— 显存留给 Ollama 的 qwen3.5-9b
  · 状态机：listening(收音) -> thinking(转写) -> pending(等后端取走) -> paused(播放中不收音)

接口：
  GET  /health                 服务与模型状态
  POST /transcribe             直接转写一段音频（body=音频字节 或 ?pcm=1&sr=16000 的 float32 PCM）
  POST /session/start {mode}   开始语音会话，mode = "ptt"(按键说话) | "vad"(免手动连续)
  POST /session/stop           停止收音；ptt 模式下立即转写本次录音
  POST /session/cancel         放弃本次会话并丢弃录音
  POST /session/consume        取走已转写好的文字（后端消费后进入 paused，避免收到她自己的声音）
  POST /session/resume         恢复收音（后端播完语音后调用）
  GET  /session/state          当前状态 / 音量电平 / 最近转写
"""
import gzip
import json
import os
import queue
import struct
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

HOST, PORT = "127.0.0.1", int(os.environ.get("XIAOYUN_ASR_PORT", "8091"))
MODEL_DIR = os.environ.get("XIAOYUN_ASR_MODEL",
                           os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models', 'faster-whisper-small')))
LANG = os.environ.get("XIAOYUN_ASR_LANG", "zh")
SR = 16000

# --- VAD 参数（30ms 一块）---
CHUNK = 480                 # 30ms @16kHz
PRE_ROLL = 12               # 话音前多留 360ms，别把首字切掉
SILENCE_CHUNKS = 30         # 连续 900ms 静默 = 说完了
MIN_SPEECH = 8              # 短于 240ms 的算误触发，丢弃
MAX_UTTER = 15 * SR // CHUNK   # 单句最长 15 秒，防失控
NOISE_FLOOR_ALPHA = 0.05    # 噪声底的自适应速度（双向：既会降也会升，自动贴合本机麦克风）
LEVEL_SMOOTH = 0.35         # 音量电平平滑系数

# 阈值下限：实测本机麦克风（Realtek HD）环境静音 RMS 仅 ~0.00015、峰值 0.00025，
# 而旧默认 0.012 是它的 ~50 倍 —— 正常说话常常够不到，VAD 永远判不出"有人说话"，
# 于是'一直听、从不结束'，用户体感就是"她不知道我说完了/说完没反应"。
# 下调到 0.0015（约为环境峰值的 6 倍，仍有足够余量），并保留噪声底自适应。
# 想再调：设环境变量 XIAOYUN_ASR_THR=0.001（越低越灵敏）后重启 8091。
THR_FLOOR = float(os.environ.get("XIAOYUN_ASR_THR", "0.0015"))

# 噪声底倍数：实际触发线 = max(下限, 噪声底 × K)。K 越小越灵敏，但**低于
# "环境峰值 ÷ 噪声底"就会持续误触发**（一直判成在说话）。USB 麦实测噪声底 ~0.0007，
# 故 K=3.2 时实际线会顶到 ~0.0022（偏钝）。配合"播放期间不收音"消除了最大的误触发源后，
# 可以把 K 调小以提高灵敏度。用 XIAOYUN_ASR_NOISE_K 调，改完重启 8091。
NOISE_K = float(os.environ.get("XIAOYUN_ASR_NOISE_K", "3.2"))

BEAM = int(os.environ.get("XIAOYUN_ASR_BEAM", "1"))

# initial_prompt 不能写成"以下是简体中文普通话的口语对话。"这类**元描述句**——
# Whisper 会把提示语本身当成要转写的内容原样输出。实测日志里就出现过
#   转写 -> "这些是简体中文普通话的口语。"
# 与提示语几乎逐字对应，这是典型幻觉。改成一句**自然的口语**，既是领域偏置，
# 又不会被当成内容复读。
INITIAL_PROMPT = os.environ.get("XIAOYUN_ASR_PROMPT",
                                "你好，念念。跟你说个事儿。")

# ==================== 幻觉抑制（本次"识别不准"的主因）====================
# 原调用只给了 beam_size=1 + temperature=0.0（纯贪婪解码），且四个质量门
# 一个都没设，等于"模型编出什么就信什么"。faster-whisper 1.2.1 全部支持：
#   · log_prob_threshold        平均对数概率低于此值 -> 判定为垃圾，换温度重解
#   · compression_ratio_threshold 文本压缩比高于此值 -> 判定为复读循环，换温度重解
#   · no_speech_threshold       该段无语音的概率高于此值 -> 直接跳过（不依赖 vad_filter）
#   · repetition_penalty        解码时惩罚重复 token，从源头压掉"复读机"
# 实测证据：同一句被编成"坐我旁边"/"坐我跑吧"两个版本，尾部随机漂移。
TEMPS = [float(x) for x in os.environ.get(
    "XIAOYUN_ASR_TEMPS", "0.0,0.2,0.4,0.6,0.8,1.0").split(",")]
LOGPROB_THR = float(os.environ.get("XIAOYUN_ASR_LOGPROB_THR", "-0.80"))
COMPRESS_THR = float(os.environ.get("XIAOYUN_ASR_COMPRESS_THR", "1.80"))
NO_SPEECH_THR = float(os.environ.get("XIAOYUN_ASR_NO_SPEECH_THR", "0.60"))
REP_PENALTY = float(os.environ.get("XIAOYUN_ASR_REP_PENALTY", "1.15"))
BEST_OF = int(os.environ.get("XIAOYUN_ASR_BEST_OF", "5"))


def log(*a):
    print("[asr]", *a, flush=True)


# --- 统一事件日志：前端页面 / 桌面壳 / 后端 / ASR 都往同一个文件写 ---
# 出现"点了没反应/说完不回"时，直接看这个文件的时间线，一眼能看出是哪一步断了。
EVENT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "前端事件日志.txt")


def evlog(tag, msg):
    try:
        with open(EVENT_LOG, "a", encoding="utf-8") as f:
            f.write("%s [%s] %s\n" % (time.strftime("%H:%M:%S"), tag, msg))
    except Exception:
        pass


# ==================== 模型 ====================
class Asr:
    """faster-whisper 包装：惰性加载 + 线程安全"""

    def __init__(self):
        self.model = None
        self.loading = False
        self.error = None
        self.load_secs = None

    def ensure(self):
        if self.model or self.loading:
            return self.model is not None
        self.loading = True
        try:
            t0 = time.time()
            from faster_whisper import WhisperModel
            # 线程数别拉满：机器上还跑着 Ollama(qwen3.5-9b) 和后端，留点 CPU 给别人
            threads = int(os.environ.get("XIAOYUN_ASR_THREADS",
                                          str(max(2, min(6, (os.cpu_count() or 4) - 2)))))
            self.model = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8",
                                      cpu_threads=threads)
            self.load_secs = round(time.time() - t0, 2)
            log("模型加载完成 %.2fs  %s  cpu_threads=%d" % (self.load_secs, MODEL_DIR, threads))
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            log("模型加载失败:", self.error)
        finally:
            self.loading = False
        return self.model is not None

    def transcribe(self, audio16k, lang=LANG):
        """audio16k: float32 numpy 数组，16kHz 单声道。
        返回 (文字, 音频秒数, 耗时秒, 质量指标dict)"""
        q = {"avg_logprob": None, "no_speech_prob": None,
             "compression_ratio": None, "dropped": ""}
        if not self.ensure():
            raise RuntimeError("模型未就绪: " + str(self.error))
        if audio16k is None or len(audio16k) < SR // 10:
            return "", 0.0, 0.0, q
        # 转写前先做峰值归一化：这只 USB 麦说话时 RMS 只有 0.006~0.019（正常应
        # 0.05~0.2），低电平会让解码器更容易滑向幻觉。统一抬到 -3dBFS 再送入。
        audio16k = _peak_normalize(audio16k)
        t0 = time.time()
        segs, info = self.model.transcribe(
            np.ascontiguousarray(audio16k, dtype=np.float32),
            language=lang, beam_size=BEAM, best_of=BEST_OF, temperature=TEMPS,
            vad_filter=False, condition_on_previous_text=False,
            initial_prompt=INITIAL_PROMPT,
            # ↓ 四道质量门，任一项不过就换温度重解，全不过则返回空
            log_prob_threshold=LOGPROB_THR,
            compression_ratio_threshold=COMPRESS_THR,
            no_speech_threshold=NO_SPEECH_THR,
            repetition_penalty=REP_PENALTY,
            suppress_blank=True,
            word_timestamps=False)
        # 关键：原来的 segs, _info 把 info 整个丢了。语言置信度/时长都在里面，
        # 是判断"这次结果能不能信"的依据之一。
        # 注意：faster-whisper 的 TranscriptionInfo **没有** avg_logprob /
        # no_speech_prob / compression_ratio（这三个只在 Segment 上），实测踩过。
        try:
            q["lang_prob"] = round(float(info.language_probability), 3)
            q["lang"] = str(info.language)
            q["duration"] = round(float(info.duration), 2)
        except Exception:
            pass
        segs = list(segs)
        if segs:
            try:
                q["avg_logprob"] = round(
                    sum(s.avg_logprob for s in segs) / len(segs), 3)
                q["compression_ratio"] = round(
                    sum(s.compression_ratio for s in segs) / len(segs), 3)
                q["no_speech_prob"] = round(max(s.no_speech_prob for s in segs), 3)
                q["temperature"] = segs[-1].temperature
            except Exception:
                pass
        text = "".join(s.text for s in segs).strip()
        # 最后一道闸：结果不能是提示语的复读，也不能是同一小段话的自我复制
        if text and _looks_like_hallucination(text):
            q["dropped"] = "疑幻觉(复读/回显提示语)"
            evlog("ASR", "丢弃疑幻觉转写 [%s] -> %s" % (q["dropped"], text[:50]))
            text = ""
        return text, round(len(audio16k) / SR, 2), round(time.time() - t0, 2), q


# ==================== 百度智能云 短语音识别（云端兜底）====================
# 本地 Whisper 在"低电平 + 小模型"下会编话（见 INITIAL_PROMPT 注释里的实测）。
# 云端识别不依赖本机算力，且不会回显提示语，作为兜底通道。
# 需要的权限：百度智能云 → 语音技术 → 短语音识别（本应用必须勾选该服务，
# 否则会返回 err_no=3302 "No permission to access data"）。
BAIDU_APP_ID = os.environ.get("XIAOYUN_BAIDU_APP_ID", "").strip()
BAIDU_API_KEY = os.environ.get("XIAOYUN_BAIDU_API_KEY", "").strip()
BAIDU_SECRET_KEY = os.environ.get("XIAOYUN_BAIDU_SECRET_KEY", "").strip()
# dev_pid: 1537=普通话(纯中文) 1536=普通话(支持英文) 1737=英语 1637=粤语 1837=四川话
BAIDU_DEV_PID = int(os.environ.get("XIAOYUN_BAIDU_DEV_PID", "1537"))
BAIDU_URL = os.environ.get("XIAOYUN_BAIDU_URL", "https://vop.baidu.com/server_api")
# provider: auto(优先云端，失败回落本地) | volc | baidu | whisper
PROVIDER = os.environ.get("XIAOYUN_ASR_PROVIDER", "auto").strip().lower()


# ==================== 火山方舟 Agent Plan · 豆包流式识别 2.0 ====================
# 2026-10-18 接入。端点与 Resource-Id 由 Agent Plan 套餐固定，**不支持 Auto/控制台切换**，
# 必须显式带上；认证只认「专属 API Key」（不是方舟模型 Key，用模型 Key 会 401）。
# 实测：5.21s 音频 -> 1.55s 出整句，识别质量明显优于本地 small 模型。
VOLC_ASR_WS = os.environ.get("XIAOYUN_VOLC_ASR_WS",
                             "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream")
VOLC_ASR_RES = os.environ.get("XIAOYUN_VOLC_ASR_RES", "volc.seedasr.sauc.duration")


def _reg_env(name):
    """读用户/系统环境变量。setx 写的值，已启动的进程读不到，必须查注册表。"""
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
    except Exception:
        pass
    return ""


# Step 5 收编：与 agentplan.get_plan_key() / xiaoyun_server._plan_key() 同一链路
# （ARK_PLAN_API_KEY → VOLC_SPEECH_API_KEY，env → 注册表）。本服务保持自包含、
# 不反向依赖 agentplan 模块，链路对齐即可。
VOLC_KEY = (os.environ.get("ARK_PLAN_API_KEY", "").strip()
            or os.environ.get("VOLC_SPEECH_API_KEY", "").strip()
            or _reg_env("ARK_PLAN_API_KEY")
            or _reg_env("VOLC_SPEECH_API_KEY"))

# 火山二进制协议常量：4 字节头(ver/hdr_size | msg_type/flags | ser/cmp | 保留) + 4 字节大端长度
_P_VER, _P_HDR = 0b0001, 0b0001
_M_FULL_REQ, _M_AUDIO_REQ, _M_FULL_RESP, _M_ERR = 0b0001, 0b0010, 0b1001, 0b1111
_S_JSON, _S_RAW, _C_GZIP, _C_NONE = 0b0001, 0b0000, 0b0001, 0b0000
_F_LAST = 0b0010


def _volc_packet(msg_type, flags, ser, cmp_, payload=b""):
    return (bytes([(_P_VER << 4) | _P_HDR, (msg_type << 4) | flags,
                   (ser << 4) | cmp_, 0x00])
            + struct.pack(">I", len(payload)) + payload)


def _volc_parse(data):
    """解析服务端帧 -> dict(type=resp|error|other, flags, json)。"""
    if len(data) < 4:
        return {"type": "other", "msg_type": -1, "flags": 0}
    hdr_words = data[0] & 0x0F
    msg_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    cmp_ = data[2] & 0x0F
    off = hdr_words * 4
    if msg_type == _M_ERR:
        code = int.from_bytes(data[off:off + 4], "big")
        size = int.from_bytes(data[off + 4:off + 8], "big")
        return {"type": "error", "code": code,
                "message": data[off + 8:off + 8 + size].decode("utf-8", "ignore")}
    if msg_type != _M_FULL_RESP:
        return {"type": "other", "msg_type": msg_type, "flags": flags}
    # 不同版本是否带 seq 前缀有差异，两种偏移都试一次，避免整包解析失败
    for skip in (0, 4):
        try:
            base = off + skip
            size = int.from_bytes(data[base:base + 4], "big")
            if size <= 0 or base + 4 + size > len(data) + 8:
                continue
            payload = data[base + 4:base + 4 + size]
            if cmp_ == _C_GZIP:
                payload = gzip.decompress(payload)
            j = json.loads(payload.decode("utf-8", "ignore")) if payload else {}
            if isinstance(j, dict):
                return {"type": "resp", "flags": flags, "json": j}
        except Exception:
            continue
    return {"type": "other", "msg_type": msg_type, "flags": flags}


class VolcAsr:
    """豆包流式识别 2.0（一句话识别/no-stream 版）：整段 PCM 推上去，一次拿整句文本。
    与本地 Whisper 的输入完全一致（float32/16k/mono），所以能直接插进 Router。"""

    NAME = "volc"

    def __init__(self):
        self.error = None
        self.last_logid = ""

    def available(self):
        return bool(VOLC_KEY)

    def transcribe(self, audio16k, lang=LANG):
        import websocket
        q = {"provider": "volc", "logid": ""}
        if audio16k is None or len(audio16k) < SR // 10:
            return "", 0.0, 0.0, q
        a = np.asarray(audio16k, dtype=np.float32)
        a = _peak_normalize(a)
        pcm = np.clip(a * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        req = {"user": {"uid": "xiaoyun"},
               "audio": {"format": "pcm", "rate": SR, "bits": 16, "channel": 1,
                         "language": "zh-CN" if str(lang).startswith("zh") else str(lang)},
               "request": {"model_name": "bigmodel", "enable_itn": True,
                           "enable_punc": True, "show_utterances": False}}
        headers = ["X-Api-Key: " + VOLC_KEY,
                   "X-Api-Resource-Id: " + VOLC_ASR_RES,
                   "X-Api-Request-Id: " + str(uuid.uuid4()),
                   "X-Api-Connect-Id: " + str(uuid.uuid4()),
                   "X-Api-Sequence: -1"]
        t0 = time.time()
        ws = None
        try:
            ws = websocket.create_connection(VOLC_ASR_WS, header=headers, timeout=20,
                                             sslopt={"cert_reqs": __import__("ssl").CERT_NONE})
            try:
                logid = ws.headers.get("X-Tt-Logid") or ""
            except Exception:
                logid = ""
            self.last_logid = logid
            q["logid"] = logid
            ws.send_binary(_volc_packet(_M_FULL_REQ, 0, _S_JSON, _C_GZIP,
                                        gzip.compress(json.dumps(req).encode())))
            step = 6400                      # 200ms @16k/16bit（官方推荐分包）
            for i in range(0, len(pcm), step):
                piece = pcm[i:i + step]
                last = (i + step) >= len(pcm)
                ws.send_binary(_volc_packet(_M_AUDIO_REQ, _F_LAST if last else 0,
                                            _S_RAW, _C_NONE, piece))
                time.sleep(0.02)             # 发包间隔别过密，否则影响服务端性能
            text = ""
            while True:
                msg = _volc_parse(ws.recv())
                if msg["type"] == "error":
                    raise RuntimeError("火山 ASR code=%s %s" %
                                       (msg["code"], (msg.get("message") or "")[:200]))
                if msg["type"] == "resp":
                    res = msg.get("json", {}).get("result") or {}
                    if res.get("text"):
                        text = res["text"]
                    if msg["flags"] & _F_LAST or msg.get("json", {}).get("sequence", 0) < 0:
                        break
                if time.time() - t0 > 30:
                    raise RuntimeError("火山 ASR 超时")
            took = round(time.time() - t0, 2)
            text = (text or "").strip()
            if text and _looks_like_hallucination(text):
                q["dropped"] = "疑幻觉"
                evlog("ASR", "丢弃疑幻觉转写(volc) -> %s" % text[:50])
                text = ""
            self.error = None
            return text, round(len(a) / SR, 2), took, q
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, str(e)[:200])
            raise
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass


class BaiduAsr:
    """百度短语音识别 REST 接口。音频须为 16k/单声道/16bit PCM。"""

    NAME = "baidu"

    def __init__(self):
        self.token = None
        self.token_ts = 0.0
        self.error = None

    def available(self):
        return bool(BAIDU_API_KEY and BAIDU_SECRET_KEY)

    def _token(self):
        # token 有效期 30 天，提前 1 天刷新
        if self.token and (time.time() - self.token_ts) < 29 * 86400:
            return self.token
        import json as _json
        import ssl as _ssl
        import urllib.request as _u
        ctx = _ssl.create_default_context()
        try:
            url = ("https://aip.baidubce.com/oauth/2.0/token"
                   "?grant_type=client_credentials&client_id=%s&client_secret=%s"
                   % (BAIDU_API_KEY, BAIDU_SECRET_KEY))
            req = _u.Request(url, headers={"User-Agent": "xiaoyun-asr"})
            d = _json.loads(_u.urlopen(req, timeout=10, context=ctx).read().decode())
            if "access_token" not in d:
                raise RuntimeError("取 token 失败: %s" % d)
            self.token = d["access_token"]
            self.token_ts = time.time()
            return self.token
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            raise

    def transcribe(self, audio16k, lang=LANG):
        """返回 (文字, 音频秒数, 耗时秒, 质量dict)。失败抛异常，由 Router 回落。"""
        import base64
        import json as _json
        import ssl as _ssl
        import urllib.request as _u

        q = {"provider": "baidu", "err_no": None, "err_msg": None}
        if audio16k is None or len(audio16k) < SR // 10:
            return "", 0.0, 0.0, q
        a = np.asarray(audio16k, dtype=np.float32)
        a = _peak_normalize(a)
        pcm = np.clip(a * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        if len(pcm) > 10 * 1024 * 1024:            # 云端单次上限，超了直接回落本地
            raise RuntimeError("音频过长，超出云端单次限制")
        ctx = _ssl.create_default_context()
        body = _json.dumps({
            "format": "pcm", "rate": 16000, "channel": 1,
            "cuid": (BAIDU_APP_ID or "xiaoyun-pc"),
            "token": self._token(),
            "dev_pid": BAIDU_DEV_PID,
            "speech": base64.b64encode(pcm).decode(),
            "len": len(pcm),
        }).encode()
        req = _u.Request(BAIDU_URL, data=body,
                         headers={"Content-Type": "application/json",
                                  "User-Agent": "xiaoyun-asr"})
        t0 = time.time()
        r = _json.loads(_u.urlopen(req, timeout=20, context=ctx).read().decode())
        took = round(time.time() - t0, 2)
        q["err_no"] = r.get("err_no")
        q["err_msg"] = r.get("err_msg")
        if r.get("err_no") != 0:
            raise RuntimeError("百度 err_no=%s %s" % (r.get("err_no"), r.get("err_msg")))
        text = "".join(r.get("result") or []).strip()
        if text and _looks_like_hallucination(text):
            q["dropped"] = "疑幻觉"
            text = ""
        return text, round(len(a) / SR, 2), took, q


class Router:
    """按 PROVIDER 选择引擎；auto = 火山(Agent Plan) -> 百度 -> 本地 Whisper，逐级回落。

    2026-10-18 起火山是主力：识别质量与速度都优于本地 small 模型。
    任何一级失败都记日志并继续往下掉，绝不会"识别不出来就不说话"。"""

    def __init__(self):
        self.whisper = Asr()
        self.baidu = BaiduAsr()
        self.volc = VolcAsr()
        self.last_provider = "whisper"

    def transcribe(self, audio16k, lang=LANG):
        chain = []
        if PROVIDER in ("auto", "volc") and self.volc.available():
            chain.append(self.volc)
        if (PROVIDER == "baidu") or (PROVIDER == "auto" and self.baidu.available()):
            chain.append(self.baidu)
        # whisper 永远兜底：本地、离线、不吃额度
        for eng in chain:
            try:
                text, secs, took, q = eng.transcribe(audio16k, lang)
                self.last_provider = eng.NAME
                q["provider"] = eng.NAME
                return text, secs, took, q
            except Exception as e:
                log("%s 识别失败，回落下一级: %s" % (eng.NAME, e))
                evlog("ASR", "%s 识别失败，回落: %s" % (eng.NAME, str(e)[:200]))
        text, secs, took, q = self.whisper.transcribe(audio16k, lang)
        self.last_provider = "whisper"
        q["provider"] = "whisper"
        return text, secs, took, q


ASR = Asr()          # 保留给 /health 用（模型状态、加载耗时）
ENGINE = Router()


# ==================== 音频预处理 / 幻觉判别 ====================
def _peak_normalize(audio, target=0.7):
    """把整段音频的峰值抬到 target（-3dBFS 左右）。
    这只 USB 麦说话时 RMS 只有 0.006~0.019、峰值不到 0.15，属于"远低于满刻度"输入；
    Whisper 内部虽会归一化，但过低的绝对电平意味着 ADC 有效位少、量化噪声占比高，
    解码更容易跑偏。这里统一抬满，让每次送入模型的响度一致。"""
    a = np.asarray(audio, dtype=np.float32)
    if a.size == 0:
        return a
    pk = float(np.max(np.abs(a)))
    if pk < 1e-6:                      # 全零/数字静默，别放大成噪声
        return a
    if pk >= target:                   # 已经够响，不动（避免把爆音也压回来）
        return a
    return (a * (target / pk)).astype(np.float32)


def _looks_like_hallucination(text):
    """结果层兜底：命中即判为幻觉。
    两类和实测现象一一对应：
      1) 回显 initial_prompt（"这些是简体中文普通话的口语。"）
      2) 同一小段话自我复制（"坐我旁边"/"坐我跑吧"这类尾部漂移的前兆）"""
    import re as _re
    t = _re.sub(r"[\s，。？！、,.?!\"'“”‘’：:；;…\-—]", "", text or "")
    if len(t) < 2:
        return False
    # 0) 历史上真实出现过的"提示语被当内容输出"。即使以后改了 initial_prompt，
    #    这些元描述句也永远是幻觉，直接拉黑。
    for bad in ("以下是简体中文普通话的口语对话", "这些是简体中文普通话的口语",
                "以下是普通话的口语对话", "简体中文普通话的口语"):
        if bad in t:
            return True
    # 1) 与当前提示语高度重合
    p = _re.sub(r"[\s，。？！、,.?!\"'“”‘’：:；;…\-—]", "", INITIAL_PROMPT or "")
    if len(p) >= 4:
        grams = {p[i:i + 3] for i in range(len(p) - 2)}
        hit = sum(1 for g in grams if g in t)
        if grams and hit / float(len(grams)) >= 0.5:
            return True
    # 2) 重复片段：任意 2~6 字片段出现 >= 4 次
    for n in (2, 3, 4, 5, 6):
        if len(t) < n * 3:
            continue
        for i in range(len(t) - n):
            frag = t[i:i + n]
            if t.count(frag) >= 4:
                return True
    # 3) 整句由单一字符反复构成（"啊啊啊啊…"）
    if len(t) >= 6 and len(set(t)) <= 2:
        return True
    return False


# ==================== 录音 + VAD ====================
class Session:
    """一次语音会话。state: off / listening / thinking / pending / paused"""

    def __init__(self):
        self.lock = threading.RLock()   # 可重入：_finish 里还会再进锁
        self.mode = "ptt"
        self.state = "off"
        self.level = 0.0            # 平滑后的音量电平 0~1，给 UI 做光圈
        self.raw_level = 0.0
        self.noise = 0.001          # 噪声底（双向自适应，会自动收敛到本机麦克风的实际静音电平）
        self.text = ""              # 最近一次转写
        self.error = ""
        self.detail = ""            # 给界面看的提示，如"正在识别…"
        self.seq = 0                # 每次有新转写就 +1，方便轮询去重
        self.consumed_seq = -1
        self.blocks_seen = 0        # 已收到的 30ms 音频块数（诊断麦克风是否真的在收音）
        self.device_name = ""       # 实际使用的输入设备名

        self.chunks = []            # 当前收到的 30ms 音频块
        self.ring = []              # 预存环（话音前的静音）
        self.speech_run = 0         # 连续超阈值的块数
        self.silence_run = 0
        self.in_speech = False
        self.utter = []             # 正在录的这一句
        self.stop_flag = False
        self.native_sr = SR         # 流的实际采样率（设备不支持 16k 时会变）

        self.stream = None
        self.q = queue.Queue()
        self.worker = None
        self.gen = 0              # 会话代际：新会话 +1，旧 worker 看到代际不符就退出
        # 恢复收音后的静默缓冲期：这段时间的音频一律丢弃。
        # ★ 用途：防自循环的最后一道闸。resume 的时机再准也免不了毫秒级时序差
        #   （页面 onended 提前触发、兜底解除、播放尾部拖音），恢复初期录到的
        #   很可能是她声音的尾巴。旧实现直接送 VAD，尾巴够响就触发新一轮转写。
        self.grace_until = 0.0

    # ---------- 阈值 ----------
    def thr(self):
        return max(THR_FLOOR, self.noise * NOISE_K)

    def level01(self, rms):
        return min(1.0, rms * 7.0)

    # ---------- 音频回调 ----------
    def _cb(self, indata, frames, time_info, status):
        if status:
            pass
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _open_stream(self):
        """音频流整个进程只开一次、绝不 close。
        踩过的坑：从 HTTP 线程里 close() 流，而 PortAudio 的回调线程还在跑，
        会直接让进程无声无息死掉（pythonw 连 traceback 都看不到）。
        所以"收起麦克风"只用 abort()，"恢复"用 start()，两者都不碰底层对象。"""
        if self.stream is not None:
            return
        import sounddevice as sd
        # 可用 XIAOYUN_ASR_INPUT 指定麦克风（按名字子串匹配），默认用系统默认输入
        want = os.environ.get("XIAOYUN_ASR_INPUT", "").strip()
        device = None
        if want:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and want.lower() in d["name"].lower():
                    device = i
                    break
            if device is None:
                log("找不到名字含 '%s' 的输入设备，退回系统默认" % want)
        self.device_name = (sd.query_devices(device)["name"] if device is not None
                            else sd.query_devices(kind="input")["name"])
        try:
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                         blocksize=CHUNK, callback=self._cb, device=device)
            self.native_sr = SR
        except Exception:
            # 设备不支持 16k 时退回设备原生采样率，再由 worker 重采样
            dev = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
            self.native_sr = int(dev["default_samplerate"])
            self.stream = sd.InputStream(samplerate=self.native_sr, channels=1, dtype="float32",
                                         blocksize=CHUNK, callback=self._cb, device=device)
            log("设备不支持 16k，退回 %dHz（worker 会重采样）" % self.native_sr)
        log("使用输入设备: %s" % self.device_name)

    def _pause_stream(self):
        """收起麦克风：只 abort，不 close"""
        try:
            if self.stream and self.stream.active:
                self.stream.abort()
        except Exception:
            pass

    def _resume_stream(self):
        try:
            if self.stream and not self.stream.active:
                self.stream.start()
        except Exception as e:
            with self.lock:
                self.error = "麦克风重开失败: %s" % e

    def _drain(self):
        """把队列里的块取出并按需重采样成 16k/30ms"""
        out = []
        native_sr = self.native_sr or SR
        while True:
            try:
                blk = self.q.get_nowait()
            except queue.Empty:
                break
            blk = np.asarray(blk, dtype=np.float32).reshape(-1)
            if blk.size == 0:
                continue
            if native_sr != SR:
                n = int(round(blk.size * SR / native_sr))
                if n <= 0:
                    continue
                idx = np.linspace(0, blk.size - 1, n)
                blk = np.interp(idx, np.arange(blk.size), blk).astype(np.float32)
            out.append(blk)
        return out

    # ---------- 会话控制 ----------
    def start(self, mode="ptt"):
        with self.lock:
            # 正在收音/识别中不给重开；off / paused / pending 都可以直接重开
            if self.state in ("listening", "thinking"):
                return False, "正在%s中" % ("收音" if self.state == "listening" else "识别")
            self.mode = mode
            self.state = "listening"
            self.detail = "正在听…" if mode == "vad" else "按住说话"
            self.error = ""
            self.chunks, self.ring, self.utter = [], [], []
            self.speech_run = self.silence_run = 0
            self.in_speech = False
            self.stop_flag = False
            self.grace_until = time.time() + 0.8   # 开麦初期同样留缓冲，防上一段播放尾音
        self._drop_queue()      # 新会话从干净队列开始（上一段残留可能含她自己的声音）
        try:
            self._open_stream()           # 流只开一次；已开过就直接复用
            self._resume_stream()
        except Exception as e:
            with self.lock:
                self.state = "off"
                self.error = "打不开麦克风: %s" % e
            return False, self.error
        # 每次都开新 worker：旧 worker 靠代际检查自己退出。
        # 绝不复用旧线程 —— 旧 worker 可能正在转写，会和新的抢音频队列，
        # 导致状态错乱、偶发"点了没反应"。
        self.gen += 1
        self.worker = threading.Thread(target=self._loop, args=(self.gen,), daemon=True)
        self.worker.start()
        evlog("ASR", "会话开始 mode=%s 设备=%s" % (self.mode, self.device_name))
        return True, "ok"

    def finish(self):
        """vad 模式专用：用户主动表示"我说完了"，立刻把已录的话结束并转写。

        这是修「连续对话里再点一次麦克风 = 这句话被丢掉」的语义错误：
        旧实现里 vad + stop 走的是 cancel（丢弃录音），页面上却提示"正在识别"，
        于是用户看到"说了话她没反应，好像不知道我说完了"。
        现在：已录到语音 -> 走 _finish 正常转写；完全没录到 -> 直接结束会话。"""
        with self.lock:
            if self.state == "off":
                return False, "没有进行中的会话"
            if self.state != "listening":
                return False, "当前不在收音（state=%s）" % self.state
            if self.utter or self.in_speech:
                self.stop_flag = True          # 交给 _loop 走 _finish(auto=False)
                self.detail = "正在识别…"
                evlog("ASR", "用户主动结束说话 -> 转写已录 %.2fs"
                     % (sum(len(c) for c in self.utter) / float(SR)))
                return True, "ok"
            self.state = "off"
            self.detail = ""
            self.stop_flag = True
        self._pause_stream()
        evlog("ASR", "用户结束说话，但没录到有效语音 -> 会话结束")
        return True, "ok"

    def stop(self):
        """停止收音。ptt: 立刻转写已录内容；vad: 结束会话（真正要转写请用 finish）"""
        with self.lock:
            if self.state == "off":
                return False, "没有进行中的会话"
            if self.mode == "ptt":
                self.stop_flag = True
                self.detail = "正在识别…"
                evlog("ASR", "ptt 停止收音 -> 转写")
                return True, "ok"
            self.state = "paused"
            self.detail = "已暂停"
        self._pause_stream()
        evlog("ASR", "vad 停止收音 -> 暂停（未转写）")
        return True, "ok"

    def cancel(self):
        with self.lock:
            self.state = "off"
            self.detail = ""
            self.stop_flag = True
        self._pause_stream()
        evlog("ASR", "取消会话（录音丢弃）")
        return True, "ok"

    def consume(self):
        """取走转写并**彻底停止收音**（她播放回复期间绝不收音）。

        ★ 旧实现只有 ptt 模式调用 _pause_stream()，vad 模式仅把 state 改成 paused：
          麦克风流其实还开着，PortAudio 回调继续把她自己的播放声灌进队列；等
          resume 时这些积压块会被一起当成本次说话处理 —— 于是她听到自己 → 自问自答。
          现在两种模式都真的 abort 掉麦克风，并清空积压队列。"""
        with self.lock:
            if self.state != "pending":
                return False, "", "当前没有待取走的转写（state=%s）" % self.state
            text = self.text
            self.consumed_seq = self.seq
            self.state = "paused"
            self.detail = "播放中（不收音）"
        self._pause_stream()               # ← 关键 1：真的把麦克风关掉
        self._drop_queue()                 # ← 关键 2：丢掉期间可能积压的音频块
        evlog("ASR", "转写被取走，已停止收音（播放期间不收音）: %s"
             % (text[:60] or "(空)"))
        return True, text, "ok"

    def resume(self):
        with self.lock:
            if self.state not in ("paused", "pending"):
                return False, "当前状态不需要恢复（state=%s）" % self.state
            self.mode = "vad" if self.mode == "vad" else self.mode
            self.state = "listening"
            self.detail = "正在听…"
            self.chunks, self.ring, self.utter = [], [], []
            self.speech_run = self.silence_run = 0
            self.in_speech = False
            self.grace_until = time.time() + 1.2   # 恢复初期静默缓冲（防录到她的尾音）
        self._drop_queue()      # 播放期间/切换期间积压的音频一律丢掉，绝不当成用户说话
        try:
            self._open_stream()
            self._resume_stream()
        except Exception as e:
            with self.lock:
                self.state = "paused"
                self.error = "麦克风重开失败: %s" % e
            return False, self.error
        evlog("ASR", "恢复收音 mode=%s" % self.mode)
        return True, "ok"

    def _drop_queue(self):
        """丢掉积压的音频块（会话切换时用，不碰底层流对象）"""
        self.q = queue.Queue()

    # ---------- 工作线程 ----------
    def _loop(self, my_gen):
        while True:
            if my_gen != self.gen:
                return   # 旧代际的 worker，退出，别和新会话抢音频队列
            try:
                self._loop_once()
            except Exception as e:
                # 线程里抛出异常会静默死掉（pythonw 连 traceback 都看不到），必须自己兜住
                log("工作线程异常: %s: %s" % (type(e).__name__, e))
                with self.lock:
                    if self.state in ("listening", "thinking"):
                        self.state = "paused"
                        self.detail = "收音出错，请再点一次麦克风"
                        self.error = "%s: %s" % (type(e).__name__, e)
                self._drop_queue()
                time.sleep(0.5)

    def _loop_once(self):
        # 一轮处理。停止/结束检查放在最前面：不能因为"这一轮没有音频块"就跳过，
        # 否则 stop() 永远得不到处理，会话卡在"正在识别…"
        need_finish = False
        with self.lock:
            st = self.state
            if st == "off":
                time.sleep(0.2)               # 没有会话，别空转
                return
            if self.stop_flag:
                self.stop_flag = False
                if st == "listening":
                    need_finish = True
        if need_finish:
            self._finish(auto=False)
            return

        # 只有"正在听"才碰音频：转写中/等待取走/播放中一律不收音，
        # 否则会把她自己的声音录进去，还会重复触发转写把 CPU 占满
        if st != "listening":
            time.sleep(0.05)
            return

        blocks = self._drain()
        if not blocks:
            time.sleep(0.01)
            return
        # 恢复缓冲期：丢弃这段时间的音频（可能含她自己声音的尾巴/时序毛刺），
        # 只丢不判，噪声底也不更新——缓冲期一过再进入正常 VAD。
        if time.time() < getattr(self, "grace_until", 0.0):
            return
        self.blocks_seen += len(blocks)
        self._hb()                       # 诊断心跳：把电平/阈值/静音计数写进事件日志

        for blk in blocks:
            rms = float(np.sqrt(np.mean(blk * blk)) + 1e-9)
            self.raw_level = rms
            self._peak = max(getattr(self, "_peak", 0.0), rms)
            self.level = self.level * (1 - LEVEL_SMOOTH) + self.level01(rms) * LEVEL_SMOOTH
            # 噪声底自适应：只要"当前不在说话"，就把噪声底往当前电平靠。
            # 双向（可升可降）才重要：旧实现只在 rms<阈值*0.6 时才更新，
            # 遇到高增益麦克风（环境电平本身就高）会永久卡在初值、阈值虚高；
            # 遇到低增益麦克风又要十几秒才降下来。双向更新两种情况都能自动校准。
            if not self.in_speech:
                self.noise = min(0.05, max(2e-5,
                    self.noise * (1 - NOISE_FLOOR_ALPHA) + rms * NOISE_FLOOR_ALPHA))

            speaking = rms > self.thr()

            if not self.in_speech:
                self.ring.append(blk)
                if len(self.ring) > PRE_ROLL:
                    self.ring.pop(0)
                if speaking:
                    self.speech_run += 1
                    if self.speech_run >= 3:          # 连续 90ms 有人声才算开口
                        self.in_speech = True
                        self.silence_run = 0
                        self.utter = list(self.ring)
                else:
                    self.speech_run = 0
            else:
                self.utter.append(blk)
                if speaking:
                    self.silence_run = 0
                else:
                    self.silence_run += 1
                    if self.silence_run >= SILENCE_CHUNKS:
                        self._finish(auto=True)
                        break
                if len(self.utter) > MAX_UTTER:
                    self._finish(auto=True)
                    break

        need_finish = False
        with self.lock:
            if self.stop_flag:
                self.stop_flag = False
                if self.state == "listening":
                    need_finish = True
        if need_finish:
            self._finish(auto=False)

    def _hb(self):
        """诊断心跳：约每 2 秒记录一次关键量。
        用来回答"她到底有没有在收音 / 说话有没有超过阈值"——
        若"peak"长期低于"阈值"，就是麦克风太轻或阈值偏高，她自然"不知道你在说话/说完"。"""
        now = time.time()
        if now - getattr(self, "_hb_ts", 0.0) < 2.0:
            return
        self._hb_ts = now
        peak = getattr(self, "_peak", 0.0)
        self._peak = 0.0
        # 数字静默（恰好为 0）不同于"环境很安静"：说明这只采集端点根本没在出数据
        # （USB 设备休眠、被独占、或流打开后失效）。标出来免得把设备问题当阈值问题查。
        tip = " ⚠输入是数字静默(设备休眠/被独占/流失效?)" if peak < 1e-5 else ""
        evlog("ASR", "听 peak=%.4f 阈值=%.4f 噪声=%.4f 在语音=%s 静音计数=%d/%d 已收块=%d%s"
              % (peak, self.thr(), self.noise, self.in_speech,
                 self.silence_run, SILENCE_CHUNKS, self.blocks_seen, tip))

    def _finish(self, auto):
        """一句结束：拼音频 -> 裁剪静音 -> 转写 -> pending"""
        audio = np.concatenate(self.utter) if self.utter else np.zeros(0, dtype=np.float32)
        self.utter, self.ring = [], []
        self.in_speech = False
        self.speech_run = self.silence_run = 0
        with self.lock:
            self.state = "thinking"
            self.detail = "正在识别…"

        if len(audio) < SR * 0.24:                      # 太短，当误触发
            evlog("ASR", "结束但音频过短(%.2fs) -> 当误触发，无转写" % (len(audio) / float(SR)))
            release = False
            with self.lock:
                if self.mode == "ptt":
                    self.state = "paused"
                    self.detail = "没听清，再说一次？"
                    self.error = ""
                    release = True
                else:
                    self.state = "listening"
                    self.detail = "正在听…"
                    release = False
            # ↑ 原实现里 release 只在 ptt 分支赋值，vad 分支会用上一次调用的残留值
            #   或直接抛 UnboundLocalError，异常被 _loop 兜住后表现为"收音出错"。
            if release:
                self._pause_stream()
            return

        audio = _trim_silence(audio)
        try:
            text, secs, took, q = ENGINE.transcribe(audio)
        except Exception as e:
            with self.lock:
                self.state = "listening"
                self.error = "识别失败: %s" % e
                self.detail = self.error
            evlog("ASR", "转写异常: %s: %s" % (type(e).__name__, e))
            return
        # 质量指标落盘：以后再"识别不准"，直接看这三个数就能判断是
        # 说得太轻(avg_logprob 低) / 根本没说话(no_speech_prob 高) / 复读(compression_ratio 高)
        qtxt = ("质量 对数概率=%s 无语音概率=%s 压缩比=%s"
                % (q.get("avg_logprob"), q.get("no_speech_prob"),
                   q.get("compression_ratio")))

        with self.lock:
            self.text = text
            self.seq += 1
            if text:
                self.state = "pending"
                self.detail = "识别到: " + text[:40]
                # 原格式串把 (耗时, 音频时长) 标成了 "音频 -> 耗时"，排查时会看反
                log("转写 音频%.2fs/耗时%.2fs: %s" % (secs, took, text))
                evlog("ASR", "转写完成 %.2fs音频/%.2fs耗时 -> %s | %s"
                      % (secs, took, text[:60], qtxt))
            elif self.mode == "ptt":
                # 按键模式下没听清就收起麦克风，等用户再点一次；
                # 绝不能回到 listening，否则环境噪声会反复触发识别，空转耗 CPU
                self.state = "paused"
                self.detail = "没听清，再说一次？"
                evlog("ASR", "转写为空(ptt) -> 收起麦克风 | %s" % qtxt)
            else:
                self.state = "listening"
                self.detail = "正在听…"
                evlog("ASR", "转写为空(vad) -> 继续听 | %s%s"
                      % (qtxt, (" 丢弃=%s" % q["dropped"]) if q.get("dropped") else ""))


def _trim_silence(audio):
    """裁掉首尾静音：前 30ms 一块地找第一块超过阈值的位置"""
    n = CHUNK
    if len(audio) < n:
        return audio
    blocks = [audio[i:i + n] for i in range(0, len(audio) - n + 1, n)]
    rms = np.array([float(np.sqrt(np.mean(b * b)) + 1e-9) for b in blocks])
    # 原实现硬编码下限 0.010，但**这只 USB 麦说话时 RMS 只有 0.006~0.019** ——
    # 低于 0.010 的整段人声会被当成静音整块裁掉，送去转写的只剩噪声尾巴，
    # 这正是"明明说了却识别出鬼话"的一部分原因。下限改为跟着本段音频走：
    # 取该段中位电平的一半，绝不高于该段最高电平的 1/4，保证真实语音一定留得下。
    peak_blk = float(np.max(rms))
    thr = min(float(np.percentile(rms, 25)) * 2.5, peak_blk * 0.25)
    thr = max(thr, float(np.median(rms)) * 0.5, 1e-6)
    idx = np.where(rms > thr)[0]
    if len(idx) == 0:
        return audio
    lo = max(0, idx[0] - 2) * n
    hi = min(len(audio), (idx[-1] + 3) * n)
    return audio[lo:hi]


SESS = Session()


def _watchdog():
    """看门狗：工作线程万一还是死了，把它拉起来；
    流被系统（设备变更/休眠）关掉时也帮忙重开。"""
    while True:
        time.sleep(5)
        try:
            with SESS.lock:
                st = SESS.state
            if st in ("listening", "thinking", "pending", "paused") and                not (SESS.worker and SESS.worker.is_alive()):
                log("看门狗：工作线程已死，重新拉起")
                SESS.gen += 1
                SESS.worker = threading.Thread(target=SESS._loop, args=(SESS.gen,), daemon=True)
                SESS.worker.start()
            if st == "listening" and SESS.stream is not None and not SESS.stream.active:
                log("看门狗：音频流被系统关了，重开")
                SESS._resume_stream()
        except Exception as e:
            log("看门狗异常: %s" % e)


# ==================== HTTP ====================
def _decode_body(handler):
    """把请求体转成 16k 单声道 float32。支持 wav / 其它容器(av) / 裸 PCM"""
    ct = (handler.headers.get("Content-Type") or "").lower()
    q = handler.path.split("?", 1)[1] if "?" in handler.path else ""
    params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
    body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))

    if params.get("pcm") == "1":                       # 裸 float32 PCM
        sr = int(params.get("sr", SR))
        a = np.frombuffer(body, dtype=np.float32)
        if sr != SR:
            n = int(round(len(a) * SR / sr))
            a = np.interp(np.linspace(0, len(a) - 1, max(n, 1)), np.arange(len(a)), a)
        return a.astype(np.float32), sr

    if "wav" in ct or body[:4] == b"RIFF":
        import io
        w = wave.open(io.BytesIO(body), "rb")
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
        if sr != SR:
            n = int(round(len(a) * SR / sr))
            a = np.interp(np.linspace(0, len(a) - 1, max(n, 1)), np.arange(len(a)), a)
        return a.astype(np.float32), sr

    # 其它容器（mp3/webm/opus/m4a…）交给 PyAV
    import av, io
    with av.open(io.BytesIO(body)) as container:
        stream = container.streams.audio[0]
        parts = []
        for frame in container.decode(stream):
            arr = frame.to_ndarray()
            if arr.ndim > 1:
                arr = arr.mean(axis=0)
            parts.append(np.asarray(arr, dtype=np.float32).reshape(-1))
        a = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        sr = stream.rate or SR
        if a.size and (np.abs(a).max() > 1.5):         # int16 量纲兜底
            a = a / 32768.0
        if sr != SR and a.size:
            n = int(round(len(a) * SR / sr))
            a = np.interp(np.linspace(0, len(a) - 1, max(n, 1)), np.arange(len(a)), a)
        return a.astype(np.float32), sr


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # 前端每 0.5s 轮询 /session/state，页面切走/重载时会直接断开，
        # 原实现没有兜住，于是每条连接中断都往 asr_err.log 打一整条 traceback
        # （实测 5.6KB 日志文件几乎全是 ConnectionAbortedError 10053）。
        # 这不是故障，静默吞掉即可，别让它淹没真正的错误。
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except OSError:
            pass

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._send({"ok": True, "model": MODEL_DIR, "loaded": ASR.model is not None,
                        "loading": ASR.loading, "error": ASR.error,
                        "load_secs": ASR.load_secs,
                        # 云端兜底的状态一眼可见：baidu_ready 为 False 时说明
                        # 没配密钥或没开通语音识别（err_no=3302），实际走的还是本地
                        "provider": PROVIDER,
                        # 火山（Agent Plan）为主力：volc_ready=False 说明没配专属 Key，
                        # 此时 auto 会直接走下一级（百度/本地 Whisper）
                        "volc_ready": ENGINE.volc.available(),
                        "volc_error": ENGINE.volc.error,
                        "volc_logid": ENGINE.volc.last_logid,
                        "volc_resource": VOLC_ASR_RES,
                        "baidu_ready": ENGINE.baidu.available(),
                        "baidu_error": ENGINE.baidu.error,
                        "last_provider": ENGINE.last_provider,
                        "device": "cpu", "compute_type": "int8"})
        elif path == "/session/state":
            with SESS.lock:
                self._send({"ok": True, "state": SESS.state, "mode": SESS.mode,
                            "recording": SESS.state == "listening",
                            "level": round(SESS.level, 3),
                            "text": SESS.text, "seq": SESS.seq,
                            "consumed_seq": SESS.consumed_seq,
                            "detail": SESS.detail, "error": SESS.error,
                            "pending": SESS.state == "pending",
                            "blocks": SESS.blocks_seen,
                            "device": SESS.device_name,
                            # ↓ VAD 内部量：排查"不知道我说完了"时看这几个
                            "thr": round(SESS.thr(), 4),
                            "raw_level": round(SESS.raw_level, 4),
                            "noise": round(SESS.noise, 4),
                            "in_speech": SESS.in_speech,
                            "silence_run": SESS.silence_run,
                            "speech_run": SESS.speech_run})
        else:
            self._send({"ok": False, "error": "unknown path"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/transcribe":
                audio, sr = _decode_body(self)
                text, secs, took, q = ENGINE.transcribe(audio)
                self._send({"ok": True, "text": text, "seconds": secs,
                            "elapsed": took, "sr": sr, "quality": q})
            elif path == "/session/start":
                body = self._read_json()
                ok, msg = SESS.start(body.get("mode", "ptt"))
                self._send({"ok": ok, "msg": msg})
            elif path == "/session/stop":
                ok, msg = SESS.stop()
                self._send({"ok": ok, "msg": msg})
            elif path == "/session/finish":
                # vad 模式："我说完了" -> 结束当前这句并转写（不丢弃）
                ok, msg = SESS.finish()
                self._send({"ok": ok, "msg": msg})
            elif path == "/session/cancel":
                ok, msg = SESS.cancel()
                self._send({"ok": ok, "msg": msg})
            elif path == "/session/consume":
                ok, text, msg = SESS.consume()
                self._send({"ok": ok, "text": text, "msg": msg})
            elif path == "/session/resume":
                ok, msg = SESS.resume()
                self._send({"ok": ok, "msg": msg})
            else:
                self._send({"ok": False, "error": "unknown path"}, 404)
        except Exception as e:
            self._send({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)


class Server(ThreadingHTTPServer):
    # 端口独占：已被占用就直接退出报错，绝不允许两个实例轮流应答（会读到旧代码的行为）
    allow_reuse_address = False
    daemon_threads = True


def main():
    log("ASR 服务启动 http://%s:%d  模型=%s" % (HOST, PORT, MODEL_DIR))
    threading.Thread(target=ASR.ensure, daemon=True).start()   # 预热，免得第一句等加载
    threading.Thread(target=_watchdog, daemon=True).start()
    try:
        Server((HOST, PORT), Handler).serve_forever()
    except OSError as e:
        log("端口 %d 绑定失败（可能已有一个实例在跑）: %s" % (PORT, e))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
