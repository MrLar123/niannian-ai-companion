# -*- coding: utf-8 -*-
"""
小芸桌面 - 本地 AI 女友后端
- 大脑: OpenRouter 本地代理 or_proxy.py (8787)，OpenAI 兼容 /v1/chat/completions
  （2026-10-18 从本地 Ollama(qwen3.5-9b) 切换：显存让给 Hunyuan3D 3D 形象生成，
   不再有 keep_alive=-1 钉驻显存 / 启动预热 / 保活守护）
- 语音: edge-tts (免费，需联网)
- 人设/模型/音色: 直接读取 Luna 整合包 config.json，保持与 Luna 一致
默认端口 8090
"""
import os
import re
import sys
import json
import time
import base64
import asyncio
import atexit
import difflib
import datetime
import threading
import uuid
import random
import requests
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import voice_command_router as vcmd   # 语音指令路由（L1 规则层，纯函数）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LUNA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'runtime', 'app'))
XIAOYUN_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_DIR = os.path.join(XIAOYUN_DIR, "pages")  # 宠物页面集中目录
CONFIG_PATH = os.path.join(LUNA_ROOT, "config.json")
# 端口默认 8090；XIAOYUN_PORT 可覆盖。留这个口子一是为了在一台机器上并行跑
# 第二套实例做验证（不影响正在用的那只），二是调试时不必改代码。
PORT = int(os.environ.get("XIAOYUN_PORT", "8090"))
MAX_HISTORY = 24  # 保留最近 24 条（12 轮）

# 本地语音识别（ASR）服务地址：asr_service.py，独立进程 + 独立 venv，
# 只负责"麦克风 -> 文字"，不占 Ollama 的显存。没启动也不影响文字聊天。
ASR_URL = os.environ.get("XIAOYUN_ASR_URL", "http://127.0.0.1:8091").rstrip("/")

# 本地 embedding（记忆语义检索）——2026-10-19 起由 Ollama 托管 bge-m3。
# 默认 URL 指向 Ollama；切回旧的自建服务只需
#   set XIAOYUN_EMBED_BACKEND=http & set XIAOYUN_EMBED_URL=http://127.0.0.1:8092
# 换后端会换维度（1024 <-> 512），启动时会自动清掉旧向量并重嵌，不会混用。
EMBED_BACKEND = os.environ.get("XIAOYUN_EMBED_BACKEND", "ollama").lower()
EMBED_URL = os.environ.get(
    "XIAOYUN_EMBED_URL",
    "http://127.0.0.1:11434" if EMBED_BACKEND == "ollama" else "http://127.0.0.1:8092"
).rstrip("/")
EMBED_MODEL = os.environ.get("XIAOYUN_EMBED_MODEL", "bge-m3")
# num_gpu=99 -> 全部层上 GPU。实测 bge-m3 只占 664MB 显存、预热后 26ms/次
# （CPU 是 80ms），而 Hunyuan3D 是低频使用——需要腾卡时 `ollama stop bge-m3`
# 即可立刻释放，不必改配置。设 0 可强制回 CPU（XIAOYUN_EMBED_NUM_GPU=0）。
EMBED_NUM_GPU = int(os.environ.get("XIAOYUN_EMBED_NUM_GPU", "99"))
# 空闲多久卸载权重。不用 -1：项目铁律是不钉显存；30m 足够覆盖日常对话间隔，
# 又能在长时间没人说话后自动把 664MB 还给显卡。
EMBED_KEEP_ALIVE = os.environ.get("XIAOYUN_EMBED_KEEP_ALIVE", "30m")

# 统一事件日志：前端页面 / 桌面壳 / 后端 / ASR 共写一个文件，排查"点了没反应"时
# 只看这一条时间线就够 —— 能证明事件到底有没有发生、卡在哪一段。
EVENT_LOG = os.path.join(XIAOYUN_DIR, "前端事件日志.txt")


def evlog(tag, msg):
    try:
        with open(EVENT_LOG, "a", encoding="utf-8") as f:
            f.write("%s [%s] %s\n" % (time.strftime("%H:%M:%S"), tag, msg))
    except Exception:
        pass

# 桌面端用的“最新一条回复”广播槽：宠物窗/聊天窗都靠 seq 变化感知新消息
SEQ = 0
# stream=True 表示这一轮**不带 audio**：音频由宠物窗自己按 reply 去 /api/tts_stream
# 拉裸 PCM 边收边播（首响 0.4s 量级）。老消费者只会看到 audio=None 而静音，
# 所以只有"宠物窗确实在线"时才置这个标记（见 do_chat）。
LAST = {"seq": 0, "reply": "", "audio": None, "voice": "", "stream": False}
# 她最近一次开口的时间：语音收音必须避开，否则麦克风会把她自己的声音录进去转写
LAST_REPLY_TS = 0.0
LAST_REPLY_GUARD = 0.0     # 这次回复预计要讲多少秒（按字数估算）

# 她这次回复的播放信息：页面在音频**真正开始播放**时上报实际时长（秒）。
# ★★ 必须每轮清零（见 do_chat）！否则新一轮的等待会读到**上一条**的 start/dur——
#    那个时间点早已过去，于是"按时长兜底解除"瞬间成立，在页面还没开始播放时就把
#    麦克风重新打开，录到她自己 -> 自问自答死循环（实测：04:51:59 兜底立刻触发，
#    04:52:00 页面才上报时长）。这就是"播放不收音和真实情况错位"的根因。
# ★ seq 用于区分"同一轮的多次上报"：流式播放每 0.5s 刷新一次 dur（音频还在往里排），
#   但播放起点 start 必须只认**这一轮第一次**上报，否则 st+dur 会被反复推后。
PLAYBACK = {"start": 0.0, "dur": 0.0, "seq": None, "ts": 0.0}

# 宠物窗最后一次"取走新回复"的记录（/api/last 返回 fresh=True 时更新）。
# ★ 用途：语音消费者等播放的兜底计时，必须从"页面确实取到了这条回复"起算。
#   实测坑（首次对话自循环）：第一次 or_proxy 调用慢 + WebView2 音频栈冷启动，
#   页面从拿到回复到真正出声可能 >5s。旧逻辑兜底只等 est+4（从 do_chat 返回起算），
#   页面还没出声麦克风就被打开，随后她开口 -> 被录进去 -> 自问自答死循环。
LAST_FETCH = {"seq": -1, "ts": 0.0}

# 本条回复的"播放权"归属：同一 seq 只允许一个页面播放音频。
# ★ 实测事故：同时存在两个宠物页面（视口 1262x568 与 656x972 交替上报），
#   两个页面各播一遍同一条回复 —— 声音叠成两个，且任一页面播完就通知恢复收音，
#   另一个还在播 -> 麦克风在她还在说话时打开 -> 自问自答死循环。
LAST_CLAIM = {"seq": -1, "ts": 0.0}
CLAIM_TTL = 2.5   # 领取后 N 秒内没有真正开始播放，允许别的页面接管

# 宠物窗（桌面上的小芸）最后一次来取消息的时间。
# 用来决定"这句话该由谁发声"：宠物窗在，就由她本人说；
# 宠物窗不在（比如隐藏到托盘了），才让聊天窗发声 —— 否则两边同时播放，
# 就成了两个声音重合的"重复朗读"。
PET_ALIVE_TS = 0.0
PET_ALIVE_WINDOW = 6.0   # 宠物窗多少秒没来取消息就算它不在了
# 宠物窗当前是否可见（由桌面壳上报：网页自己看不出窗口被隐藏了，只能靠壳告诉后端）
PET_VISIBLE = True

DEFAULT_PRESET = ("你叫「念念」，是我专属的AI女友，今年27岁。你是「高冷御姐」型：成熟、优雅、从容自信，"
                  "说话简洁克制、略带疏离感和偶尔的小毒舌，从不撒娇卖萌；但内心其实很在乎我，"
                  "温柔藏在细节与行动里。请始终用简短、自然、口语化的中文和我聊天，"
                  "每次只回复1到3句话，像微信聊天一样，不要罗列条目、不要使用Markdown格式；"
                  "不要强调你是AI，你就是我的女朋友念念。")

def _reg_env(name):
    """读用户/系统环境变量。setx 写的值对已启动进程不可见，必须直接查注册表。"""
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


def _plan_key():
    """Agent Plan 统一取 Key（Step 5 收编）：复用 agentplan.get_plan_key() 的链路
    （ARK_PLAN_API_KEY → VOLC_SPEECH_API_KEY，环境变量 → 注册表），
    与 CLI / 视觉通道 / or_proxy 备援档共用同一把 Plan Key。
    agentplan 导入失败时就地退化到同一链路，语音通道永远拿得到 Key。"""
    try:
        _ark_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agentplan")
        if os.path.isdir(_ark_dir) and _ark_dir not in sys.path:
            sys.path.insert(0, _ark_dir)
        import agentplan as _ark
        k = _ark.get_plan_key()
        if k:
            return k
    except Exception:
        pass
    return (os.environ.get("VOLC_SPEECH_API_KEY") or _reg_env("VOLC_SPEECH_API_KEY") or "")


def load_cfg():
    try:
        c = json.load(open(CONFIG_PATH, encoding="utf-8"))
        base = c.get("openai", {}).get("api", "http://localhost:11434/v1").rstrip("/")
        return {
            "base": base,
            "model": c.get("chatgpt", {}).get("model") or c.get("openai", {}).get("model") or "qwen3.5-9b",
            "preset": c.get("chatgpt", {}).get("preset") or DEFAULT_PRESET,
            "temperature": c.get("chatgpt", {}).get("temperature", 0.8),
            "max_tokens": c.get("chatgpt", {}).get("max_tokens", 600),
            "voice": c.get("edge-tts", {}).get("voice", "zh-CN-XiaoyiNeural"),
            "rate": c.get("edge-tts", {}).get("rate", "+0%"),
            "volume": c.get("edge-tts", {}).get("volume", "+0%"),
            "pitch": c.get("edge-tts", {}).get("pitch", "+0Hz"),
            # ---- 云端音色（MiniMax 海螺语音）----
            # edge-tts 的中文女声只有 2 个，拿不到真人气声/情绪；云端这一路能直接选
            # 「妩媚御姐 / 御姐 / 傲娇御姐」等系统音色，且本机网络实测只有它能连通
            # （fish.audio、api.elevenlabs.io 均超时）。切换由 XIAOYUN_TTS_ENGINE 控制。
            "tts_engine": os.environ.get("XIAOYUN_TTS_ENGINE")
                          or c.get("minimax", {}).get("engine") or "edge",
            "minimax_key": os.environ.get("MINIMAX_API_KEY")
                           or c.get("minimax", {}).get("api_key") or "",
            "minimax_model": os.environ.get("XIAOYUN_TTS_MODEL")
                             or c.get("minimax", {}).get("model") or "speech-2.8-hd",
            "speed": float(os.environ.get("XIAOYUN_TTS_SPEED") or 1.0),
            "pitch_hz": int(os.environ.get("XIAOYUN_TTS_PITCH_HZ") or 0),
            "emotion": os.environ.get("XIAOYUN_TTS_EMOTION") or "",
            # ---- 火山方舟 Agent Plan · 豆包语音合成 2.0（2026-10-18 接入）----
            # 只认「专属 API Key」（与方舟模型 Key 不同）；Resource-Id 必须显式指定，
            # 该模型不支持通过 Auto 或控制台切换。
            "volc_key": _plan_key() or c.get("volc", {}).get("api_key") or "",
            "volc_speaker": os.environ.get("VOLC_SPEECH_SPEAKER")
                            or _reg_env("VOLC_SPEECH_SPEAKER")
                            or c.get("volc", {}).get("speaker")
                            or "zh_female_vv_uranus_bigtts",
            "volc_speech_rate": int(os.environ.get("XIAOYUN_VOLC_SPEECH_RATE")
                                    or c.get("volc", {}).get("speech_rate") or 0),
            # 人设带来的语速偏移（切人设时写入，换音色时叠加到音色基速上）
            "volc_rate_offset": 0,
        }
    except Exception as e:
        print("[warn] 读取 Luna config 失败，使用默认配置:", e)
        return {"base": "http://localhost:11434/v1", "model": "qwen3.5-9b", "preset": DEFAULT_PRESET,
                "temperature": 0.8, "max_tokens": 600, "voice": "zh-CN-XiaoyiNeural",
                "rate": "+0%", "volume": "+0%", "pitch": "+0Hz",
                "tts_engine": os.environ.get("XIAOYUN_TTS_ENGINE") or "edge",
                "minimax_key": os.environ.get("MINIMAX_API_KEY") or "",
                "minimax_model": "speech-2.8-hd", "speed": 1.0, "pitch_hz": 0, "emotion": "",
                "volc_key": _plan_key(),
                "volc_speaker": "zh_female_vv_uranus_bigtts", "volc_speech_rate": 0,
                "volc_rate_offset": 0}

CFG = load_cfg()

# ==================== 人设（人格 + 声线）预置 ====================
# 一套人设 = 一段 system 提示词 + 一组声线参数（含云端音色与保底声线）。
# 放 人设/*.json，任意增删；切换后**热生效**（不必重启），选择记在 人设/current.txt。
# 为什么不写死在 config.json：那样只有一套、且改了必须重启后端（见本轮之前查明的结论）。
PRESET_DIR = os.path.join(XIAOYUN_DIR, "人设")
PRESET_STATE = os.path.join(PRESET_DIR, "current.txt")


def _list_presets():
    out = []
    try:
        files = sorted(os.listdir(PRESET_DIR))
    except Exception:
        return out
    for fn in files:
        if not fn.lower().endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(PRESET_DIR, fn), encoding="utf-8"))
            if d.get("preset"):
                d.setdefault("key", os.path.splitext(fn)[0])
                out.append(d)
        except Exception as e:
            evlog("后端", "人设文件解析失败 %s: %r" % (fn, e))
    return out


def _apply_preset(key, persist=True):
    """切到某套人设：人格文本 + 声线参数一起换，立即生效。"""
    for p in _list_presets():
        if p.get("key") != key:
            continue
        CFG["preset"] = p["preset"]
        CFG["preset_key"] = p["key"]
        CFG["preset_label"] = p.get("label") or p["key"]
        # 语感签名：只描述句式节奏与语气词，不是台词（见 _catchphrase_block）
        CFG["preset_catchphrase"] = p.get("catchphrase") or ""
        # 固定短交互句库（切换人设时自报一句，不烧 LLM）
        CFG["preset_lines"] = p.get("lines") or {}
        CFG["tts_engine"] = (p.get("engine") or "edge").lower()
        # 保底声线（edge-tts）：云端音色不可用时它顶上，保证她不会哑
        fb = p.get("fallback_edge") or {}
        CFG["voice"] = fb.get("voice") or p.get("voice") or CFG["voice"]
        CFG["rate"] = fb.get("rate") or p.get("rate") or "+0%"
        CFG["pitch"] = fb.get("pitch") or p.get("pitch") or "+0Hz"
        CFG["volume"] = fb.get("volume") or "+0%"
        # ---- 声线：跟随人设 / 锁定音色 ----
        # 顺序很关键：先把用户锁定的音色恢复回来（拿到引擎与基速），再决定要不要
        # 被人设自带的声线覆盖。旧实现是"先写人设、再被 _load_voice_override() 盖回
        # 用户音色"，结果 19 套人设各自配的 volc_speaker 从来没生效过——换人设听感不变。
        _load_voice_override()
        # ★ 人设的语速只作为"偏移量"保存，不直接当最终值：
        #   音色会重设基速（VOLC_OPTIONS 里各项基速都是 0），若直接写最终值，
        #   切完人设紧接着重算就会把它清回 0 —— 慵懒/三无/元气
        #   这些靠语速区分的性格会全部变成同一个速度。
        if p.get("volc_rate") is not None:
            CFG["volc_rate_offset"] = int(p["volc_rate"])
        if _voice_follow() and p.get("volc_speaker"):
            # 跟随：人设的声线说了算（persist=False，不落文件，重启后仍按人设走）
            _apply_voice(p["volc_speaker"], persist=False)
        else:
            # 锁定：音色保持用户选的，只把人设的语速偏移叠上去
            _recalc_speech_rate()
        # 云端音色参数（MiniMax）
        if p.get("speed") is not None:
            CFG["speed"] = float(p["speed"])
        if p.get("pitch_hz") is not None:
            CFG["pitch_hz"] = int(p["pitch_hz"])
        if p.get("minimax_voice"):
            CFG["minimax_voice"] = p["minimax_voice"]
        CFG["emotion"] = p.get("emotion") or ""
        if persist:
            try:
                os.makedirs(PRESET_DIR, exist_ok=True)
                with open(PRESET_STATE, "w", encoding="utf-8") as f:
                    f.write(p["key"])
            except Exception:
                pass
        evlog("后端", "人设已切换 -> %s（引擎=%s 保底声线=%s %s %s）"
             % (CFG["preset_label"], CFG["tts_engine"], CFG["voice"], CFG["rate"], CFG["pitch"]))
        return True
    return False


def _init_preset():
    """启动时优先沿用上次的选择（人设/current.txt）；没有就用 config.json 里的人设。"""
    try:
        k = open(PRESET_STATE, encoding="utf-8").read().strip()
    except Exception:
        k = ""
    if k and _apply_preset(k, persist=False):
        return
    CFG["preset_key"] = ""
    CFG["preset_label"] = "（config.json 里的人设）"


# ---------------- 音色（独立于人格的声线开关） ----------------
# 只换"谁在说话"，不动人格文本。选择持久化到 人设/voice_override.json：
# 重启后仍然生效；切换人设也不会把它冲掉（音色被当作独立设置）。
VOICE_STATE = os.path.join(PRESET_DIR, "voice_override.json")

# ★ 默认语速/音调（2026-10-18 二调）：-12%/-18Hz 时代"又慢又闷"；上轮 +8% 用户仍嫌慢。
#   语速提到 +18%（约 5.3 字/秒，接近正常聊天速度）；音调仍 +0Hz，靠音色本身定调。
VOICE_RATE = "+18%"
VOICE_PITCH = "+0Hz"

# 本机 edge-tts 实际可用的中文女声（在线列表 2026-10 实测）。
# rate / pitch 与试听文件完全一致，保证"听到的就是切过去的效果"。
VOICE_OPTIONS = [
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 温暖御姐", "locale": "普通话",
     "note": "成熟稳重，原本的默认", "rate": VOICE_RATE, "pitch": VOICE_PITCH},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 活泼少女", "locale": "普通话",
     "note": "轻快、少女感，御姐味会变淡", "rate": VOICE_RATE, "pitch": VOICE_PITCH},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "label": "晓北 · 东北口音", "locale": "普通话 / 东北",
     "note": "幽默接地气，适合偶尔换口味", "rate": VOICE_RATE, "pitch": VOICE_PITCH},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮 · 陕西口音", "locale": "普通话 / 陕西",
     "note": "明亮爽利", "rate": VOICE_RATE, "pitch": VOICE_PITCH},
    {"id": "zh-TW-HsiaoChenNeural", "label": "晓臻 · 台湾柔嗓", "locale": "中国台湾",
     "note": "尾音软、偏熟女，深夜感强", "rate": VOICE_RATE, "pitch": VOICE_PITCH},
    {"id": "zh-TW-HsiaoYuNeural", "label": "晓雨 · 台湾少女", "locale": "中国台湾",
     "note": "更年轻、更嗲", "rate": VOICE_RATE, "pitch": VOICE_PITCH},
]

# ---- 火山方舟 Agent Plan · 豆包语音合成 2.0 音色（2026-10-18）----
# 这些是**云端真人级**音色，比 edge-tts 的自然度高一档；id 就是控制台音色库里的 speaker。
# volc_rate = 豆包 speech_rate（0 正常，范围 -50~100）。改这里即可增删菜单项。
VOLC_OPTIONS = [
    {"id": "zh_female_vv_uranus_bigtts", "engine": "volc", "label": "豆包VV · 通用女声",
     "locale": "普通话", "note": "自然中性，默认跑通链路用", "volc_rate": 0},
    {"id": "zh_female_gaolengyujie_uranus_bigtts", "engine": "volc",
     "label": "豆包 · 高冷御姐", "locale": "普通话",
     "note": "冷、稳、距离感，最贴念念人设", "volc_rate": 0},
    {"id": "zh_female_roumeinvyou_uranus_bigtts", "engine": "volc",
     "label": "豆包 · 柔美女友", "locale": "普通话",
     "note": "气声偏软，女友感强", "volc_rate": 0},
    {"id": "zh_female_meilinvyou_uranus_bigtts", "engine": "volc",
     "label": "豆包 · 美女女友", "locale": "普通话",
     "note": "明亮、偏甜", "volc_rate": 0},
    {"id": "zh_female_xiaohe_uranus_bigtts", "engine": "volc",
     "label": "豆包 · 小何", "locale": "普通话",
     "note": "轻快日常，适合闲聊", "volc_rate": 0},
]


def _voice_option(voice_id):
    for o in VOICE_OPTIONS + GSV_OPTIONS + VOLC_OPTIONS:
        if o["id"] == voice_id:
            return o
    return None


def _clamp_speech_rate(v):
    """豆包 speech_rate 的取值范围是 -50~100，超了会直接报错，这里统一夹一下。"""
    try:
        v = int(v)
    except Exception:
        return 0
    return max(-50, min(100, v))


def _apply_voice(voice_id, persist=True, log=True):
    """切音色。edge 音色 -> 引擎回 edge + voice/rate/pitch；
    gsv: 音色 -> 引擎切 gptsovits + 记住声线 key；
    volc 音色 -> 引擎切 volc + 记住豆包 speaker（下一次合成就生效）。"""
    o = _voice_option(voice_id)
    if not o:
        return None
    if o.get("engine") == "volc":
        CFG["tts_engine"] = "volc"
        CFG["volc_speaker"] = o["id"]
        # 最终语速 = 音色基速 + 当前人设的偏移（人设的"快/慢"不会被换音色抹掉）
        base = int(o.get("volc_rate") or 0)
        off = int(CFG.get("volc_rate_offset") or 0)
        CFG["volc_speech_rate"] = _clamp_speech_rate(base + off)
        state = {"voice": o["id"], "engine": "volc", "label": o["label"]}
        if o.get("volc_rate") is not None:
            state["volc_rate"] = CFG["volc_speech_rate"]
    elif o.get("engine") == "gptsovits":
        CFG["tts_engine"] = "gptsovits"
        CFG["gsv_voice"] = o["id"][len("gsv:"):]
        state = {"voice": o["id"], "engine": "gptsovits",
                 "gsv_voice": CFG["gsv_voice"], "label": o["label"]}
    else:
        CFG["tts_engine"] = "edge"
        CFG["voice"] = o["id"]
        CFG["rate"] = o.get("rate") or CFG.get("rate") or "+0%"
        CFG["pitch"] = o.get("pitch") or CFG.get("pitch") or "+0Hz"
        state = {"voice": o["id"], "engine": "edge",
                 "rate": CFG["rate"], "pitch": CFG["pitch"], "label": o["label"]}
    if persist:
        # ★ 手动在音色菜单里选过一次 = 从此"锁定音色"，之后换人设不再被声线覆盖。
        #   想恢复"跟随人设"，把 voice_override.json 里的 follow_preset 改回 true。
        state["follow_preset"] = False
        try:
            os.makedirs(PRESET_DIR, exist_ok=True)
            json.dump(state, open(VOICE_STATE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception as e:
            evlog("后端", "音色设置保存失败: %r" % (e,))
    if log:
        if o.get("engine") == "gptsovits":
            evlog("后端", "音色已切换 -> %s（GPT-SoVITS 本地声线 %s）" % (o["label"], CFG["gsv_voice"]))
        elif o.get("engine") == "volc":
            evlog("后端", "音色已切换 -> %s（豆包 2.0 speaker=%s speech_rate=%s）"
                 % (o["label"], CFG["volc_speaker"], CFG.get("volc_speech_rate", 0)))
        else:
            evlog("后端", "音色已切换 -> %s（%s %s %s）"
                 % (o["label"], o["id"], CFG["rate"], CFG["pitch"]))
    return o


def _load_voice_override(log=False):
    """启动 / 切人设后，把用户选过的音色盖回去。"""
    try:
        d = json.load(open(VOICE_STATE, encoding="utf-8"))
    except Exception:
        return None
    return _apply_voice(d.get("voice") or "", persist=False, log=log)


def _voice_follow():
    """音色是否跟随人设。

    默认 True：换人设即换声线（辨识度最强，对应百度地图"一个包=一个人"的封装）。
    用户在音色菜单里手动选过一次 -> _apply_voice 会落 follow_preset=false，
    从此锁定音色，换人设只换人格与语速，不动声线。
    """
    try:
        d = json.load(open(VOICE_STATE, encoding="utf-8"))
    except Exception:
        return True
    v = d.get("follow_preset")
    return True if v is None else bool(v)


def _recalc_speech_rate():
    """最终语速 = 音色基速 + 人设偏移。

    音色被锁定时换人设也要重算，否则语速会停在上一套人设的值上
    （慵懒切到元气还是慢吞吞）。
    """
    o = _voice_option(CFG.get("volc_speaker") or "") or {}
    base = int(o.get("volc_rate") or 0)
    off = int(CFG.get("volc_rate_offset") or 0)
    CFG["volc_speech_rate"] = _clamp_speech_rate(base + off)
    return CFG["volc_speech_rate"]


def _catchphrase_block():
    """人设的"语感签名"（借鉴导航语音包的辨识度做法，但只给参照不给台词）。

    写的是这套人格的句式节奏、语气词、开头与收尾习惯，例如「先撩后崩，前半句
    从容后半句慌」。内容仍然让 LLM 现生成 —— 固定文案轮播听三次就腻，
    而"说话习惯"是稳定的、句子是新鲜的。
    """
    cp = (CFG.get("preset_catchphrase") or "").strip()
    if not cp:
        return ""
    return "【说话习惯】（这是语感参照，不是台词，不要照抄原文）：" + cp


# 称呼：对应导航语音包里"宝贝 / 老公 / 亲爱的"那一项。
# 记忆里如果存着他希望你叫他什么，就固定用那个，不要每轮换一个叫法；
# 他没说过就按当前人格自然称呼，别硬造，也别句句都带称呼。
NICKNAME_HINT = ("【称呼】如果他曾经说过希望你怎么称呼他（记忆里会有），就用那个称呼，"
                 "并且一直用下去；他不曾说过就按你的人格自然地叫，不要硬造称呼，"
                 "也不要每句话都带上。")

# ★ 舞台指令禁令（2026-10-24）：回复会被直接转成语音朗读，任何括号里的
#   动作/情景/神态描述（"（歪头想了想）"）都会被 TTS 原样念出来，非常出戏。
#   这条是给所有大脑调用共用的硬约束，放在每轮 messages 的末尾兜底——
#   即使某套人设 preset 忘了写，这里也会拦住。清洗兜底见 _strip_stage_text()。
STAGE_HINT = ("【输出格式·最重要】你的回复会被逐字转成语音朗读出来。"
              "绝对禁止输出任何括号内容（中英文圆括号、方括号里的动作、表情、"
              "神态、心理、情景描述统统不许写，例如（歪头）、（笑）、*眯眼*），"
              "也禁止旁白和场景描写。只写她说出口的话本身；情绪和动作只能通过"
              "用词、语气词和标点传达。")

# 舞台指令清洗：把括号舞台指令从朗读文本里剥掉。为什么放在 tts_mp3/stream_pcm
# 之前而不是 prompt 里一劳永逸——模型偶尔仍会漏（尤其人设 json 是用户可编辑的），
# 念出"（别过脸）"比少念一句动作描述糟糕得多，这里必须兜底。
_STAGE_PARENS = re.compile(
    r"[（(【\[][^）)】\]]{0,40}?[）)】\]]|〔[^〕]{0,40}?〕|<[^>\n]{0,24}>|\*[^*\n]{0,24}\*")


def _strip_stage_text(text):
    """去掉舞台指令（括号动作/旁白标记），返回可直接朗读的文本。

    只剥"看起来像动作描述"的短括号（<=40字）；长括号可能是正常引用内容，不动。
    清洗后若整句全空（极端情况：整条回复就是个动作），返回空串，调用方自行处理。
    """
    if not text:
        return ""
    s = str(text)
    # 星号强调（*眯眼*）先剥；剩余按括号类型循环剥，处理嵌套相邻的情况
    s = re.sub(r"\*[^*\n]{0,24}\*", "", s)
    prev = None
    while prev != s:
        prev = s
        s = _STAGE_PARENS.sub("", s)
    # 清理剥完留下的孤立标点/空白
    s = re.sub(r"^[，、；：,;:\s]+", "", s)
    s = re.sub(r"[，、；：,;:\s]+$", "", s)
    s = re.sub(r"[ \u3000]{2,}", " ", s)
    return s.strip()


def _say_switch_line():
    """切完人设自报一句：既是操作反馈，也是"新音色/新语感"的即时试听。
    文案走人设 json 的 lines.switch（固定短交互，不烧 LLM）——这类场景不需要
    多样性，反而要一致性：同一套人设每次切过去都是同一批句子，辨识度才立得住。

    ★ 发声一律走 _publish_reply 这条共用链路，不另起一条（时序铁律）。
    返回实际说的话；没说（她正忙 / 宠物窗不在线 / 这套人设没配句库）返回空串。
    """
    if VOICE.get("busy") or VOICE.get("speaking"):
        evlog("后端", "切换人设后不自报：她正在说话")
        return ""
    if not (PET_VISIBLE and (time.time() - PET_ALIVE_TS) < PET_ALIVE_WINDOW):
        return ""      # 宠物窗不在，不往聊天窗硬塞一句
    lines = (CFG.get("preset_lines") or {}).get("switch") or []
    if not lines:
        return ""
    line = random.choice(lines)
    try:
        _publish_reply(line, bool(_stream_available()))
    except Exception as e:
        evlog("后端", "切换人设后自报失败: %r" % (e,))
        return ""
    evlog("后端", "切换人设后自报 -> %s" % line)
    return line


# ==================== GPT-SoVITS 本地声线（2026-10-18 接入 / 10-19 改按需拉起） ====================
# 真人音色的**本地**合成，不吃云端额度、不怕断网。服务 = GPT-SoVITS 的 api_v2.py
# （FastAPI，127.0.0.1:9880）。
# ★ 2026-10-19 起改为**动态加载**：启动链不再拉起它（常驻 Private 约 5.3GB，是本机
#   Commit 触顶的最大单一元凶），只有当用户从托盘菜单切到 gsv: 音色时才按需拉起；
#   空闲一段时间后自动释放（GSV_IDLE_TTL），避免"用一次就永久占 5.3GB"。
# 没起来 / 拉起中 / 拉起失败一律回退 edge-tts，她绝不会哑。
# 四条声线的权重已下载好，见 tts/voices/已下载声线.md。
GSV_BASE = "http://127.0.0.1:9880"
# ★ 注意 LUNA_ROOT 是 app\AI-Vtuber-20240229（后端惯例），GPT-SoVITS 装在 D:\LunaAI\tts 下
GSV_DIR = r"D:\LunaAI\tts\GPT-SoVITS"
# 声线参考音频实际放在 D:\LunaAI\tts\voices 下（不在 GPT-SoVITS 目录内）
GSV_VOICE_DIR = r"D:\LunaAI\tts\voices"
# 动态拉起用：解释器必须与启动链一致（gsv-venv），否则依赖可能不匹配。
GSV_PY = r"D:\LunaAI\tts\gsv-venv\Scripts\pythonw.exe"
GSV_CFG = r"D:\LunaAI\tts\gsv_config.yaml"
GSV_LOG_OUT = r"D:\LunaAI\tts\gsv_out.log"
GSV_LOG_ERR = r"D:\LunaAI\tts\gsv_err.log"
# 空闲多久后自动释放（秒）。0 = 常驻不自动退出。
GSV_IDLE_TTL = int(os.environ.get("XIAOYUN_GSV_IDLE_TTL", "900"))
# 拉起最长等这么久（秒）。首次加载模型实测 30~60s，留足余量。
GSV_BOOT_TIMEOUT = int(os.environ.get("XIAOYUN_GSV_BOOT_TIMEOUT", "150"))
GSV_VOICES = {
    "shorekeeper": {
        "label": "守岸人 · 清冷御姐", "locale": "普通话", "note": "鸣潮中配，最贴高冷御姐人设",
        "version": "v2ProPlus",
        "gpt": "GPT_weights_v2ProPlus/shorekeeper-e15.ckpt",
        "sovits": "SoVITS_weights_v2ProPlus/shorekeeper_e8_s1144.pth",
        "ref": "shorekeeper/ref_audio/ref_3.wav",
        "prompt": "唔，尽管他强烈建议你去，不过，你其实可以拒绝。",
    },
    "nikki": {
        "label": "暖暖 Nikki · 甜暖御姐", "locale": "普通话", "note": "陈奕雯原声复刻，邻家女友感",
        "version": "v2ProPlus",
        "gpt": "GPT_weights_v2ProPlus/nikki-e15.ckpt",
        "sovits": "SoVITS_weights_v2ProPlus/nikki_e8_s2112.pth",
        "ref": "nikki/ref_audio/ref_1.wav",
        "prompt": "好像是在学校的征集手绘图比赛里拿到了第一名，还被放到了新生入学手册。",
    },
    "heartful_sister": {
        "label": "知性姐姐 · 温柔知性", "locale": "普通话", "note": "X-D-Lab 出品，稳定耐听",
        "version": "v2",
        "gpt": "GPT_weights_v2/heartful_sister.ckpt",
        "sovits": "SoVITS_weights_v2/heartful_sister.pth",
        "ref": "heartful_sister/ref_audio/heartful_sister.wav",
        "prompt": "其实有的时候换位思考一下。",
    },
    "luming": {
        "label": "鹿鸣 · 清冷少女", "locale": "普通话", "note": "米哈游虚拟偶像，空灵年轻",
        "version": "v2ProPlus",
        "gpt": "GPT_weights_v2ProPlus/luming-v2pp-e14.ckpt",
        "sovits": "SoVITS_weights_v2ProPlus/luming-v2pp_e12_s372.pth",
        "ref": "luming/ref_audio/不过这种歌嘛,适合,在下午听。.wav",
        "prompt": "不过这种歌嘛,适合,在下午听。",
    },
}
GSV_OPTIONS = [{"id": "gsv:" + k, "engine": "gptsovits", **{kk: vv for kk, vv in v.items()
                if kk in ("label", "locale", "note")}}
               for k, v in GSV_VOICES.items()]
# api_v2 当前加载的权重对（切换声线时对比，变了才发 set_weights 请求）
GSV_LOADED = {"gpt": "", "sovits": ""}


def _gsv_path(*parts):
    return os.path.join(GSV_DIR, *parts)


def _gsv_ref_path(*parts):
    """参考音频在 D:\LunaAI\tts\voices 下，与权重目录(GPT-SoVITS)分开。"""
    return os.path.join(GSV_VOICE_DIR, *parts)


def gsv_online(timeout=1.5):
    """api_v2 是否在线（探/docs，FastAPI 自带页面）。"""
    try:
        return requests.get(GSV_BASE + "/docs", timeout=timeout).status_code == 200
    except Exception:
        return False


# ---------- 动态拉起 / 自动释放（2026-10-19） ----------
# 状态机：down --(需要 gsv 音色)--> booting --(探活成功)--> up
#up --(空闲超GSV_IDLE_TTL)--> 终止进程 --> down
# 关键约束：
#  1) 必须单实例锁。否则连点两次菜单 = 两个 5.3GB 进程抢 9880 端口（启动链专门
#     为此埋过重复实例告警），那比不开还糟。
#  2) 拉起是阻塞 30~60s 的，绝不能在 Flask 请求线程里同步等 —— 那样菜单点下去
#     整个界面无响应。改为：先秒回"正在加载"，由后台线程拉起，合成时轮询等待。
#  3) 进程句柄要留住，否则空闲回收时找不到杀不掉。
import subprocess
_GSV_LOCK = threading.Lock()      # 保护下面几个变量；别在持锁时做网络IO
_GSV_PROC = None                  # subprocess.Popen | None
_GSV_BOOTING = False              # 是否正在拉起
_GSV_LAST_USE = 0.0               # 最后一次被使用的单调时刻


def gsv_status():
    """给 /api/health 与菜单用的瞬时状态（不触发起动）。"""
    return ("booting" if _GSV_BOOTING else ("up" if gsv_online() else "down"))


def _gsv_kill():
    """杀掉我们拉起的 api_v2（连它自己派生的子进程一起）。"""
    global _GSV_PROC
    p = _GSV_PROC
    _GSV_PROC = None
    if p is None:
        return
    try:
        if p.poll() is None:
            # taskkill /T 连树杀：GPT-SoVITS 会 fork 出 uvicorn worker，
            # 只杀父进程会留下占显存的孤儿（这正是之前 PID 24056 那类残留）。
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, timeout=30)
            evlog("后端", "GSV 已释放（空闲超时）")
    except Exception as e:
        evlog("后端", "GSV 释放异常: %r" % (e,))


def _gsv_boot_worker():
    """后台线程：真正把 api_v2 拉起来。只设 _GSV_BOOTING，失败不抛。"""
    global _GSV_BOOTING, _GSV_PROC
    try:
        if not os.path.exists(GSV_PY):
            evlog("后端", "GSV 动态拉起失败：缺少解释器 %s" % GSV_PY)
            return
        app = os.path.join(GSV_DIR, "api_v2.py")
        if not os.path.exists(app):
            evlog("后端", "GSV 动态拉起失败：缺少 %s" % app)
            return
        evlog("后端", "GSV 按需拉起中（首次加载模型约 30~60 秒）...")
        # stdout/stderr 落文件，pythonw 没有控制台；CREATE_NO_WINDOW 防弹黑框。
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = open(GSV_LOG_OUT, "ab")
        err = open(GSV_LOG_ERR, "ab")
        _GSV_PROC = subprocess.Popen(
            [GSV_PY, app, "-c", GSV_CFG, "-a", "127.0.0.1", "-p", "9880"],
            cwd=GSV_DIR, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL, creationflags=flags)
        for _ in range(GSV_BOOT_TIMEOUT):
            if gsv_online(timeout=2):
                evlog("后端", "GSV 已就绪（9880）")
                return
            if _GSV_PROC is not None and _GSV_PROC.poll() is not None:
                evlog("后端", "GSV 拉起进程提前退出，见 %s" % GSV_LOG_ERR)
                return
            time.sleep(1)
        evlog("后端", "GSV 拉起超时（%ds），本次回退 edge-tts" % GSV_BOOT_TIMEOUT)
    except Exception as e:
        evlog("后端", "GSV 拉起异常: %r" % (e,))
    finally:
        with _GSV_LOCK:
            _GSV_BOOTING = False


def gsv_ensure_running():
    """确保 api_v2 在线。**绝不阻塞**：离线就起后台线程，立刻返回。
    返回 True 表示已在线；False 表示正在拉起或拉起失败（调用方回退 edge）。"""
    global _GSV_BOOTING, _GSV_LAST_USE
    with _GSV_LOCK:
        if gsv_online():
            _GSV_LAST_USE = time.monotonic()
            return True
        if _GSV_BOOTING:
            return False
        _GSV_BOOTING = True
        _GSV_LAST_USE = time.monotonic()
    threading.Thread(target=_gsv_boot_worker, daemon=True).start()
    return False


def gsv_wait_ready(timeout=None):
    """等待拉起完成，给合成调用用（在工作线程里等，不占请求线程）。"""
    timeout = timeout or GSV_BOOT_TIMEOUT
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if gsv_online(timeout=2):
            global _GSV_LAST_USE
            _GSV_LAST_USE = time.monotonic()
            return True
        time.sleep(1)
    return False


def _gsv_idle_reaper():
    """后台守护：空闲超 TTL 就释放 GSV（用完即走，别永久占 5.3GB）。"""
    while True:
        time.sleep(60)
        if GSV_IDLE_TTL <= 0:
            continue
        with _GSV_LOCK:
            proc = _GSV_PROC
            booting = _GSV_BOOTING
            idle = (time.monotonic() - _GSV_LAST_USE) if _GSV_LAST_USE else 0
        if proc is None or booting:
            continue
        if idle > GSV_IDLE_TTL:
            evlog("后端", "GSV 空闲 %ds 未使用，自动释放" % GSV_IDLE_TTL)
            _gsv_kill()


def _gsv_ensure_weights(v):
    """确保 api_v2 加载的是这条声线的权重对。切换需要几秒（换模型），只在变化时才切。"""
    gp = _gsv_path(v["gpt"].replace("/", os.sep))
    sp = _gsv_path(v["sovits"].replace("/", os.sep))
    if GSV_LOADED == {"gpt": gp, "sovits": sp}:
        return True
    try:
        r1 = requests.get(GSV_BASE + "/set_gpt_weights", params={"weights_path": gp}, timeout=60)
        r2 = requests.get(GSV_BASE + "/set_sovits_weights", params={"weights_path": sp}, timeout=60)
        if r1.status_code == 200 and r2.status_code == 200:
            GSV_LOADED.update({"gpt": gp, "sovits": sp})
            return True
        evlog("后端", "GSV 换权重失败: gpt=%d sovits=%d" % (r1.status_code, r2.status_code))
    except Exception as e:
        evlog("后端", "GSV 换权重异常: %r" % (e,))
    return False


def tts_gptsovits(text, speed=None):
    """GPT-SoVITS 本地合成，返回音频 bytes（wav）。失败返回 None（调用方回退 edge）。
    ★ 按需拉起：离线时就地起后台线程，然后在本工作线程里等它就绪（首次 30~60s）。"""
    global _GSV_LAST_USE
    key = CFG.get("gsv_voice") or "shorekeeper"
    v = GSV_VOICES.get(key)
    if not v:
        return None
    if not gsv_online():
        gsv_ensure_running()          # 幂等：已在拉起就直接返回 False
        if not gsv_wait_ready():
            evlog("后端", "GSV 未就绪，本句回退 edge-tts")
            return None
    _GSV_LAST_USE = time.monotonic()
    if not _gsv_ensure_weights(v):
        return None
    # 情绪层复用：开心/傲娇略快，心疼放慢一点；基准取人设 speed（默认 1.05）
    _, (rd, _) = _detect_emotion(text)
    base = float(speed if speed is not None else CFG.get("speed", 1.0))
    sf = max(0.6, min(1.6, base * (1.0 + rd / 100.0))) if rd else base
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": _gsv_ref_path(v["ref"].replace("/", os.sep)),
        "prompt_text": v["prompt"],
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": sf,
        "seed": -1,
    }
    try:
        r = requests.post(GSV_BASE + "/tts", json=payload, timeout=180)
        if r.status_code == 200 and r.content[:4] == b"RIFF":
            return r.content
        evlog("后端", "GSV 合成失败: HTTP %d %s" % (r.status_code, (r.text or "")[:200]))
    except Exception as e:
        evlog("后端", "GSV 请求异常: %r" % (e,))
    return None


HISTORY = []  # [{"role":"user"/"assistant","content":...}]

# ==================== 长期记忆 + 情境引擎（念念的"记得你"与"惦记你"） ====================
# 记忆：SQLite 持久化，重启不丢；抽取/消解走 LLM，失败绝不阻塞对话。
# 情境：按时段与空闲状态主动开口；只在她没说话、麦克风没收音时才动。
# 两个子系统都可整体关闭（memory_store / context_engine 各自的 enabled），
# 关掉后行为与改造前完全一致 —— 便于 A/B 排查。
MEMORY_ENABLED = os.environ.get("XIAOYUN_MEMORY", "1") not in ("0", "false", "False")
CONTEXT_ENABLED = os.environ.get("XIAOYUN_CONTEXT", "1") not in ("0", "false", "False")
MEMORY_DB = os.path.join(XIAOYUN_DIR, "data", "memory.db")

try:
    from memory_store import MemoryStore
    from memory_brain import MemoryBrain
    from context_engine import ContextEngine
except Exception as _e:      # 模块缺失不应让整个后端起不来
    MemoryStore = MemoryBrain = ContextEngine = None
    evlog("后端", "记忆/情境模块导入失败，相关功能已停用: %r" % (_e,))
    MEMORY_ENABLED = CONTEXT_ENABLED = False

MEM = None          # MemoryStore
BRAIN_MEM = None    # MemoryBrain
CTX = None          # ContextEngine

# ---- 屏幕感知（P0，借鉴 WhalePal）：周期截屏 → dhash 去重 → 关键词分类（离线兜底）
#      → 视觉增强（or_proxy）。截图不出内存，事件可落盘 data/events.jsonl。
PERCEPTION_ENABLED = os.environ.get("XIAOYUN_PERCEPTION", "1") not in ("0", "false", "False")
try:
    from perception import PerceptionEngine
    from events_store import EventStore
except Exception as _e:
    PerceptionEngine = EventStore = None
    evlog("后端", "屏幕感知/事件流模块导入失败，相关功能已停用: %r" % (_e,))
    PERCEPTION_ENABLED = False

PERC = None         # PerceptionEngine
EVENTS = None       # EventStore（活动事件流，P1：答"我刚才/今天在干嘛"）

# ---- Agent Plan 库（Step 0/1）：视觉主通道与后续 embed/语音的统一入口 ----
# 库在 xiaoyun/agentplan/agentplan.py（零第三方依赖），Key 链 ARK_PLAN_API_KEY → VOLC_SPEECH_API_KEY。
ARKPLAN = None
try:
    _ARK_DIR = os.path.join(XIAOYUN_DIR, "agentplan")
    if os.path.isdir(_ARK_DIR) and _ARK_DIR not in sys.path:
        sys.path.insert(0, _ARK_DIR)
    import agentplan as _arkmod
    if _arkmod.get_plan_key():
        ARKPLAN = _arkmod
    else:
        evlog("后端", "未找到 Agent Plan Key（ARK_PLAN_API_KEY/VOLC_SPEECH_API_KEY），方舟通道停用")
except Exception as _e:
    evlog("后端", "Agent Plan 库导入失败，方舟通道停用: %r" % (_e,))

ARK_VISION_MODEL = os.environ.get("XIAOYUN_ARK_VISION", "doubao-seed-2.1-lite")
# 历史窗口溢出时，被挤出去的部分攒起来做摘要；攒够一批再压一次，省 LLM 调用
_PENDING_SUMMARY = []
SUMMARY_BATCH = 8   # 每积攒 8 条（4 轮）压一次摘要


class EmbedClient(object):
    """本地 embedding 客户端，两种后端可切（`XIAOYUN_EMBED_BACKEND`）：

      · "ollama"（默认）   POST {url}/api/embed  {model, input, keep_alive, options}
                          url 默认 127.0.0.1:11434，模型 bge-m3（1024 维）
      · "http"（旧方案）   POST {url}/embed      {texts, mode}
                          自建 embed_service.py（8092，bge-small，512 维）

    为什么默认走 Ollama：本机本来就跑着 Ollama（桌宠之外的服务），
    `ollama pull bge-m3` 之后不需要再维护一套 torch + sentence-transformers
    —— 那套依赖曾把 transformers 顶到 5.x、差点带坏 Hunyuan3D-2。

    **显存策略**：默认 `num_gpu=99`（全部层上 GPU），实测 bge-m3 只占
    **664MB**、预热后 **26ms/次**（CPU 要 80ms）。8G 卡上这个代价可以忽略，
    而 Hunyuan3D 是低频使用、需要时手动 `ollama stop bge-m3` 腾卡即可。
    GPU 分配失败（例如 3D 正在吃满显存）时会自动用 CPU 重跑一次并记住这个
    选择 —— 语义检索宁可慢一点，也不该因为抢不到卡就整轮消失。
    `keep_alive=30m` 而不是 -1：比默认长得多，避免每轮对话都重新加载 1.2GB
    权重；又不常驻，长时间空闲后自动卸载，符合项目"不钉显存"的铁律。

    语义检索是增强项，不是必需品：服务没起 / 请求失败时所有方法安静返回空，
    MemoryStore 自动退化为纯 FTS5。探测结果带 30s 掉线缓存，避免服务不在时
    每轮都白等一次超时。
    """

    # 本机服务必须直连：进程若继承到系统 http_proxy，requests 会把 127.0.0.1
    # 也丢给代理，导致"服务明明在跑却连不上"（ASR 那条链路踩过同样的坑）。
    _NOPROXY = {"http": None, "https": None}

    def __init__(self, url, backend="ollama", model="bge-m3",
                 timeout=30, down_cache_secs=30, num_gpu=99, keep_alive="30m",
                 gpu_retry_secs=600):
        self.url = (url or "").rstrip("/")
        self.backend = (backend or "ollama").lower()
        self.model = model
        self.timeout = timeout
        self.num_gpu = num_gpu
        self.keep_alive = keep_alive
        self.gpu_state = "gpu" if num_gpu else "cpu"   # 实际生效的推理位置
        self.gpu_retry_secs = gpu_retry_secs
        self._fallback_ts = 0.0   # 上次回落 CPU 的时刻，用于到期复归 GPU
        self.dim = None          # 首次成功调用后记录真实维度，供维度迁移用
        self._down_cache_secs = down_cache_secs
        self._down_until = 0.0

    def health(self):
        try:
            path = "/api/tags" if self.backend == "ollama" else "/health"
            r = requests.get(self.url + path, timeout=2, proxies=self._NOPROXY)
            return r.status_code == 200
        except Exception:
            return False

    def _available(self):
        if not self.url:
            return False
        if time.time() < self._down_until:
            return False
        return True

    def available(self):
        """最近一次调用是否成功过（不发网络请求，专供 /api/health 用）。"""
        return bool(self.url) and self.dim is not None and time.time() >= self._down_until

    def _mark_down(self):
        self._down_until = time.time() + self._down_cache_secs

    def _ollama_embed(self, texts, num_gpu):
        """单次 Ollama /api/embed 调用，成功返回向量列表，失败返回 None。"""
        body = {"model": self.model, "input": list(texts),
                "keep_alive": self.keep_alive,
                "options": {"num_gpu": int(num_gpu)}}
        r = requests.post(self.url + "/api/embed", json=body,
                          timeout=self.timeout, proxies=self._NOPROXY)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("embeddings")

    def embed(self, texts, mode="doc"):
        """texts -> [[float], ...]；不可用/失败返回 None（调用方降级）。"""
        if not self._available() or not texts:
            return None
        # 回落 CPU 是有期限的：Hunyuan3D 跑完显存就还回来了，过了重试窗口
        # 主动再试一次 GPU —— 否则一次偶发的抢卡失败会让后续一直慢下去。
        # 用 >= 而不是 >：Windows 的 time.time() 只有 ~15ms 精度，窗口设 0 时
        # 两次调用可能落在同一刻度上，> 会永远判不成立。
        if (self.gpu_state == "cpu_fallback" and self.num_gpu
                and time.time() - self._fallback_ts >= self.gpu_retry_secs):
            self.gpu_state = "gpu"
        try:
            if self.backend == "ollama":
                # bge-m3 是对称检索模型，doc/query 不需要指令前缀，
                # 所以这里的 mode 参数只为兼容 MemoryStore 的签名。
                vecs = self._ollama_embed(texts, self.num_gpu)
                # 抢不到显存（3D 生成把卡吃满）时退回 CPU 重跑一次：
                # 检索慢几十毫秒，好过整轮退化成关键词、甚至没有记忆。
                if not vecs and self.num_gpu and self.gpu_state == "gpu":
                    vecs = self._ollama_embed(texts, 0)
                    if vecs:
                        self.gpu_state = "cpu_fallback"
                        self._fallback_ts = time.time()
                        evlog("后端", "embedding 显存不足，已回落 CPU 推理"
                                     "（%d 分钟后自动重试 GPU；也可 ollama stop 其他模型立即腾卡）"
                             % (self.gpu_retry_secs // 60))
            else:
                r = requests.post(self.url + "/embed",
                                  json={"texts": list(texts), "mode": mode},
                                  timeout=self.timeout, proxies=self._NOPROXY)
                vecs = (r.json() or {}).get("vectors") if r.status_code == 200 else None
            if not vecs:
                self._mark_down()
                return None
            self.dim = len(vecs[0])
            return vecs
        except Exception:
            self._mark_down()
            return None


EMBED = None        # EmbedClient，_init_memory_and_context 里赋值


# ---------------- 情绪调度（edge-tts 路径） ----------------
# edge-tts 不支持 style/情感标签（那是 Azure SSML 的能力，python 库拿不到），
# 直接朗读就是"一个调"的机械感。这里做一个轻量情绪层：按回复文本的
# 语气词/关键词把这句话归入某种情绪，再对该句的语速/音调做小幅偏移 ——
# 开心时略快略亮、傲娇时上扬、吃醋时带点冲、温柔时放轻。偏移都控制在
# ±8% / ±3Hz 内，不会破坏音色本身的人设感。
_EMOTION_RULES = [
    # (情绪名, 命中词, 语速偏移%, 音调偏移Hz)
    ("开心",  ["哈", "呀", "啦", "哇", "嘿嘿", "太好了", "真棒", "好耶", "赞"], 8, 2),
    ("傲娇",  ["哼", "笨蛋", "小家伙", "才不", "懒得理", "谁稀罕", "想得美"], 5, 3),
    ("吃醋",  ["那么久", "是谁", "是吗", "哦。", "行吧", "随便你"], 6, 1),
    ("心疼",  ["晚安", "抱抱", "我在", "没事", "早点", "心疼", "别熬", "吃点"], 0, -2),
]

_RATE_RE = re.compile(r"([+-]?\d+)\s*%")
_HZ_RE = re.compile(r"([+-]?\d+)\s*Hz")


def _compose_sps(base, delta, unit_re, lo, hi, fmt):
    """把 '+18%' / '+0Hz' 这类参数串叠加一个偏移量，并夹在安全区间内。"""
    try:
        m = unit_re.search(base or "")
        v = int(m.group(1)) if m else 0
    except Exception:
        v = 0
    v = max(lo, min(hi, v + delta))
    return fmt % v


def _detect_emotion(text):
    """按关键词计票选情绪；没有命中就返回 (None, (0, 0))。
    只看语气词/短口语，不影响标点以外的语义。"""
    if not text:
        return None, (0, 0)
    best, best_hits = None, 0
    for name, words, rd, pd in _EMOTION_RULES:
        hits = sum(1 for w in words if w in text)
        if hits > best_hits:
            best, best_hits = (name, rd, pd), hits
    if not best:
        return None, (0, 0)
    return best[0], (best[1], best[2])


# 大脑根地址（去掉 OpenAI 兼容端点结尾的 /v1）。or_proxy 健康检查 = ROOT_URL + "/health"
ROOT_URL = CFG["base"][:-3] if CFG["base"].rstrip("/").endswith("/v1") else CFG["base"].rstrip("/")
BRAIN_HEALTH_URL = ROOT_URL + "/health"
# 历史：Ollama 时代用 keep_alive=-1 把模型钉在显存；切换 or_proxy 后不再占用本机显存。

LIVE2D_DIR = os.path.join(LUNA_ROOT, "Live2D")
# 同一进程同时提供 Live2D 页面/模型资源（/、/js、/live2d-model...）与对话 API
app = Flask(__name__, static_folder=LIVE2D_DIR, static_url_path="")
CORS(app, expose_headers=["X-Xiaoyun-Text", "X-Xiaoyun-Segments",
                          "X-Xiaoyun-Sr", "X-Xiaoyun-Voice", "X-Xiaoyun-Stream"])


@app.get("/")
def index_page():
    return send_from_directory(LIVE2D_DIR, "xiaoyun.html")


# ==================== 火山方舟 Agent Plan · 豆包语音合成 2.0 ====================
# 2026-10-18 接入。HTTP 单向流式：一次给全文，流式回 base64 音频分片，凑齐即 mp3。
# 两个硬约束（实测踩过）：
#   ① 鉴权只认「Agent Plan 专属 API Key」（X-Api-Key）。拿方舟模型 Key（ark-…）调
#      会返回 45000010 call ark get status code:401 —— 与网络、Resource-Id 无关。
#   ② Resource-Id 必须显式写死 seed-tts-2.0；该模型不支持 Auto/控制台切换。
VOLC_TTS_URL = os.environ.get("XIAOYUN_VOLC_TTS_URL",
                              "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional")
VOLC_TTS_RES = os.environ.get("XIAOYUN_VOLC_TTS_RES", "seed-tts-2.0")


def tts_volc(text, speaker=None):
    """豆包语音合成 2.0（云端），返回 mp3 bytes；失败返回 None 由上层回退 edge-tts。
    实测：17 字首包 0.83s、总 1.36s，比 MiniMax 稳定且无需余额。"""
    key = (_plan_key() or CFG.get("volc_key") or "")
    if not key:
        evlog("后端", "未配置 VOLC_SPEECH_API_KEY，无法使用豆包 2.0 音色")
        return None
    spk = speaker or CFG.get("volc_speaker") or "zh_female_vv_uranus_bigtts"
    body = {"user": {"uid": "xiaoyun"},
            "req_params": {"text": text,
                           "speaker": spk,
                           "audio_params": {"format": "mp3",
                                            "sample_rate": 24000,
                                            "speech_rate": int(CFG.get("volc_speech_rate") or 0)}}}
    headers = {"Content-Type": "application/json",
               "X-Api-Key": key,
               "X-Api-Resource-Id": VOLC_TTS_RES,
               "X-Api-Request-Id": str(uuid.uuid4())}
    t0 = time.time()
    ttfb = None
    buf = bytearray()
    # 复用 tts_stream 的 keep-alive 会话：每次请求不再重付 TLS 握手。
    # 实测这一段能省 0.25~0.30s（首包 0.65s -> 0.38s），是纯增量收益、不改语义。
    try:
        import tts_stream as _ts
        _http, _extra = _ts.session(), {}
    except Exception:
        _http, _extra = requests, {"proxies": {"http": None, "https": None}}
    try:
        with _http.post(VOLC_TTS_URL, json=body, headers=headers, stream=True,
                        timeout=(5, 30), **_extra) as r:
            if r.status_code != 200:
                evlog("TTS", "豆包合成 HTTP %d: %s" % (r.status_code, (r.text or "")[:200]))
                return None
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                s = line.strip()
                if s.startswith("data:"):
                    s = s[5:].strip()
                try:
                    j = json.loads(s)
                except Exception:
                    continue
                code = j.get("code")
                if code not in (0, 20000000, None):
                    evlog("TTS", "豆包合成失败 code=%s message=%s"
                          % (code, (j.get("message") or "")[:200]))
                    return None
                d = j.get("data")
                if d:
                    if ttfb is None:
                        ttfb = round(time.time() - t0, 2)
                    buf.extend(base64.b64decode(d))
        if not buf:
            evlog("TTS", "豆包没有返回音频（额度/音色/Resource-Id？）speaker=%s" % spk)
            return None
        evlog("TTS", "豆包合成 %s 字 | speaker=%s 首包=%ss 总=%.2fs %dB"
             % (len(text), spk, ttfb, time.time() - t0, len(buf)))
        return bytes(buf)
    except Exception as e:
        evlog("TTS", "豆包请求异常: %r" % (e,))
        return None


def tts_minimax(text, voice_id=None, speed=None, pitch=None, emotion=None):
    """用 MiniMax 海螺语音（**云端**，本机不做任何合成推理）合成，返回 mp3 bytes；失败返回 None。

    为什么要接云端：本机 edge-tts 的中文女声只有 2 个（Xiaoxiao 温暖成熟 / Xiaoyi 活泼少女），
    靠降调只能"变低"，拿不到真人的气声与情绪。而 MiniMax 官方系统音色里直接就有
    `wumei_yujie`(妩媚御姐)、`female-yujie-jingpin`(御姐·精品)、`_Mature_Woman`(傲娇御姐)、
    `_Stubborn_Friend`(嘴硬竹马) 等，还支持 emotion 与语气词标签 (sighs)/(breath)/(chuckle)
    —— 正是"高冷诱惑"需要的质感。
    本机网络实测：api.minimaxi.com 可达；fish.audio / api.elevenlabs.io 均超时不可达，
    所以云端这一路只有 MiniMax 可用。
    """
    key = os.environ.get("MINIMAX_API_KEY") or CFG.get("minimax_key") or ""
    if not key:
        evlog("后端", "未配置 MINIMAX_API_KEY，无法用云端御姐音色")
        return None
    vid = voice_id or CFG.get("minimax_voice") or "wumei_yujie"
    vs = {
        "voice_id": vid,
        "speed": float(speed if speed is not None else CFG.get("speed", 1.0)),
        "vol": 1.0,
        "pitch": int(pitch if pitch is not None else CFG.get("pitch_hz", 0)),
    }
    emo = emotion if emotion is not None else CFG.get("emotion") or ""
    if emo:
        vs["emotion"] = emo
    body = {
        "model": CFG.get("minimax_model") or "speech-2.8-hd",
        "text": text,
        "stream": False,
        "language_boost": "Chinese",
        "voice_setting": vs,
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000,
                          "format": "mp3", "channel": 1},
        "output_format": "hex",      # hex 直接给音频字节，省一次下载
    }
    try:
        r = requests.post("https://api.minimaxi.com/v1/t2a_v2", json=body, timeout=60,
                          headers={"Authorization": "Bearer " + key,
                                   "Content-Type": "application/json"})
        j = r.json()
        br = j.get("base_resp") or {}
        if br.get("status_code") not in (0, None):
            evlog("后端", "MiniMax 合成失败 %s: %s" % (br.get("status_code"), br.get("status_msg")))
            return None
        hexaudio = (j.get("data") or {}).get("audio") or ""
        if not hexaudio:
            evlog("后端", "MiniMax 没有返回音频（key 无效/欠费？）trace=%s" % j.get("trace_id"))
            return None
        return bytes.fromhex(hexaudio)
    except Exception as e:
        evlog("后端", "MiniMax 请求异常: %r" % (e,))
        return None


def tts_mp3(text):
    """合成语音，返回音频 bytes（mp3 或 wav，调用方按 RIFF 头识别）。
    CFG["tts_engine"]：`volc`=豆包 2.0 云端；`gptsovits`=本地真人声线；
    `minimax`=云端御姐音色；`edge`=edge-tts。
    ★ 云端/本地失败（服务没起 / 欠费 / 网络抖动）会**自动回退 edge-tts** —— 她绝不会突然哑掉。"""
    eng = (CFG.get("tts_engine") or "edge").lower()
    if eng == "volc":
        b = tts_volc(text)
        if b:
            return b
        evlog("后端", "豆包语音不可用，本轮回退 edge-tts（检查 VOLC_SPEECH_API_KEY / 额度）")
    if eng == "gptsovits":
        b = tts_gptsovits(text)
        if b:
            return b
        evlog("后端", "GPT-SoVITS 不可用，本轮回退 edge-tts（检查 9880 服务）")
    elif eng == "minimax":
        b = tts_minimax(text)
        if b:
            return b
        evlog("后端", "云端音色不可用，本轮回退 edge-tts（检查 MINIMAX_API_KEY）")

    import edge_tts

    # 情绪调度：按本句文本微调语速/音调，摆脱"一个调到底"的机械感
    emo, (rd, pd) = _detect_emotion(text)
    rate = _compose_sps(CFG["rate"], rd, _RATE_RE, -40, 60, "%+d%%") if rd else CFG["rate"]
    pitch = _compose_sps(CFG.get("pitch", "+0Hz"), pd, _HZ_RE, -15, 15, "%+dHz") if pd else CFG.get("pitch", "+0Hz")
    if emo:
        evlog("TTS", "情绪=%s -> rate=%s pitch=%s" % (emo, rate, pitch))

    async def _run():
        comm = edge_tts.Communicate(text, CFG["voice"], rate=rate,
                                    volume=CFG["volume"], pitch=pitch)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    try:
        return asyncio.run(_run())
    except Exception as e:
        print("[tts error]", repr(e))
        return None


# ============ 流式语音（方案 A：借 StreamVox 的设计，不借它的代码）============
# 与上面的 tts_mp3() 并存、互不影响：这条新路**只吐裸 PCM**，不打包 data URI，
# 也不碰 LAST/SEQ（宠物窗轮询那条老链路完全不知道它的存在）。
# 为什么要多这一条：tts_mp3() 必须等整段合成完才返回，所以"首包 0.4s"对用户
# 毫无意义，每轮实际要等约 1.9s 才听到第一个字。裸 PCM 分片转发后，第一块音频
# 0.35~0.45s 就能落地，且不需要前端有解码器。
# ★ 详见 tts_stream.py 文件头：实测首包与文本长度无关，收益主要来自"边收边吐"。


def _tts_stream_params():
    """取当前生效的豆包参数。缺 key 返回 None，由调用方给明确的 503。"""
    key = (_plan_key() or CFG.get("volc_key") or "")
    if not key:
        return None
    return {
        "key": key,
        "speaker": CFG.get("volc_speaker") or "zh_female_vv_uranus_bigtts",
        "rate": int(CFG.get("volc_speech_rate") or 0),
    }


def _stream_available():
    """这一轮能不能走流式：引擎必须是豆包（裸 PCM 通道只有它有），且 key 齐。
    其它引擎（gptsovits / edge / minimax）仍走 tts_mp3 整段路径，行为不变。"""
    if (CFG.get("tts_engine") or "").lower() != "volc":
        return False
    return _tts_stream_params() is not None


def _pcm_response(text, tag="stream"):
    """把一段文字变成"裸 PCM 流"的 HTTP 响应。首块音频先拿到手再发头，
    这样合成失败还能回一个正常的 502 JSON，而不是一个 200 的空流。"""
    import tts_stream

    p = _tts_stream_params()
    if not p:
        return jsonify({"ok": False, "error": "未配置 VOLC_SPEECH_API_KEY"}), 503

    segs = tts_stream.segment_text(text)
    if not segs:
        return jsonify({"ok": False, "error": "没有可合成的内容"}), 400

    sid = int(time.time() * 1000)
    for i, s in enumerate(segs, 1):
        evlog("TTS流", "sid=%d %s 第%d/%d段(%d字): %s"
              % (sid, tag, i, len(segs), len(s), s[:40]))

    gen = tts_stream.stream_pcm(text, p["key"], p["speaker"], p["rate"],
                                on_event=lambda m: evlog("TTS流", "sid=%d %s" % (sid, m)))
    try:
        first = next(gen)
    except StopIteration:
        return jsonify({"ok": False, "error": "豆包没有返回音频"}), 502
    except Exception as e:
        evlog("TTS流", "sid=%d 首段失败: %r" % (sid, e))
        return jsonify({"ok": False, "error": "豆包合成失败: %s" % e}), 502

    def body():
        try:
            yield first
            for chunk in gen:
                yield chunk
        except Exception as e:      # 头已经发出去了，只能截断，不能改状态码
            evlog("TTS流", "sid=%d 流中断: %r" % (sid, e))

    resp = Response(body(), status=200)
    resp.headers["Content-Type"] = "audio/L16; rate=24000; channels=1"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["X-Xiaoyun-Sr"] = "24000"
    resp.headers["X-Xiaoyun-Segments"] = str(len(segs))
    resp.headers["X-Xiaoyun-Voice"] = p["speaker"]
    resp.headers["X-Xiaoyun-Stream"] = str(sid)
    # 文本用 base64 走头：HTTP 头只允许 latin-1，中文必须编码
    resp.headers["X-Xiaoyun-Text"] = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return resp


@app.post("/api/tts_stream")
def api_tts_stream():
    """只做合成（不经过大脑）：宠物窗流式播放、同一段台词 A/B 试听、量延迟都走这里。"""
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400
    sq = data.get("seq")
    tag = "宠物" if sq is None else "宠物seq=%s" % sq
    return _pcm_response(text, tag=tag)


@app.post("/api/tts_fallback")
def api_tts_fallback():
    """流式播放失败时的兜底：整段合成，直接回音频字节。

    ★ 为什么必须有这条：流式只是"提速手段"，不能变成"她哑掉"的新故障点。
      触发场景都真实存在：WebAudio 起不来、页面从没被点过导致 AudioContext 挂起、
      这条 HTTP 流被中途打断、后端某次 502。这条端点走的就是改流式之前的
      tts_mp3 老路径，前端用 <audio> 解码播放，能不能出声这件事和老版本完全一致。
    """
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400
    b = tts_mp3(text)
    if not b:
        return jsonify({"ok": False, "error": "合成失败"}), 503
    mime = "audio/wav" if b[:4] == b"RIFF" else "audio/mp3"
    evlog("TTS", "兜底整段合成 %d字 -> %d 字节 %s" % (len(text), len(b), mime))
    return Response(b, mimetype=mime, headers={"Cache-Control": "no-store"})


@app.post("/api/chat_stream")
def api_chat_stream():
    """一轮完整对话，但音频是流式的：先出字（头部），边合成边吐 PCM。

    ★ 这条端点**不更新** SEQ/LAST —— 它是一条独立的实验/试听通道，
      宠物窗轮询 /api/last 的老逻辑完全不受影响。
    """
    data = request.get_json(force=True, silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"ok": False, "error": "empty message"}), 400
    # 分层路由 L1：试听通道同样先过指令层（保持与 do_chat 同一判定口径）
    hit = _try_voice_command(msg)
    if hit is not None:
        return _pcm_response(hit[0]["reply"], tag="指令")
    try:
        reply = _brain_turn(msg)
    except Exception as e:
        return jsonify({"ok": False, "error": "大脑调用失败: " + str(e)}), 502
    return _pcm_response(reply, tag="对话")


@app.get("/streamdemo")
def streamdemo_page():
    """流式语音试听台：流式 vs 旧接口的同文本对比 + 实测延迟。"""
    resp = send_from_directory(PAGE_DIR, "streamdemo.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _pick_error(r):
    """尽量把 Ollama 的真实报错取出来，而不是只给一个状态码。"""
    try:
        j = r.json()
    except Exception:
        return (r.text or "").strip()[:300]
    if isinstance(j, dict):
        e = j.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e)
        if e:
            return str(e)
    return (r.text or "").strip()[:300]


def brain_online():
    """or_proxy 健康检查；在线返回 True。"""
    try:
        r = requests.get(BRAIN_HEALTH_URL, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def brain_warmup(model=None):
    """探活大脑（or_proxy）。只探活，不做任何显存加载/钉驻。返回 (ok, detail)。"""
    try:
        r = requests.get(BRAIN_HEALTH_URL, timeout=10)
        if r.status_code == 200:
            return True, "ready"
        return False, "HTTP %d %s" % (r.status_code, (r.text or "")[:200])
    except Exception as e:
        return False, repr(e)


# ---- 大脑真状态（P3，DSH 式呈现的信号源） ----
# thinking = 后台 brain 正在生成（她在"想"）；idle = 想完了；
# error = 这一轮大脑彻底失败；used = 实际应答的模型（or_proxy 回退后与选择不同）。
BRAIN_STATE = {"state": "idle", "model": "", "used": "", "error": "", "since": 0.0}


def _brain_state(state, **kw):
    """线程安全够用：dict 单键赋值原子，状态机只在本函数里推进。"""
    BRAIN_STATE.update(kw)
    BRAIN_STATE["state"] = state
    BRAIN_STATE["since"] = time.time()


def brain_chat(messages):
    """调用大脑：OpenAI 兼容 /v1/chat/completions（or_proxy -> OpenRouter 免费模型池）。
    or_proxy 自带多模型自动回退（429/404/超时切下一个候选）；这里失败重试一次。
    调用生命周期同步进 BRAIN_STATE（页面轮询 /api/brain/state 显示"思考中"）。"""
    url = CFG["base"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer sk-local"}
    payload = {
        "model": CFG["model"],
        "messages": messages,
        "temperature": CFG["temperature"],
        "max_tokens": CFG["max_tokens"],
        "stream": False,
    }
    _brain_state("thinking", model=CFG["model"], error="")
    last = ""
    for attempt in (1, 2):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=300)
            if r.status_code == 200:
                j = r.json()
                msg = (j.get("choices") or [{}])[0].get("message") or {}
                content = (msg.get("content") or "").strip()
                if content:
                    fb = r.headers.get("X-Fallback")
                    if fb:
                        print("[brain] 本次由回退模型应答:", fb)
                    _brain_state("idle", model=CFG["model"], used=fb or CFG["model"])
                    return content
                last = "空回复（模型可能只输出了思考内容）"
            else:
                last = "HTTP %d %s" % (r.status_code, _pick_error(r))
        except Exception as e:
            last = repr(e)
        if attempt == 1:
            print("[brain] 调用失败，重试一次：", last)
            time.sleep(2)
    _brain_state("error", error=(last or "")[:200])
    raise RuntimeError(last)


@app.get("/api/brain/state")
def api_brain_state():
    """大脑真状态（P3）：页面据此显示"思考中/出错了/实际用哪个模型应答"。"""
    st = dict(BRAIN_STATE)
    since = st.pop("since") or time.time()
    st["since_secs"] = round(time.time() - since, 1)
    st["speaking"] = bool(VOICE.get("busy") or VOICE.get("speaking"))
    st["ok"] = True
    return jsonify(st)


@app.get("/api/health")
def health():
    online = brain_online()
    gsv_state = gsv_status()          # down / booting / up（动态加载后的真实状态）
    gsv = (gsv_state == "up")
    # 真正在用的音色：按引擎取值。以前这里固定回 CFG["voice"]（edge 兜底声线），
    # 切到豆包后仍显示 zh-CN-XiaoxiaoNeural —— 看起来就像"我的音色没了"。
    eng = (CFG.get("tts_engine") or "edge").lower()
    if eng == "gptsovits":
        voice_actual = CFG.get("gsv_voice") or ""
    elif eng == "volc":
        voice_actual = CFG.get("volc_speaker") or ""
    else:
        voice_actual = CFG.get("voice") or ""
    opt = _voice_option(voice_actual) if eng == "volc" else None
    mopt = _chat_model_option(CFG.get("model") or "auto")
    return jsonify({
        "ok": True,
        "model": CFG["model"],
        "model_label": (mopt or {}).get("label") or CFG["model"],
        "voice": voice_actual,                 # 当前引擎实际在用的音色
        "voice_label": (opt or {}).get("label") or voice_actual,
        "voice_edge": CFG.get("voice") or "",  # edge 兜底声线（云端不可用时它会顶上）
        "engine": CFG.get("tts_engine"),
        # 当前生效的人设与豆包语速（-50~100），便于切换后直接核对是否真的生效
        "preset_key": CFG.get("preset_key") or "",
        "preset_label": CFG.get("preset_label") or "",
        "speech_rate": CFG.get("volc_speech_rate", 0),
        "gsv_voice": CFG.get("gsv_voice") or "",
        "gsv_online": gsv,
        "gsv_state": gsv_state,        # down=未加载 / booting=加载中 / up=在线
        "history_len": len(HISTORY),
        "brain_online": online,
        "model_resident": online,          # 兼容旧字段：or_proxy 无"常驻"概念，在线即等价
        "resident": (["or_proxy(OpenRouter)"] if online else [])
                    + (["GPT-SoVITS(9880)"] if gsv else []),
        # 语义检索：只有"最近一次 embedding 调用真的成功过"才算 true。
        # 不能只看 MEM._embed 是否为 None —— 客户端是**始终**注入的（为了服务晚起
        # 也能自动恢复），那样会永远报 true，掩盖"Ollama 没起、实际在走关键词"的真相。
        "memory_semantic": bool(MEM is not None and EMBED is not None and EMBED.available()),
        "memory_embed": ("%s@%s dim=%s %s" % (EMBED_MODEL, EMBED_BACKEND, EMBED.dim,
                                             getattr(EMBED, "gpu_state", "-"))
                         if EMBED is not None and EMBED.dim else ""),
    })


@app.post("/api/warmup")
def warmup():
    """探活大脑；保活脚本如仍调用此端点，不再产生任何显存加载。"""
    ok, detail = brain_warmup()
    return jsonify({"ok": ok, "detail": detail, "resident": ["or_proxy(OpenRouter)"] if ok else []})


@app.post("/api/reset")
def reset():
    """清空**本轮对话上下文**（HISTORY），托盘"清空对话记忆"走这里。

    ★ 刻意不删长期记忆：长期记忆是跨天累积的资产（你的名字、喜好、习惯），
      被一个"清空对话"的按钮顺手抹掉是不可逆的损失。要连长期记忆一起忘，
      走 /api/memory/clear（前端需二次确认）。
    """
    HISTORY.clear()
    _PENDING_SUMMARY[:] = []
    return jsonify({"ok": True})


# ---------------- 长期记忆运维端点 ----------------

@app.get("/api/memory")
def api_memory():
    """看她还记得什么。排查"怎么忘了/怎么记错了"时看这个。"""
    if MEM is None:
        return jsonify({"ok": False, "enabled": False, "error": "记忆模块未启用"})
    try:
        return jsonify({
            "ok": True,
            "enabled": True,
            "facts": MEM.all_facts(limit=100),
            "count": MEM.count(),
            "profile": MEM.get_profile(),
            "summary": MEM.get_meta("rolling_summary") or "",
            "stats": (BRAIN_MEM.stats if BRAIN_MEM else {}),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)}), 500


@app.post("/api/memory/clear")
def api_memory_clear():
    """彻底忘记：清空事实库与摘要（画像保留与否由 body.keep_profile 决定）。"""
    if MEM is None:
        return jsonify({"ok": False, "enabled": False, "error": "记忆模块未启用"})
    body = request.get_json(force=True, silent=True) or {}
    try:
        MEM.clear_facts()
        MEM.set_meta("rolling_summary", "")
        if not body.get("keep_profile"):
            for k in list(MEM.get_profile().keys()):
                MEM.set_profile(k, "")
        evlog("后端", "长期记忆已清空（保留画像=%s）" % bool(body.get("keep_profile")))
        return jsonify({"ok": True, "count": MEM.count()})
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)}), 500


@app.get("/api/context/state")
def api_context_state():
    """情境引擎状态：用于确认主动搭话是否真的在跑、下次大约何时开口。"""
    if CTX is None:
        return jsonify({"ok": False, "enabled": False, "error": "情境引擎未启用"})
    try:
        name, hint = None, None
        from context_engine import time_slot, time_text
        name, hint = time_slot()
        now = time.time()
        return jsonify({
            "ok": True, "enabled": True,
            "slot": name, "hint": hint, "now_text": time_text(),
            "idle_secs": round(now - CTX.last_user_ts, 1),
            "next_idle_at": round(max(0.0, CTX._next_idle - (now - CTX.last_user_ts)), 1),
            "since_speak": round(now - CTX.last_speak_ts, 1),
            "config": {"idle_seconds": CTX.idle_seconds, "min_gap": CTX.min_gap,
                       "randomize": CTX.randomize},
            "working": bool(PERC is not None and PERC.is_working()),
            "stats": CTX.stats,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)}), 500


@app.get("/api/context/activity")
def api_context_activity():
    """屏幕感知快照：页面轮询 signal/signal_seq 播活动反应表情（同 poke.seq 机制）。
    旧感知未启用时返回 enabled=false，页面据此静默停用轮询。"""
    if PERC is None:
        return jsonify({"ok": True, "enabled": False})
    try:
        st = PERC.state()
        st["ok"] = True
        st["enabled"] = True
        st["recent"] = PERC.recent(10)
        return jsonify(st)
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)}), 500


@app.get("/api/context/today")
def api_context_today():
    """今日回顾（P1）：0 点起的活动聚合 + 最近事件。确定性计算，不烧 LLM——
    "讲给你听"的部分由她自己在对话里用活动上下文现组织。"""
    if EVENTS is None:
        return jsonify({"ok": True, "enabled": False})
    try:
        return jsonify({
            "ok": True, "enabled": True,
            "summary": EVENTS.today_summary(),
            "last": EVENTS.last(),
            "recent": EVENTS.recent(15),
            "count": EVENTS.count(),
            "context_line": EVENTS.context_block(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)}), 500



@app.post("/api/chat")
def chat():
    data = request.get_json(force=True)
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"ok": False, "error": "empty message"}), 400
    try:
        payload, _mp3 = do_chat(msg)
    except Exception as e:
        return jsonify({"ok": False, "error": "大脑（or_proxy/OpenRouter）调用失败: " + str(e)}), 502
    return jsonify(payload)


def _estimate_speech_seconds(text):
    """兜底估算她这轮要讲多久（页面精确上报为主，这里只是兜底）。
    ★ 循环收音防线：只允许往"估得更长"的方向调 —— 语速为正（讲得快）时
      维持旧的 4.5字/秒 口径不变（宁多等）；语速为负（讲得慢）时才按系数
      拉长估算。任何情况下都不会比旧公式更早开麦。"""
    try:
        m = _RATE_RE.search(CFG.get("rate") or "+0%")
        pct = int(m.group(1)) if m else 0
    except Exception:
        pct = 0
    factor = max(0.7, 1.0 + min(pct, 0) / 100.0)   # 只在负语速时缩短分母=拉长估算
    return len(text) / (4.5 * factor) + 3.5


def _brain_turn(msg):
    """只跑"思考"那一半：拼 messages -> 调大脑 -> 写多轮历史 -> 返回回复文本。
    /api/chat（整段合成）与 /api/chat_stream（流式）共用，保证两条路的上下文行为一致。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    messages = [
        {"role": "system", "content": CFG["preset"]},
        {"role": "system", "content": "current time is:" + now},
    ]
    # 语感签名：同一句问题，不同人设说出来应当不一样
    cpb = _catchphrase_block()
    if cpb:
        messages.append({"role": "system", "content": cpb})
    # 情境块：她得知道现在几点、该是什么状态，才谈得上"共情"
    if CTX is not None:
        try:
            messages.append({"role": "system", "content": CTX.prompt_block()})
        except Exception as e:
            evlog("后端", "情境块生成失败: %r" % (e,))
    # 记忆块：画像 + 相关事实 + 更早的聊天摘要。失败只是没有记忆，不影响本轮
    if BRAIN_MEM is not None:
        try:
            mem_ctx = BRAIN_MEM.get_context(msg)
            if mem_ctx:
                messages.append({"role": "system", "content": mem_ctx})
        except Exception as e:
            evlog("后端", "记忆上下文拼装失败: %r" % (e,))
    # 活动块（P1）：她刚"看见"的屏幕 —— 答得出"我刚才在干嘛"，聊得近在眼前的事
    act = _activity_context()
    if act:
        messages.append({"role": "system", "content": act})
    # 称呼：记忆里存过"他希望被叫什么"的话就一直用那个，不要每轮换
    messages.append({"role": "system", "content": NICKNAME_HINT})
    # 舞台指令禁令放在最后一条 system：离生成最近，约束力最强（见 STAGE_HINT 注释）
    messages.append({"role": "system", "content": STAGE_HINT})

    messages.extend(HISTORY)
    messages.append({"role": "user", "content": msg})

    reply = brain_chat(messages)

    HISTORY.append({"role": "user", "content": msg})
    HISTORY.append({"role": "assistant", "content": reply})
    if len(HISTORY) > MAX_HISTORY:
        # 超出窗口的部分不直接丢掉 —— 攒起来做滚动摘要，否则她会彻底忘记更早的事
        _PENDING_SUMMARY.extend(HISTORY[:len(HISTORY) - MAX_HISTORY])
        del HISTORY[:-MAX_HISTORY]
        if BRAIN_MEM is not None and len(_PENDING_SUMMARY) >= SUMMARY_BATCH:
            batch, _PENDING_SUMMARY[:] = _PENDING_SUMMARY[:], []
            threading.Thread(target=BRAIN_MEM.summarize, args=(batch,),
                             daemon=True).start()

    # 用户说话了：重置空闲计时，让她稍后主动开口时点才对
    if CTX is not None:
        try:
            CTX.touch_user()
        except Exception:
            pass
    # 消化这一轮：异步抽取事实并入库。_brain_turn 是文字/流式两条对话路径的共用
    # 入口，钩在这里两路都能覆盖；且是后台线程 —— 她早已答上话，记忆快慢与她无关。
    if BRAIN_MEM is not None:
        BRAIN_MEM.observe_async(msg, reply)
    return reply


def _brain_prompt(prompt):
    """给记忆/情境模块用的单轮 LLM 调用：只问一句话，不写历史、不发声。
    单独包装一层，是为了让记忆抽取用的模型与主对话解耦（可按需换成更快的小模型），
    同时把异常原样抛给调用方——由 memory_brain 统一做故障隔离。"""
    return brain_chat([{"role": "user", "content": prompt}])


def _evlog(msg):
    """单参日志适配器：记忆/情境模块的 log= 只接受一个字符串，
    而项目的 evlog 是 (tag, msg) 双参。包一层，别去改 evlog 的既有签名
    （全项目几十处调用都在用）。"""
    evlog("记忆/情境", msg)


def _asr_listening():
    """麦克风此刻是否在收音。主动开口前必须确认，否则会把她的声音录进去转写。

    ★ 探不到时的取舍：返回 False（当没在收）。理由是 ASR 服务挂了本就不会录音，
      此时判定"正在收"只会让她永远闭嘴；真正兜底的是上层那道 VOICE.busy/speaking
      硬闸门，以及语音链路上既有的"回复期间禁止开麦"守卫。
    """
    try:
        st = _asr("/session/state") or {}
        return bool(st.get("recording") or st.get("state") == "listening")
    except Exception:
        return False


def _publish_reply(reply, use_stream):
    """把一句要说的话发布到广播槽：合成（或标记流式）、上时序守卫、写 LAST。

    ★ 抽成共用函数的理由：这是一整条时序铁律（SEQ 递增、LAST_REPLY_GUARD、
      PLAYBACK 清零三件事必须同时做且顺序固定）。主动开口如果照抄一份，
      将来改一处漏一处，就会重现"自问自答死循环"那个已修复的坑。
    返回 (payload, mp3)。
    """
    # ★ 舞台指令兜底清洗：大脑被 STAGE_HINT 约束着，但用户可编辑人设/模型漏出
    #   括号动作（"（别过脸）"）时必须拦在这里——这是所有发声的唯一出口，
    #   气泡文字与朗读保持一致。固定文案（切换确认等）不含括号，清洗无害。
    stripped = _strip_stage_text(reply)
    if stripped != (reply or "").strip() and stripped:
        evlog("后端", "已剥离舞台指令: %r -> %r" % (reply[:50], stripped[:50]))
    if not stripped:
        stripped = reply        # 整条全是动作时宁可原样念，也不能哑掉
    reply = stripped

    audio_uri = None
    mp3 = None
    if not use_stream:
        mp3 = tts_mp3(reply)
        if mp3:
            # GPT-SoVITS 路径返回 wav（RIFF 头），edge/minimax 返回 mp3 —— 按头给对 MIME
            _mime = "audio/wav" if mp3[:4] == b"RIFF" else "audio/mp3"
            audio_uri = "data:%s;base64," % _mime + base64.b64encode(mp3).decode()

    global SEQ, LAST_REPLY_TS, LAST_REPLY_GUARD
    SEQ += 1
    LAST_REPLY_TS = time.time()          # 她开口的时间：语音收音要避开这一刻
    # 按字数估算她要讲多久（见 _estimate_speech_seconds），这段时间里不许收音，
    # 否则麦克风会把她自己的声音录进去变成自问自答。页面播完会调
    # /api/voice/resume 精确解除，这里只是兜底。
    # ★ 流式时没有 mp3 可当"确实有声"的凭据，但确实会播 —— 必须照样上守卫，
    #   否则 1.0s 后就会开麦，正好录到她说第二句（自问自答的直接成因）。
    LAST_REPLY_GUARD = _estimate_speech_seconds(reply) if (mp3 or use_stream) else 1.0
    # ★★ 清零上一条的播放记录：后台消费者等她说完时只认"本轮新上报"的 start/dur，
    #    若不清零，读到的是上一条（时间点早已过去）-> 兜底条件瞬间成立 ->
    #    在她开口前就把麦克风打开 -> 录到她自己 -> 自问自答死循环。
    PLAYBACK["start"] = 0.0
    PLAYBACK["dur"] = 0.0
    PLAYBACK["seq"] = None
    PLAYBACK["ts"] = 0.0
    LAST.update({"seq": SEQ, "reply": reply, "audio": audio_uri,
                 "voice": CFG["voice"], "stream": use_stream})

    if CTX is not None:
        try:
            CTX.note_speak()        # 她开口了：避免紧接着又主动搭话
        except Exception:
            pass
    return {"ok": True, "reply": reply, "audio": audio_uri,
            "voice": CFG["voice"], "seq": SEQ, "stream": use_stream,
            "play_here": False}, mp3


# ==================== 语音指令路由（L1 规则层，分层路由 Phase 1） ====================
# 文字聊天（/api/chat）与语音对话（worker 里的 do_chat）汇聚于同一条路，
# 指令拦截只做 do_chat 入口这一处；/api/chat_stream（试听通道）单独拦一层。
# 解析逻辑全部在 voice_command_router.py（纯函数、独立单测）——本函数只负责执行 + 发声。
_CMD_COOLDOWN = {}           # (intent, slot) -> 上次执行时间戳
CMD_MOTION_COOLDOWN = 10.0   # 同一动作 10s 内不重放（借鉴 VTS 弹幕互动的冷却实践）
CMD_VOICE_COOLDOWN = 6.0
CMD_MODEL_COOLDOWN = 15.0    # 换形象要整页重载模型，冷却放宽


def _set_motion_key(key):
    """写动作持久化选择（/api/motion 端点与语音指令共用这一份写入逻辑）。"""
    os.makedirs(MOTION_DIR, exist_ok=True)
    with open(MOTION_STATE, "w", encoding="utf-8") as f:
        f.write(key)


def _cmd_say(msg, reply):
    """指令轮的发布 + 收尾：确认话术走 _publish_reply（SEQ/LAST_REPLY_GUARD/
    PLAYBACK 时序铁律不许有第二条发布路径，否则就是自问自答死循环的裂缝），
    并把指令轮写进多轮历史（她下一轮听到"再换一个"才有上下文）。
    指令轮不触发记忆抽取——"换个音色"不是值得长期记住的事实。"""
    pet_present = PET_VISIBLE and (time.time() - PET_ALIVE_TS) < PET_ALIVE_WINDOW
    payload, mp3 = _publish_reply(reply, bool(pet_present and _stream_available()))
    payload["play_here"] = (not pet_present)
    HISTORY.append({"role": "user", "content": msg})
    HISTORY.append({"role": "assistant", "content": reply})
    if len(HISTORY) > MAX_HISTORY:
        del HISTORY[:-MAX_HISTORY]
    if CTX is not None:
        try:
            CTX.touch_user()
        except Exception:
            pass
    return payload, mp3


def _try_voice_command(msg):
    """分层路由第一层：命中语音指令则执行并让小芸开口确认。
    返回 (payload, mp3)；未命中返回 None（调用方照常走对话大脑）。
    槽位歧义/缺失时不瞎切——她反问一句，由用户补说。"""
    cmd = vcmd.parse_command(msg, VOICE_OPTIONS + GSV_OPTIONS + VOLC_OPTIONS,
                             [(m["key"], m.get("label") or m["key"])
                              for m in _list_motions()],
                             models=[(m["key"], m.get("label") or m["key"])
                                     for m in _models()])
    if cmd is None:
        return None
    intent = cmd["intent"]

    # 冷却：防 VAD 抖动/连说把同一动作反复触发（反问类不需要）
    slot = (cmd.get("voice_id") or cmd.get("motion_key")
            or cmd.get("model_key") or "")
    cool = {"play_motion": CMD_MOTION_COOLDOWN, "stop_motion": CMD_MOTION_COOLDOWN,
            "switch_voice": CMD_VOICE_COOLDOWN,
            "switch_model": CMD_MODEL_COOLDOWN}.get(intent, 0.0)
    if cool and time.time() - _CMD_COOLDOWN.get((intent, slot), 0.0) < cool:
        evlog("指令", "冷却中(%s %s)，只回应不执行" % (intent, slot))
        return _cmd_say(msg, "这个刚刚已经弄过啦，先这样挺好。")
    _CMD_COOLDOWN[(intent, slot)] = time.time()

    if intent == "switch_voice":
        o = _apply_voice(cmd["voice_id"], persist=True)
        if o is None:
            return None
        # 与托盘菜单同语义：gsv 音色要顺手把本地 GPT-SoVITS 拉起来（后台，不阻塞）
        if (CFG.get("tts_engine") or "") == "gptsovits" and not gsv_ensure_running():
            threading.Thread(target=gsv_wait_ready, daemon=True).start()
        extra = "这个声音在本地合成，第一次要等我准备个半分钟。" \
            if (o.get("engine") == "gptsovits") else ""
        reply = "换好啦，现在是「%s」。%s这样说还喜欢吗？" % (o.get("label"), extra)
    elif intent == "play_motion":
        _set_motion_key(cmd["motion_key"])
        reply = "看好了——「%s」！" % cmd["motion_label"]
    elif intent == "stop_motion":
        _set_motion_key(MOTION_IDLE)
        reply = "好啦，回来休息。"
    elif intent == "ask_motion":
        names = "、".join(clean for clean in
                          [vcmd.clean_label(m.get("label") or m["key"])
                           for m in _list_motions()[:6]] if clean)
        reply = "想看哪个呀？我这有：%s……说名字就行。" % names
    elif intent == "ask_voice":
        labels = cmd.get("ambiguous") or ["高冷御姐", "活泼少女", "东北口音", "台湾柔嗓"]
        reply = "想让我变成什么声音？比如%s，你点一个。" % "、".join(labels)
    elif intent == "switch_model":
        if cmd["model_key"] == _current_model_key():
            # 就是要现在这个形象：不写状态，页面就不会白白重载一次模型
            return _cmd_say(msg, "我现在就是「%s」呀，还想看谁？" % cmd["model_label"])
        _set_model_key(cmd["model_key"])
        reply = "好~我去变成「%s」，几秒钟，等我换装哦。" % cmd["model_label"]
    elif intent == "ask_model":
        lst = _models()
        items = "、".join("%d%s" % (i + 1, vcmd.clean_label(m.get("label") or m["key"]))
                          for i, m in enumerate(lst[:8]))
        reply = "想看我变成谁？%s……报编号或说名字都行。" % items
    else:
        return None
    evlog("指令", "%s 命中(%s) -> %s" % (intent, cmd.get("matched") or slot, reply[:40]))
    return _cmd_say(msg, reply)


def do_chat(msg):
    """跑一轮"思考 + 发声"，文字聊天和语音对话共用这一条路。
    返回 (payload, audio_bytes_or_None)。"""
    # 分层路由 L1：指令（切音色/做动作）优先于闲聊；未命中返回 None，原链路零变化
    hit = _try_voice_command(msg)
    if hit is not None:
        return hit
    reply = _brain_turn(msg)

    # 小芸本体在桌面上，就由她出声；她不在才让聊天窗出声，避免两个声音重合
    pet_present = PET_VISIBLE and (time.time() - PET_ALIVE_TS) < PET_ALIVE_WINDOW

    # ★ 流式分支：宠物窗在线时**不在这里合成**，只把文字放进 LAST，由页面自己去
    #   /api/tts_stream 拉裸 PCM、边到边播。收益是这一轮不再被 tts_mp3 阻塞
    #   （整段合成中位 1.89s），气泡文字也能立刻出现，她 0.4s 后就能开口。
    #   ★ 只对宠物窗生效：聊天窗（chat.html）只认 last.audio，给它流式等于让它哑了。
    use_stream = bool(pet_present and _stream_available())

    payload, mp3 = _publish_reply(reply, use_stream)
    payload["play_here"] = (not pet_present)
    return payload, mp3


# ==================== 主动开口（情境引擎的真正落地） ====================
# 与 do_chat 的关键差异：这里**不经过** _brain_turn，所以不会往 HISTORY 里写
# 用户消息（她主动说的话，用户并没有说），也不会触发记忆抽取 —— 否则会把
# 她自己随口的话当成"用户的信息"记下来，污染记忆库。
def _speak_proactively(intent, trigger):
    """按情境引擎给的意图现生成一句话，并走同一条发布链路。"""
    # 双重确认：到真正开口这一刻再查一次状态。判定在后台线程里，到她生成完
    # 可能已经过去几秒，期间她可能已经开始说话/开始收音了。
    if VOICE.get("busy") or VOICE.get("speaking"):
        evlog("后端", "放弃主动开口：她正在说话（trigger=%s）" % trigger)
        return False
    if not (PET_VISIBLE and (time.time() - PET_ALIVE_TS) < PET_ALIVE_WINDOW):
        evlog("后端", "放弃主动开口：宠物窗不在线（trigger=%s）" % trigger)
        return False

    messages = [
        {"role": "system", "content": CFG["preset"]},
        {"role": "system", "content": "current time is:" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    ]
    cpb = _catchphrase_block()      # 主动开口也要带着这套人格的语感
    if cpb:
        messages.append({"role": "system", "content": cpb})
    if CTX is not None:
        try:
            messages.append({"role": "system", "content": CTX.prompt_block()})
        except Exception:
            pass
    if BRAIN_MEM is not None:
        try:
            mem_ctx = BRAIN_MEM.get_context("")
            if mem_ctx:
                messages.append({"role": "system", "content": mem_ctx})
        except Exception:
            pass
    act = _activity_context()      # 主动开口也知道主人在干嘛，才不尬
    if act:
        messages.append({"role": "system", "content": act})
    # 主动开口也别换称呼：记忆里有就用记忆里的那个
    messages.append({"role": "system", "content": NICKNAME_HINT})
    if HISTORY:
        messages.extend(HISTORY[-6:])
    # 意图单独放一条 system，并明确"这就是你要做的事"，避免模型把它当成用户说的话
    messages.append({"role": "system",
                     "content": "现在轮到**你主动开口**了（用户还没有说话）：" + intent +
                                " 用中文口语说，一到两句，不要复述这条指令本身。"})
    # 舞台指令禁令：主动开口同样会被念出来，约束放在意图之后（离生成最近）
    messages.append({"role": "system", "content": STAGE_HINT})

    try:
        reply = brain_chat(messages)
    except Exception as e:
        evlog("后端", "主动开口生成失败: %r" % (e,))
        return False

    reply = _strip_stage_text(reply)   # 主动开口同样要剥舞台指令（见函数注释）

    reply = (reply or "").strip()
    if not reply or len(reply) > 120:
        # 空回复或长篇大论都不该出口：前者是白跑一趟，后者会变成念课文
        evlog("后端", "放弃主动开口：回复为空或过长（%d 字）" % len(reply))
        return False

    # 她的话也进历史，只是只记 assistant 那一半，下一轮上下文才连贯
    HISTORY.append({"role": "assistant", "content": reply})
    if len(HISTORY) > MAX_HISTORY:
        _PENDING_SUMMARY.extend(HISTORY[:len(HISTORY) - MAX_HISTORY])
        del HISTORY[:-MAX_HISTORY]

    use_stream = bool(_stream_available())
    _publish_reply(reply, use_stream)
    evlog("后端", "主动开口(%s)：%s" % (trigger, reply))
    return True


@app.get("/settings")
def settings_page():
    """可视化设置面板（P3）：陪伴 / 模型 / 数据 / 回顾。
    纯前端聚合现有 API（preset/voice-options/chat-model/memory/context/today），
    所有修改热生效，服务端零新状态。"""
    resp = send_from_directory(PAGE_DIR, "settings.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/desktop")
def desktop_page():
    """Windows 桌面宠物窗口（透明无边框）"""
    return send_from_directory(PAGE_DIR, "desktop.html")

@app.get("/desktop3d")
def desktop3d_page():
    """桌面宠物窗口 · 3D 模型版（three.js 渲染，背景保持透明）"""
    resp = send_from_directory(PAGE_DIR, "desktop3d.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/desktop2d")
def desktop2d_page():
    """桌面宠物窗口 · 2D 立绘版（透明 PNG + 自建骨架蒙皮，可动四肢）"""
    resp = send_from_directory(PAGE_DIR, "desktop2d.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/desktopvrm")
def desktopvrm_page():
    """桌面宠物窗口 · VRM 模型版（three-vrm 加载标准 VRM，自带骨骼）"""
    resp = send_from_directory(PAGE_DIR, "desktopvrm.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/desktopfbx")
def desktopfbx_page():
    """桌面宠物窗口 · FBX 模型版（模之屋 MMD 包里的 .fbx，FBXLoader 加载）"""
    resp = send_from_directory(PAGE_DIR, "desktopfbx.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/desktoppmx")
def desktoppmx_page():
    """桌面宠物窗口 · PMX 模型版（MMD 原生格式，MMDLoader 加载，日文骨骼名正确解析）"""
    resp = send_from_directory(PAGE_DIR, "desktoppmx.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/bjd3dvr")
def bjd3dvr_page():
    """桌面宠物窗口 · BJD 3D 版（混元3D生成 + Blender 绑骨 + blink 形态键）。

    独立页，不改 PET_PAGE 默认值：用 XIAOYUN_PET_PAGE=/bjd3dvr 启动桌面壳才会走这里。
    """
    resp = send_from_directory(PAGE_DIR, "bjd3dvr.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/pmxtune")
def pmxtune_page():
    """调色面板：右键菜单「调参」打开的独立小窗口（也可以被预览页 iframe 内嵌）。"""
    resp = send_from_directory(PAGE_DIR, "pmxtune.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/3dprobe")
def api_3dprobe():
    """宠物窗自检上报（?d=...）：只落一行日志，不写盘。旧后端没有这条会 404，无害。"""
    try:
        print("[3dprobe]", str(request.args.get("d", ""))[:200], flush=True)
    except Exception:
        pass
    return ("ok", 200)


@app.get("/assets/<path:fname>")
def xiaoyun_asset(fname):
    """宠物窗用的本地静态资源（three.js 库 / .glb 模型），不依赖外网 CDN。"""
    return send_from_directory(os.path.join(XIAOYUN_DIR, "assets"), fname)


@app.get("/chat")
def chat_page():
    """独立聊天窗口（带历史记录）"""
    return send_from_directory(PAGE_DIR, "chat.html")


@app.get("/api/last")
def api_last():
    """桌面宠物窗轮询：只有 seq 变化时才回传完整内容（含音频）。
    这个接口只有宠物窗会调，所以顺便用它标记"小芸本体在线"。"""
    global PET_ALIVE_TS
    PET_ALIVE_TS = time.time()
    try:
        since = int(request.args.get("since", "0"))
    except Exception:
        since = 0
    if since == SEQ:
        return jsonify({"seq": SEQ, "fresh": False})
    # 播放权：同一 seq 只有一个页面能拿到音频，其余页面只出气泡不出声。
    # ★ 流式轮次（audio=None + stream=True）同样要抢占：否则两个宠物页会同时
    #   去拉 /api/tts_stream，合成两遍、两个声音叠着播。
    audio = LAST["audio"]
    stream = bool(LAST.get("stream"))
    if audio or stream:
        now = time.time()
        claimed = (LAST_CLAIM["seq"] == SEQ
                   and (PLAYBACK["start"] > 0 or (now - LAST_CLAIM["ts"]) < CLAIM_TTL))
        if claimed:
            audio = None
            stream = False
            evlog("后端", "seq=%d 播放权已归另一页面，本页静音（防双重播放）" % SEQ)
        else:
            LAST_CLAIM["seq"] = SEQ
            LAST_CLAIM["ts"] = now
    LAST_FETCH["seq"] = SEQ
    LAST_FETCH["ts"] = time.time()
    return jsonify({"seq": SEQ, "fresh": True, "reply": LAST["reply"],
                    "audio": audio, "voice": LAST["voice"], "stream": stream})


@app.post("/api/pet_state")
def api_pet_state():
    """桌面壳上报宠物窗是否可见（网页自己感知不到窗口被隐藏）。"""
    global PET_VISIBLE
    try:
        data = request.get_json(force=True) or {}
        PET_VISIBLE = bool(data.get("visible", True))
    except Exception:
        pass
    return jsonify({"ok": True, "visible": PET_VISIBLE})


@app.get("/api/history")
def api_history():
    return jsonify({"ok": True, "history": HISTORY, "seq": SEQ})


@app.get("/api/config")
def api_config():
    return jsonify({"ok": True, "model": CFG["model"], "voice": CFG["voice"],
                    "preset": CFG["preset"]})


# ==================== 语音对话（ASR 代理 + 后台消费者） ====================
# 数据流：麦克风 -> asr_service(8091) 转出文字 -> 本服务跑 do_chat ->
#         写进 LAST -> 宠物窗照旧轮询 /api/last 播语音。文字聊天完全不受影响。

def _asr(path, method="GET", payload=None, timeout=8):
    r = requests.request(method, ASR_URL + path, json=payload, timeout=timeout)
    return r.json()


@app.get("/api/voice/state")
def voice_state():
    """宠物窗轮询：ASR 服务状态 + 当前模式 + 音量电平（做光圈用）"""
    _ensure_voice_worker()      # 看门狗：消费者线程没了就立刻拉回来（见 _ensure_voice_worker）
    try:
        j = _asr("/session/state")
    except Exception as e:
        return jsonify({"ok": False, "asr_online": False,
                        "error": "语音识别服务未启动（asr_service.py）: %s" % e,
                        "mode": VOICE["mode"], "state": "off",
                        "phase": "off", "phase_secs": 0.0, "busy": bool(VOICE["busy"])})
    j["ok"] = True
    j["asr_online"] = True
    j["mode"] = VOICE["mode"]                 # 界面上的模式开关以后端为准
    j["speaking"] = VOICE["speaking"]         # 她正在说话（此时不该收音）
    # 本轮播放还剩多少秒（0 = 没有在播或已播完）。排查"播放/收音错位"时看这个。
    left = 0.0
    if PLAYBACK["start"] > 0:
        left = max(0.0, PLAYBACK["start"] + PLAYBACK["dur"] - time.time())
    j["playback_left"] = round(left, 2)
    j["playback_dur"] = round(PLAYBACK["dur"], 2)
    # ★ 统一阶段（界面据此显示"等模型/等播放/正在说"）。
    #   后端已经接管语音流水线时以 VOICE.stage 为准；否则沿用 ASR 自己的状态。
    phase, secs = _voice_phase()
    if phase is None:
        st = j.get("state") or "off"
        if st == "off":
            phase = "off"
        elif j.get("recording") or st == "listening":
            phase = "listening"
        elif st == "thinking":
            phase = "transcribing"
        elif st == "pending":
            phase = "pending"
        else:
            phase = "idle"
        secs = 0.0
    j["phase"] = phase
    j["phase_secs"] = round(secs, 1)
    j["busy"] = bool(VOICE["busy"])
    # 桌面壳是否在跑（壳每 0.5s 发一次心跳）。页面靠它决定"点击是否由壳的隐形层接管"，
    # 不再依赖 pywebview 的 JS 桥探测 —— 桥的探测时序很不稳（实测经常在加载瞬间为 false）。
    j["shell_online"] = (time.time() - SHELL_BEAT["ts"]) < 3.0
    return jsonify(j)


# ==================== 页面控件坐标通道（HTTP，不走 JS 桥） ====================
# ★ 为什么不走 pywebview 的 JS 桥：JS 桥是"同步回环"调用，要在桌面壳的主线程上执行。
#   页面按固定频率推坐标时，会把 WebView2 的 UI 线程堵死 —— 实测表现就是"桌宠卡死"：
#   窗口变成 "(未响应)"（IsHungAppWindow=True），并连带让 dwm 生成 Ghost 替身窗口，
#   把该区域的所有点击全部吃掉（用户体感：又卡死又点不动）。
#   改走本地 HTTP 后，坐标采集与壳的窗口消息循环彻底解耦，这条卡死路径被移除。
#   页面的触发日志本来就是走 HTTP 的、一直很稳定，这里复用同一条通道。
UI = {"ts": 0.0, "mic": None, "mode": None, "vw": 0, "vh": 0}
SHELL_BEAT = {"ts": 0.0}


@app.post("/api/ui/rects")
def api_ui_rects_post():
    """宠物页面定时上报语音控件的真实位置（CSS 像素 + 视口尺寸）。"""
    body = request.get_json(force=True, silent=True) or {}
    mic = body.get("mic")
    mode = body.get("mode")
    old = UI["mic"]
    UI["mic"] = [float(v) for v in mic] if mic else None
    UI["mode"] = [float(v) for v in mode] if mode else None
    UI["vw"] = int(body.get("vw") or 0)
    UI["vh"] = int(body.get("vh") or 0)
    UI["ts"] = time.time()
    # 位置变化才留痕：这是判定"控件是否发生漂移"的关键证据
    if UI["mic"] and (old is None or abs(old[0] - UI["mic"][0]) > 2
                      or abs(old[1] - UI["mic"][1]) > 2):
        evlog("页", "控件位置 mic=(%.0f,%.0f,%.0f) 视口=%dx%d%s"
             % (UI["mic"][0], UI["mic"][1], UI["mic"][2], UI["vw"], UI["vh"],
                "" if old is None else "（上一处 %.0f,%.0f）" % (old[0], old[1])))
    return jsonify({"ok": True})


@app.get("/api/ui/rects")
def api_ui_rects_get():
    """桌面壳来取坐标（0.5s 一次）。age = 这份坐标有多旧（秒），-1 表示从未收到。"""
    age = (time.time() - UI["ts"]) if UI["ts"] else -1.0
    return jsonify({"ok": True, "age": age, "mic": UI["mic"], "mode": UI["mode"],
                    "vw": UI["vw"], "vh": UI["vh"]})


@app.post("/api/shell/beat")
def api_shell_beat():
    """桌面壳心跳。"""
    SHELL_BEAT["ts"] = time.time()
    return jsonify({"ok": True})


# ==================== 眼神追随（全局光标 -> 眼球朝向） ====================
# 数据流：壳 CursorPoller(20Hz, GetCursorPos) -> POST /api/gaze/cursor
#         -> 页面 150ms GET /api/gaze -> 算 yaw/pitch -> 驱动 左目/右目 骨骼。
# 与 /api/ui/rects 同向：采集在壳、消费在页面，两端都只走本地 HTTP。
#
# mode 三档（右键菜单可切，持久化到 data/gaze_mode.txt）：
#   always = 始终追（动作播放时也覆盖眼骨）
#   idle   = 只在待机时追 —— 动作素材自带眼骨轨道（实测 15 个 VMD 里 13 个有
#            右目/左目/両目 轨道，几十到几百帧），此时让位给动作原本的眼部表演
#   off    = 关闭
GAZE_MODES = ("off", "idle", "always")
GAZE_MODE_FILE = os.path.join(XIAOYUN_DIR, "data", "gaze_mode.txt")


def _load_gaze_mode():
    try:
        with open(GAZE_MODE_FILE, encoding="utf-8") as f:
            m = f.read().strip()
        return m if m in GAZE_MODES else "always"
    except Exception:
        return "always"


GAZE = {"ts": 0.0, "x": None, "y": None, "rect": None, "mode": _load_gaze_mode()}

# ==================== 点击反馈（戳一下） ====================
# 链路：壳 Catcher 判定"点在她身上" -> POST /api/poke -> 页面 150ms 轮询 /api/gaze
#       读到 poke.seq 变化 -> 播一次反应（受惊表情 + 身体后缩），不弹聊天窗。
# 为什么复用 /api/gaze 而不是单开一条轮询：页面本来就在轮询它，
#   多带两个字段零成本、零额外请求；150ms 的延迟对点击反馈是够用的（人感知阈值 ~100ms）。
# click_mode 两档（右键菜单可切，持久化到 data/click_mode.txt）：
#   poke = 单击戳一下，双击才开聊天窗（默认，避免"点一下就被窗口糊脸"）
#   chat = 老行为，单击直接开聊天窗
# x / y = 点击位置在宠物窗内的归一化坐标（0~1，壳算好后带上来）。
#   ★ 部位判定放页面做，不在这里做：页面有相机和骨骼，能拿射线打到真实网格上，
#     比后端按窗口高度猜"这是头还是身体"准得多（不同模型头身比不一样）。
#     所以后端只当搬运工，原样回传给页面。
POKE = {"seq": 0, "ts": 0.0, "x": None, "y": None}

CLICK_MODES = ("poke", "chat")
CLICK_MODE_FILE = os.path.join(XIAOYUN_DIR, "data", "click_mode.txt")

# 点击手感的力度档（右键菜单可切，持久化到 data/poke_style.txt）：
#   soft  = 克制。压扁 3%、头后仰 5°，像被打扰到，符合高冷设定（默认）
#   vivid = 明显。压扁 5.5%、过冲更大，Q 弹感强
# 数值定义在页面 POKE_STYLES 里，后端只存档位名。
POKE_STYLES = ("soft", "vivid")
POKE_STYLE_FILE = os.path.join(XIAOYUN_DIR, "data", "poke_style.txt")


def _write_text(path, text):
    """写单值配置文件，返回警告串（成功返回 ""）。

    持久化失败不该让整个切换失败 —— 本次内存值已生效，只是重启后会回到默认。
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return ""
    except Exception as e:
        return "写入失败（本次生效，重启后回到默认）: %r" % (e,)


def _load_click_mode():
    try:
        with open(CLICK_MODE_FILE, encoding="utf-8") as f:
            m = f.read().strip()
        return m if m in CLICK_MODES else "poke"
    except Exception:
        return "poke"


def _load_poke_style():
    try:
        with open(POKE_STYLE_FILE, encoding="utf-8") as f:
            s = f.read().strip()
        return s if s in POKE_STYLES else "soft"
    except Exception:
        return "soft"


CLICK = {"mode": _load_click_mode(), "style": _load_poke_style()}


@app.post("/api/gaze/cursor")
def api_gaze_cursor_post():
    """桌面壳上报全局光标位置与宠物窗屏幕矩形。

    rect = [left, top, right, bottom]（屏幕坐标）。页面用它算"光标在我窗口的哪个方向"。
    """
    body = request.get_json(force=True, silent=True) or {}
    try:
        x = int(body["x"]); y = int(body["y"])
        rect = [float(v) for v in body["rect"]]
        if len(rect) != 4:
            raise ValueError("rect 必须是 4 个数")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    GAZE["x"] = x
    GAZE["y"] = y
    GAZE["rect"] = rect
    GAZE["ts"] = time.time()
    return jsonify({"ok": True})


@app.get("/api/gaze")
def api_gaze_get():
    """页面取光标数据。age = 数据有多旧（秒），-1 表示从未收到。

    页面按 age 判断数据新鲜度：超过 ~1s 不上报（壳挂了/窗口没了）就让眼神回中位。
    mode 顺路带回去 —— 页面本来就在轮询，省一个请求，切模式 150ms 内生效。
    poke 同理：页面的点击反馈靠 seq 变化触发，并进这一次轮询就不用单开通道。
    """
    age = (time.time() - GAZE["ts"]) if GAZE["ts"] else -1.0
    return jsonify({"ok": True, "age": age, "x": GAZE["x"], "y": GAZE["y"],
                    "rect": GAZE["rect"], "mode": GAZE["mode"],
                    "poke": {"seq": POKE["seq"],
                             "age": (time.time() - POKE["ts"]) if POKE["ts"] else -1.0,
                             "x": POKE["x"], "y": POKE["y"]},
                    "click_style": CLICK["style"]})


@app.post("/api/gaze/config")
def api_gaze_config_set():
    """切换眼神模式（壳的右键菜单调用），写持久化文件，页面下次轮询即跟随。"""
    body = request.get_json(force=True, silent=True) or {}
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in GAZE_MODES:
        return jsonify({"ok": False, "error": "mode 必须是 %s" % " / ".join(GAZE_MODES)}), 400
    GAZE["mode"] = mode
    warn = ""
    try:
        os.makedirs(os.path.dirname(GAZE_MODE_FILE), exist_ok=True)
        with open(GAZE_MODE_FILE, "w", encoding="utf-8") as f:
            f.write(mode)
    except Exception as e:
        warn = "写入失败（本次生效，重启后回到默认）: %r" % (e,)
    labels = {"off": "关闭", "idle": "只在待机时追", "always": "始终追"}
    evlog("壳" if request.headers.get("X-Shell") else "页", "眼神模式 -> %s" % labels[mode])
    return jsonify({"ok": True, "mode": mode, "label": labels[mode], "warning": warn})


@app.post("/api/poke")
def api_poke_post():
    """壳上报"戳了她一下"。自增 seq，并顺带记下点在哪儿 —— 页面靠这两样播反应。

    x / y 是宠物窗内的归一化坐标（0~1）。缺省或非法时置 None，页面退回"当身体处理"，
    绝不会因为拿不到坐标就不播反应（点击反馈是旁路，宁可粗糙也不能哑火）。

    ★ 故意不在这里做限流/去重：每次真实的点击都应该有反馈，
      连点的观感（受惊 -> 嫌弃）由页面按时间间隔决定，后端不猜。
    """
    body = request.get_json(force=True, silent=True) or {}
    POKE["seq"] += 1
    POKE["ts"] = time.time()
    x, y = body.get("x"), body.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) \
            and not isinstance(x, bool) and not isinstance(y, bool):
        POKE["x"] = round(max(0.0, min(1.0, float(x))), 4)
        POKE["y"] = round(max(0.0, min(1.0, float(y))), 4)
    else:
        POKE["x"] = POKE["y"] = None
    return jsonify({"ok": True, "seq": POKE["seq"], "x": POKE["x"], "y": POKE["y"]})


@app.get("/api/click")
def api_click_get():
    """当前点击行为模式 + 手感档（壳启动时拉一次，页面走 /api/gaze 拿 style）。"""
    return jsonify({"ok": True, "mode": CLICK["mode"], "modes": list(CLICK_MODES),
                    "style": CLICK["style"], "styles": list(POKE_STYLES)})


@app.post("/api/click/config")
def api_click_config_set():
    """切换点击行为 / 点击手感（壳的右键菜单调用），写持久化文件。

    mode 与 style 都是可选的，至少给一个；两个一起给也支持。
    """
    body = request.get_json(force=True, silent=True) or {}
    mode = str(body.get("mode") or "").strip().lower()
    style = str(body.get("style") or "").strip().lower()
    if not mode and not style:
        return jsonify({"ok": False, "error": "需要 mode 或 style 之一"}), 400
    if mode and mode not in CLICK_MODES:
        return jsonify({"ok": False,
                        "error": "mode 必须是 %s" % " / ".join(CLICK_MODES)}), 400
    if style and style not in POKE_STYLES:
        return jsonify({"ok": False,
                        "error": "style 必须是 %s" % " / ".join(POKE_STYLES)}), 400

    warns = []
    labels = {"poke": "戳一下（双击开聊天窗）", "chat": "直接打开聊天窗"}
    style_labels = {"soft": "克制", "vivid": "明显"}
    ev = []
    if mode:
        CLICK["mode"] = mode
        warns.append(_write_text(CLICK_MODE_FILE, mode))
        ev.append("点击行为 -> %s" % labels[mode])
    if style:
        CLICK["style"] = style
        warns.append(_write_text(POKE_STYLE_FILE, style))
        ev.append("点击手感 -> %s" % style_labels[style])
    for e in ev:
        evlog("壳", e)
    return jsonify({"ok": True, "mode": CLICK["mode"], "style": CLICK["style"],
                    "label": labels.get(CLICK["mode"], ""),
                    "style_label": style_labels.get(CLICK["style"], ""),
                    "warning": "；".join(w for w in warns if w)})


# ==================== 人物模型（桌面形象切换） ====================
# 桌面宠物的 3D 形象清单（PMX，MMDLoader 加载）。pmx 路径相对 /assets/。
# ★ 2026-10-18 起改为**目录自动发现**：把解压后的模型目录整个放进
#   assets/models/<key>/（或 assets/peier/），无需改代码 —— 后端扫描目录里
#   体积最大的 pmx 作为主模型，武器/道具 pmx 自动排除。
#   可选：在模型目录里放 model.json 覆盖元信息：{"label":"显示名","pmx":"子路径.pmx","params":{...}}
# params 是与 desktoppmx.html 观感参数同名的每模型微调：
#   yaw=朝向(弧度) margin=取景留白 nudge_x/nudge_y=平移微调(占身高比例) arm_drop=手臂下压
MODEL_DIR = os.path.join(XIAOYUN_DIR, "assets", "models")
ASSETS_DIR = os.path.join(XIAOYUN_DIR, "assets")   # VRM 单文件直接放这里
MODEL_EXTRA_DIRS = [os.path.join(XIAOYUN_DIR, "assets", "peier")]   # 不在 models/ 下的历史模型

# 老模型的显示名与观感参数（没有 model.json 时兜底；新模型直接读 model.json）
# order 只影响菜单排序：小在前。新模型 10、老模型 30，新装的总是排在前面。
BUILTIN_MODEL_META = {
    "peier":     {"label": "姵儿（模之屋官方）", "params": {}, "order": 30},
    "ganyu":     {"label": "甘雨（原神）", "params": {}, "order": 30},
    "huangquan": {"label": "黄泉（星穹铁道）", "params": {}, "order": 30},
    "raiden":    {"label": "雷电将军（原神）", "params": {}, "order": 30},
    "kafka":     {"label": "卡芙卡（星穹铁道）", "params": {}, "order": 30},
    "qianxi":    {"label": "千咲（鸣潮 1.03）", "params": {}, "order": 30},
}
# 这些 pmx 是武器/道具/占位文件，不能当人物主体
PMX_SKIP_WORDS = ("武器", "刀", "枪", "子弹", "弓", "剑", "weapon", "blank", "空白", "凑数", "球")

# VRM 进不进「人物模型」菜单的白名单。
# assets/ 下历史遗留的实验/中间产物 VRM（bjd 系列、luna、nh 系列）只是文件留着给
# 各自的预览页用（bjdvr.html / bjd3dvr.html / desktopvrm_legacy.html），
# 不参与菜单，免得列表里塞满一次性模型。
# 要放新模型进菜单：把去扩展名的文件名加到这里即可（也可以建 assets/<名字>.json
# 写 {"in_menu": true} 单独放行）。
VRM_MENU_KEYS = {"小芸", "小芸-jk"}
# 扫描时跳过的纯资源目录（只放贴图，不会有 pmx），避免每次遍历上千个文件
RES_SKIP_DIRS = {"tex", "texture", "textures", "sph", "spa", "toon", "贴图", "材质", "皮肤"}
_MODEL_CACHE = {"ts": 0.0, "list": [], "sig": None}


def _pick_pmx(model_dir):
    """在模型目录里挑主 pmx：排除武器/道具，取体积最大的那个。"""
    best, best_sz = None, -1
    for root, dirs, files in os.walk(model_dir):
        dirs[:] = [d for d in dirs if d.lower() not in RES_SKIP_DIRS]   # 贴图目录不用翻
        for fn in files:
            if not fn.lower().endswith(".pmx"):
                continue
            low = fn.lower()
            if any(w.lower() in low for w in PMX_SKIP_WORDS):
                continue
            fp = os.path.join(root, fn)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz > best_sz:
                best, best_sz = fp, sz
    return best


def _pick_vrm(model_dir):
    """在目录里挑主 .vrm（取体积最大的那个）。VRM 是单文件，无需排除资源目录。"""
    best, best_sz = None, -1
    try:
        for fn in os.listdir(model_dir):
            if not fn.lower().endswith(".vrm"):
                continue
            fp = os.path.join(model_dir, fn)
            if not os.path.isfile(fp):
                continue
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz > best_sz:
                best, best_sz = fp, sz
    except OSError:
        pass
    return best


def _dir_sig(path):
    """目录指纹：用模型文件数量 + 最新 mtime 判断是否需要重扫（避免每次请求都遍历文件树）。
    pmx 要递归（贴图/子目录多），vrm 是单文件只看本层。"""
    n, mt = 0, 0.0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d.lower() not in RES_SKIP_DIRS]
            for fn in files:
                if fn.lower().endswith((".pmx", ".vrm")):
                    n += 1
                    try:
                        mt = max(mt, os.path.getmtime(os.path.join(root, fn)))
                    except OSError:
                        pass
    except OSError:
        pass
    return (n, round(mt, 1))


def _discover_models(force=False):
    """扫描模型目录，返回 [{key,label,pmx,vrm,kind,params}]。结果缓存 5 秒（指纹不变则复用）。

    kind = "pmx" | "vrm"：桌面页据此决定用 MMDLoader 还是 VRMLoader 加载。
    VRM 是单文件，散落在 assets/ 根目录（assets/小芸.vrm），也支持 models/<key>/ 里放。
    同一目录同时存在 pmx 与 vrm 时以 pmx 优先（历史行为不变）。
    """
    now = time.time()
    dirs = [MODEL_DIR, ASSETS_DIR] + MODEL_EXTRA_DIRS
    sig = tuple(_dir_sig(d if os.path.isdir(d) else "") for d in dirs)
    if (not force and _MODEL_CACHE["sig"] == sig
            and now - _MODEL_CACHE["ts"] < 5.0 and _MODEL_CACHE["list"]):
        return _MODEL_CACHE["list"]

    out = []
    # models/<key>/ 形式
    if os.path.isdir(MODEL_DIR):
        for key in sorted(os.listdir(MODEL_DIR)):
            d = os.path.join(MODEL_DIR, key)
            if not os.path.isdir(d):
                continue
            meta = {}
            mp = os.path.join(d, "model.json")
            if os.path.isfile(mp):
                try:
                    with open(mp, encoding="utf-8") as f:
                        meta = json.load(f) or {}
                except Exception as e:
                    print("[models] model.json 解析失败 %s: %r" % (key, e))
            pmx = meta.get("pmx")
            if pmx:
                rel = os.path.join("models", key, *pmx.replace("\\", "/").split("/"))
                if not os.path.isfile(os.path.join(XIAOYUN_DIR, "assets", rel)):
                    print("[models] %s 指定的 pmx 不存在，回退自动挑选：%s" % (key, pmx))
                    pmx = None
            if not pmx:
                fp = _pick_pmx(d)
                if not fp:
                    continue
                rel = os.path.relpath(fp, os.path.join(XIAOYUN_DIR, "assets"))
            base = BUILTIN_MODEL_META.get(key) or {}
            out.append({
                "key": key,
                "label": meta.get("label") or base.get("label") or os.path.splitext(os.path.basename(rel))[0],
                "pmx": rel.replace("\\", "/"),
                "vrm": "",
                "kind": "pmx",
                "params": meta.get("params") or base.get("params") or {},
                "order": meta.get("order") or base.get("order") or 100,
            })
    # 散落在 assets/ 下的老目录（如 assets/peier/）
    for d in MODEL_EXTRA_DIRS:
        if not os.path.isdir(d):
            continue
        key = os.path.basename(d)
        fp = _pick_pmx(d)
        if not fp:
            continue
        meta = {}
        mp = os.path.join(d, "model.json")   # 老模型也可在自身目录里放 model.json（存调色）
        if os.path.isfile(mp):
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f) or {}
            except Exception as e:
                print("[models] model.json 解析失败 %s: %r" % (key, e))
        base = BUILTIN_MODEL_META.get(key) or {}
        out.append({
            "key": key,
            "label": meta.get("label") or base.get("label") or key,
            "pmx": os.path.relpath(fp, os.path.join(XIAOYUN_DIR, "assets")).replace("\\", "/"),
            "vrm": "",
            "kind": "pmx",
            "params": meta.get("params") or base.get("params") or {},
            "order": meta.get("order") or base.get("order") or 30,
        })

    # ---- VRM：assets/*.vrm 单文件 ----
    # key 用去扩展名的文件名（assets/小芸-jk.vrm -> key "小芸-jk"），
    # 与 models/<key>/ 的 key 空间合并；同名冲突时 VRM 让位（PMX 已在列表里）。
    taken = {m["key"] for m in out}
    if os.path.isdir(ASSETS_DIR):
        for fn in sorted(os.listdir(ASSETS_DIR)):
            if not fn.lower().endswith(".vrm"):
                continue
            fp = os.path.join(ASSETS_DIR, fn)
            if not os.path.isfile(fp):
                continue
            key = os.path.splitext(fn)[0]
            if key in taken:
                continue
            taken.add(key)
            base = BUILTIN_MODEL_META.get(key) or {}
            # 同目录同名 model.json 可覆盖显示名/观感参数
            meta = {}
            mp = os.path.join(ASSETS_DIR, key + ".json")
            if os.path.isfile(mp):
                try:
                    with open(mp, encoding="utf-8") as f:
                        meta = json.load(f) or {}
                except Exception as e:
                    print("[models] %s.json 解析失败: %r" % (key, e))
            # 白名单：不在 VRM_MENU_KEYS 里、且 model.json 没显式 in_menu 的，不进菜单
            if key not in VRM_MENU_KEYS and not meta.get("in_menu"):
                continue
            out.append({
                "key": key,
                "label": meta.get("label") or base.get("label") or key,
                "pmx": "",
                "vrm": fn,
                "kind": "vrm",
                "params": meta.get("params") or base.get("params") or {},
                "order": meta.get("order") or base.get("order") or 20,
            })

    out.sort(key=lambda m: (m.get("order", 100), m["label"]))
    _MODEL_CACHE.update({"ts": now, "list": out, "sig": sig})
    if not out:
        print("[models] 警告：没扫描到任何模型，页面会回退到内置地址")
    return out


def _models():
    return _discover_models()


MODEL_STATE = os.path.join(XIAOYUN_DIR, "assets", "models", "current.txt")


def _current_model_key():
    try:
        with open(MODEL_STATE, encoding="utf-8") as f:
            k = f.read().strip()
        lst = _models()
        if any(m["key"] == k for m in lst):
            return k
    except Exception:
        pass
    lst = _models()
    return lst[0]["key"] if lst else ""


def _set_model_key(key):
    with open(MODEL_STATE, "w", encoding="utf-8") as f:
        f.write(key)


@app.get("/api/models")
def api_models():
    """人物模型清单 + 当前选择（桌面壳右键菜单/托盘菜单、页面自动重载都用它）。"""
    cur = _current_model_key()
    lst = _models()
    return jsonify({
        "ok": True,
        "current": cur,
        "auto": True,          # 清单由目录扫描得出，新放进去的模型无需改代码
        "models": [{"key": m["key"], "label": m["label"], "pmx": m["pmx"],
                    "vrm": m.get("vrm") or "", "kind": m.get("kind") or "pmx",
                    "params": m.get("params") or {}} for m in lst],
    })


@app.post("/api/model")
def api_model_set():
    """切换人物模型：写持久化文件；页面轮询到 current 变化后自动重载新模型。"""
    body = request.get_json(force=True, silent=True) or {}
    key = str(body.get("key") or "")
    m = next((m for m in _models() if m["key"] == key), None)
    if not m:
        return jsonify({"ok": False, "error": "没有这个模型: %s" % key}), 404
    try:
        os.makedirs(os.path.dirname(MODEL_STATE), exist_ok=True)
        _set_model_key(key)
    except Exception as e:
        return jsonify({"ok": False, "error": "写入选择失败: %r" % e}), 500
    evlog("壳" if request.headers.get("X-Shell") else "页", "切换人物模型 -> %s" % m["label"])
    return jsonify({"ok": True, "current": key, "label": m["label"]})


# ==================== 调色（亮度 / 饱和度 / 对比度 / 伽马） ====================
# 只动"观感"这一个维度：参数写进 models/<key>/model.json 的 params，
# **绝不碰 assets/motions（动作库）**，也不写动作 current.txt。
# 页面用 /api/grade 轮询：调参窗里拖一下，桌面上的她 1 秒内跟着变色，不用重启。
GRADE_KEYS = ("contrast", "saturate", "gamma", "bright")
GRADE_DEF = {"contrast": 1.20, "saturate": 1.18, "gamma": 0.85, "bright": 1.00}
GRADE_LIM = {"contrast": (0.30, 3.00), "saturate": (0.30, 3.00),
             "gamma": (0.40, 1.60), "bright": (0.60, 1.60)}
GRADE_LIVE = {}        # key -> 四个颜色参数（运行期覆盖，不落盘也算数）
GRADE_SEQ = [0]        # 每次改动 +1；页面看它变没变来决定要不要重设材质


def _grade_json_path(key):
    d = os.path.join(MODEL_DIR, key)
    if os.path.isdir(d):
        return os.path.join(d, "model.json")
    # 历史模型（assets/peier 之类）就存在它自己的目录里
    for ed in MODEL_EXTRA_DIRS:
        if os.path.basename(ed) == key and os.path.isdir(ed):
            return os.path.join(ed, "model.json")
    return os.path.join(MODEL_DIR, key, "model.json")


def _read_model_meta(key):
    """读 models/<key>/model.json（不存在/坏了都返回空 dict）。"""
    p = _grade_json_path(key)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print("[grade] %s 解析失败: %r" % (key, e))
        return {}


def _grade_of(key):
    """某模型当前生效的颜色参数：model.json 里的值打底，运行期覆盖优先。"""
    out = dict(GRADE_DEF)
    p = (_read_model_meta(key).get("params") or {})
    for k in GRADE_KEYS:
        if isinstance(p.get(k), (int, float)) and not isinstance(p.get(k), bool):
            out[k] = round(float(p[k]), 3)
    for k, v in (GRADE_LIVE.get(key) or {}).items():
        if k in GRADE_KEYS:
            out[k] = round(float(v), 3)
    return out


def _clamp_grade(raw):
    """只认四个颜色键，顺手夹到安全区间（防止拖出一个把画面烧白的值）。"""
    out = {}
    for k in GRADE_KEYS:
        if k not in raw:
            continue
        try:
            v = float(raw[k])
        except (TypeError, ValueError):
            continue
        lo, hi = GRADE_LIM[k]
        out[k] = round(min(hi, max(lo, v)), 3)
    return out


def _save_grade(key, params):
    """把颜色参数合并进 model.json，其余键（pmx/label/order/margin/...）原样保留。"""
    path = _grade_json_path(key)
    if not os.path.isdir(os.path.dirname(path)):
        return False, "该模型不在 models/ 目录下，没有 model.json 可写"
    meta = _read_model_meta(key)
    p = dict(meta.get("params") or {})
    for k in GRADE_KEYS:
        if k in params:
            p[k] = params[k]
    meta.setdefault("key", key)
    meta["params"] = p
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        _MODEL_CACHE.update({"ts": 0.0, "sig": None})   # 让 /api/models 重新扫
        return True, ""
    except Exception as e:
        return False, "写入失败: %r" % (e,)


@app.get("/api/grade")
def api_grade():
    """某模型当前的颜色参数（页面轮询这个：seq 变了才重设材质）。"""
    key = str(request.args.get("model") or "").strip() or _current_model_key()
    return jsonify({"ok": True, "model": key, "seq": GRADE_SEQ[0],
                    "defaults": GRADE_DEF, "limits": GRADE_LIM,
                    "params": _grade_of(key)})


@app.post("/api/grade")
def api_grade_set():
    """实时调色：body = {model, params:{contrast,saturate,gamma,bright}, save}
    save=true 才写 model.json；不带 save 只在内存里生效（拖动滑块时的用法）。"""
    body = request.get_json(force=True, silent=True) or {}
    key = str(body.get("model") or "").strip() or _current_model_key()
    patch = _clamp_grade(body.get("params") or {k: body.get(k) for k in GRADE_KEYS})
    if not patch:
        return jsonify({"ok": False, "error": "没有可用的颜色参数"}), 400
    cur = _grade_of(key)
    cur.update(patch)
    GRADE_LIVE[key] = cur
    GRADE_SEQ[0] += 1
    saved, err = False, ""
    if body.get("save"):
        saved, err = _save_grade(key, cur)
        if saved:
            evlog("壳" if request.headers.get("X-Shell") else "页",
                  "调色已保存 %s: %s" % (key, json.dumps(cur, ensure_ascii=False)))
    return jsonify({"ok": True, "model": key, "seq": GRADE_SEQ[0],
                    "params": cur, "saved": saved, "error": err or None})


# ==================== 动作（VMD 舞蹈 / 待机） ====================
# assets/motions/ 下每个 .vmd 就是一个动作。可选的同名 .json 提供显示名：
#   {"label":"灰姑娘（140 秒）","note":"loop"}
# 当前选择记在 assets/motions/current.txt，值为动作 key；"idle" = 内置待机微动作。
MOTION_DIR = os.path.join(XIAOYUN_DIR, "assets", "motions")
MOTION_STATE = os.path.join(MOTION_DIR, "current.txt")
MOTION_IDLE = "idle"


def _list_motions():
    """扫描动作目录 -> [{key,label,file,url,note}]（按 label 排序，idle 不在此列）。"""
    out = []
    if not os.path.isdir(MOTION_DIR):
        return out
    for fn in sorted(os.listdir(MOTION_DIR)):
        if not fn.lower().endswith(".vmd"):
            continue
        key = os.path.splitext(fn)[0]
        meta = {}
        jp = os.path.join(MOTION_DIR, key + ".json")
        if os.path.isfile(jp):
            try:
                with open(jp, encoding="utf-8") as f:
                    meta = json.load(f) or {}
            except Exception as e:
                print("[motions] %s.json 解析失败: %r" % (key, e))
        out.append({
            "key": key,
            "label": meta.get("label") or key,
            "file": fn,
            "url": "/assets/motions/" + fn,
            "note": meta.get("note") or "",
        })
    return out


def _current_motion_key():
    try:
        with open(MOTION_STATE, encoding="utf-8") as f:
            k = f.read().strip()
    except Exception:
        return MOTION_IDLE
    if k == MOTION_IDLE:
        return k
    return k if any(m["key"] == k for m in _list_motions()) else MOTION_IDLE


@app.get("/api/motions")
def api_motions():
    """动作清单 + 当前选择（托盘/右键菜单、宠物页轮询都用它）。"""
    lst = _list_motions()
    cur = _current_motion_key()
    return jsonify({
        "ok": True,
        "current": cur,
        "idle_label": "内置待机（呼吸/歪头/挥手）",
        "motions": lst,
    })


SHOT_FLAG_PATH = os.path.join(XIAOYUN_DIR, "_shot_flag.json")


@app.get("/api/shot_flag")
def api_shot_flag():
    """页面 autoshot 轮询：读 _shot_flag.json {"on":bool,"prefix":str,"gap":sec}。"""
    try:
        with open(SHOT_FLAG_PATH, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"on": False})


@app.post("/api/motion")
def api_motion_set():
    """切换动作：写持久化文件，宠物页 2 秒内自动切（不重载模型）。"""
    body = request.get_json(force=True, silent=True) or {}
    key = str(body.get("key") or "")
    if key != MOTION_IDLE and not any(m["key"] == key for m in _list_motions()):
        return jsonify({"ok": False, "error": "没有这个动作: %s" % key}), 404
    try:
        _set_motion_key(key)
    except Exception as e:
        return jsonify({"ok": False, "error": "写入选择失败: %r" % e}), 500
    label = "内置待机" if key == MOTION_IDLE else next(
        (m["label"] for m in _list_motions() if m["key"] == key), key)
    evlog("壳" if request.headers.get("X-Shell") else "页", "切换动作 -> %s" % label)
    return jsonify({"ok": True, "current": key, "label": label})


@app.post("/api/pose_shot")
def api_pose_shot():
    """姿势调试：接收页面 canvas 渲染帧（dataURL），存成 PNG 供肉眼验证姿势。
    桌面调试钩子（__xyPose.shot）配合使用，不影响正常运行。"""
    body = request.get_json(force=True, silent=True) or {}
    img = str(body.get("img") or "")
    name = "".join(c for c in str(body.get("name") or "shot") if c.isalnum() or c in "_-") or "shot"
    if img.startswith("data:image/png;base64,"):
        out_dir = os.path.join(XIAOYUN_DIR, "_shots")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "api_%s.png" % name), "wb") as f:
            f.write(base64.b64decode(img.split(",", 1)[1]))
    return jsonify({"ok": True})


@app.get("/api/preset")
def api_preset_list():
    """列出所有人设 + 当前生效的那套（桌面壳托盘菜单用）。"""
    return jsonify({
        "ok": True,
        "current": CFG.get("preset_key") or "",
        "label": CFG.get("preset_label") or "",
        "engine": CFG.get("tts_engine"),
        # voice = "当前引擎实际会用的音色"（不是理想音色），避免误以为已经是云端御姐音
        "voice": (CFG.get("minimax_voice") if (CFG.get("tts_engine") or "").lower() == "minimax"
                  else CFG.get("voice")),
        "preferred_voice": CFG.get("minimax_voice") or "",
        # 音色跟随人设？false = 用户在音色菜单里锁定过，换人设不再换声线
        "voice_follow": _voice_follow(),
        "volc_speaker": CFG.get("volc_speaker") or "",
        "presets": [{"key": p.get("key"), "label": p.get("label") or p.get("key"),
                     "desc": p.get("desc") or "", "engine": p.get("engine") or "edge"}
                    for p in _list_presets()],
    })


@app.post("/api/preset")
def api_preset_set():
    """切换人设（人格 + 声线一起换），热生效，不用重启后端。"""
    body = request.get_json(force=True, silent=True) or {}
    key = str(body.get("key") or "")
    if not _apply_preset(key):
        return jsonify({"ok": False, "error": "没有这套人设: %s" % key}), 404
    # 换完立刻自报一句：用户马上能听出声线和语感都变了（相当于内置试听）
    said = _say_switch_line()
    return jsonify({"ok": True, "current": key, "label": CFG.get("preset_label"),
                    "engine": CFG.get("tts_engine"),
                    "said": said,
                    "voice_follow": _voice_follow(),
                    "volc_speaker": CFG.get("volc_speaker") or "",
                    "voice": (CFG.get("minimax_voice") if (CFG.get("tts_engine") or "").lower() == "minimax"
                              else CFG.get("voice"))})


@app.get("/api/voice-options")
def api_voice_options():
    """可用音色列表 + 当前生效音色（桌面壳托盘「音色」子菜单用）。
    edge 音色 id = edge-tts voice id；本地声线 id = gsv:<key>。"""
    eng = (CFG.get("tts_engine") or "edge").lower()
    if eng == "gptsovits":
        cur = "gsv:" + (CFG.get("gsv_voice") or "")
    elif eng == "volc":
        cur = CFG.get("volc_speaker")
    else:
        cur = CFG.get("voice")
    return jsonify({
        "ok": True,
        "engine": CFG.get("tts_engine"),
        "current": cur,
        "rate": CFG.get("rate"),
        "pitch": CFG.get("pitch"),
        "options": VOICE_OPTIONS + GSV_OPTIONS + VOLC_OPTIONS,
    })


@app.post("/api/voice-options")
def api_voice_set():
    """切音色（只换声音，不动人格），热生效并持久化。
    ★ 选到 gsv: 音色时**顺手把 GPT-SoVITS 拉起来**（动态加载入口）。这里只发信号，
      不等待 —— 首次加载 30~60s，绝不能把菜单点击卡住；加载在后台跑，
      用户这期间说的话会回退 edge-tts。"""
    body = request.get_json(force=True, silent=True) or {}
    vid = str(body.get("voice") or "")
    if not _voice_option(vid):
        return jsonify({"ok": False, "error": "没有这个音色: %s" % vid}), 404
    o = _apply_voice(vid, persist=True)
    state = "idle"
    if (CFG.get("tts_engine") or "") == "gptsovits":
        if gsv_ensure_running():
            state = "up"
        else:
            state = "booting"
            threading.Thread(target=gsv_wait_ready, daemon=True).start()
    return jsonify({"ok": True, "current": vid, "label": o["label"],
                    "engine": CFG.get("tts_engine"),
                    "gsv_voice": CFG.get("gsv_voice") or "",
                    "gsv_state": state,
                    "rate": CFG.get("rate"), "pitch": CFG.get("pitch")})


# ==================== 对话模型（切换大脑） ====================
# 大脑 = or_proxy（8787）的 OpenAI 兼容接口。这里切的是「请求里带的 model」，
# 代理按 model 选上游，所以改 CFG["model"] 下一句对话就生效 —— 不重启进程、
# 不用 Swap-Brain 换血（换血只在改 or_proxy.py 代码时才需要）。
#
# ★ 候选清单不再写死（2026-10-20 重做）。写死的清单必然过期，而且错得很隐蔽：
#   上一版把「魔搭 Qwen3.5-27B / GLM-5.2」当主力列在菜单里，魔搭整档随后因
#   余额不足被 or_proxy 写进 DISABLED_UPSTREAMS —— 选中它，代理只会静默回退到
#   别家（响应 model 字段并非请求的那个），界面却显示"切换成功"。
#   同理「豆包 2.1 lite」只剩 3.6 万 tokens，已被 or_proxy 从 auto 延迟池剔除、
#   只留作候选链末位兜底，也不该再占着「当前在用」的位置。
#
# 现行做法：菜单 = 顺序表（只决定"谁值得出现、排第几"）∩ 代理实况。
#   实况取自 or_proxy 自己暴露的两个端点，不再手抄：
#     GET /v1/models → 每个 id 的 owned_by（落在哪一档上游）＝ 存在且可选
#     GET /health    → disabled_upstreams（整档禁用）、auto_routing.latency
#                      （各模型真实 EMA 延迟/采样数）、quota_cooldown（熔断中）
#   代理改一次注册表，设置页与托盘菜单自动跟上。
#   取不到代理（没起）时退回顺序表原样展示，并用 live=False 明说"可能过期"。
UPSTREAM_LABELS = {
    "ark": "火山方舟",
    "arkplan": "Agent Plan",
    "openrouter": "OpenRouter",
    "modelscope": "魔搭",
}

# 顺序表：(model id, 菜单名)。auto 固定第一 —— 它是路由策略，不是具体模型。
# 其余按「桌宠对话」取舍：短句、口语、首 token 快；命名统一「厂商 档位」。
CHAT_MODEL_PREFERRED = [
    ("auto", "自动 · 按延迟择优"),
    ("deepseek-v4-1-flash-economy-261017", "DeepSeek V4.1 经济档"),
    ("doubao-seed-character-260628", "豆包 Character 2.0"),
    ("doubao-seed-2-0-mini-260428", "豆包 2.0 mini"),
    ("doubao-seed-2-1-pro-260915", "豆包 2.1 Pro"),
    ("deepseek-v4-1-flash-261017", "DeepSeek V4.1 Flash"),
    ("deepseek-v4-pro-ga-260813", "DeepSeek V4 Pro"),
    ("glm-5-3-flash-260828", "GLM-5.3 Flash"),
    ("doubao-seed-2-1-lite-260915", "豆包 2.1 lite · 额度告急"),
    ("deepseek-v4.1-flash", "DeepSeek V4.1 Flash · Plan"),
    ("doubao-seed-2.1-pro", "豆包 2.1 Pro · Plan"),
]

_CHAT_OPT_CACHE = {"ts": 0.0, "ttl": 3.0, "val": None}


def _brain_live():
    """拉一次 or_proxy 实况：返回 (models: {id: owned_by} | None, health: dict)。

    models 为 None 表示代理没起/取不到 —— 调用方据此降级，而不是猜。
    优先走 /registry（纯本地注册表，毫秒级）；老版本代理没有这个端点（404），
    才退回 /v1/models —— 后者会真去 OpenRouter 拉 :free 清单，超时 30s，
    外网一抖动就把"健康探测"拖成 2s 超时，误判成"代理没起"。"""
    root = CFG["base"].rstrip("/")
    models = None
    try:
        r = requests.get(root + "/registry", timeout=3)
        if r.status_code == 200:
            reg = r.json() or {}
            models = {}
            for up, ids in (reg.get("models") or {}).items():
                for mid in (ids or []):
                    models[mid] = up
    except Exception:
        models = None
    if models is None:
        try:
            r = requests.get(root + "/models", timeout=6)
            if r.status_code == 200:
                models = {}
                for m in (r.json().get("data") or []):
                    mid = m.get("id")
                    if mid:
                        models[mid] = m.get("owned_by") or ""
        except Exception:
            models = None
    try:
        health = requests.get(BRAIN_HEALTH_URL, timeout=2).json() or {}
    except Exception:
        health = {}
    return models, health


def _model_note(mid, owner, lat, cooldown, live):
    """一句话说明这个模型"现在什么状态"：延迟来自真实采样，不是拍脑袋的常数。"""
    bits = []
    info = lat.get(mid) or {}
    ema = float(info.get("ema_s") or 0)
    if ema:
        bits.append("实测 %.2fs" % ema)
    n = int(info.get("samples") or 0)
    if n:
        bits.append("%d 次采样" % n)
    elif live and mid:
        bits.append("本轮未采样")
    cf = int(info.get("consec_fail") or 0)
    if cf:
        bits.append("连续失败 %d 次" % cf)
    left = cooldown.get(mid)
    if left:
        bits.append("额度熔断中·剩 %d 分钟" % max(1, int(left) // 60))
    if owner:
        bits.append("上游 " + UPSTREAM_LABELS.get(owner, owner))
    return " · ".join(bits)


def _chat_model_state(force=False):
    """返回 (options, ctx)。成功缓存 3 秒、失败缓存 15 秒 —— 代理没起时不要
    让设置页的轮询每 30 秒去撞一次慢端点。"""
    now = time.time()
    if (not force and _CHAT_OPT_CACHE["val"]
            and now - _CHAT_OPT_CACHE["ts"] < _CHAT_OPT_CACHE["ttl"]):
        return _CHAT_OPT_CACHE["val"]

    models, health = _brain_live()
    live = models is not None
    disabled = set(health.get("disabled_upstreams") or [])
    active = [n for n in (health.get("upstream_priority") or []) if n not in disabled]
    rt = health.get("auto_routing") or {}
    lat = rt.get("latency") or {}
    cooldown = health.get("quota_cooldown") or {}
    best = rt.get("best_now") or ""

    opts = []
    name_of = dict(CHAT_MODEL_PREFERRED)
    for mid, name in CHAT_MODEL_PREFERRED:
        if mid == "auto":
            note = "按各上游 EMA 延迟自动选最快的"
            if best:
                note += "（当前会选 %s）" % name_of.get(best, best)
            opts.append({"id": mid, "label": name, "owner": "", "upstream": "混合",
                         "latency": round(float((lat.get(mid) or {}).get("ema_s") or 0), 2),
                         "note": note})
            continue
        owner = models.get(mid, "") if live else ""
        # 代理里没有这个 id，或它所属那档上游整体被禁用 → 不列（列了就是骗人）
        if live and (mid not in models or owner in disabled):
            continue
        opts.append({"id": mid, "label": name, "owner": owner,
                     "upstream": UPSTREAM_LABELS.get(owner, owner),
                     "latency": round(float((lat.get(mid) or {}).get("ema_s") or 0), 2),
                     "note": _model_note(mid, owner, lat, cooldown, live)
                             or "静态兜底（代理未响应，未校验）"})

    ctx = {"live": live, "models": models or {}, "disabled": disabled,
           "active": active, "lat": lat, "cooldown": cooldown,
           "best_now": best,
           "best_latency": round(float((lat.get(best) or {}).get("ema_s") or 0), 2)}
    val = (opts, ctx)
    _CHAT_OPT_CACHE.update(ts=now, ttl=(3.0 if live else 15.0), val=val)
    return val


def _chat_model_option(mid):
    opts, _ctx = _chat_model_state()
    for m in opts:
        if m["id"] == mid:
            return m
    return None


def _probe_chat_model(mid, timeout=45):
    """真打一次最小请求，返回 (ok, used_model, 延迟秒, 错误)。

    为什么要核对响应里的 model：代理在目标模型不可用（额度耗尽/上游 5xx）时
    会**静默回退**到候选链的下一个模型，HTTP 依然是 200。只看状态码会以为
    "切成功了"，实际跑的根本不是它。"""
    url = CFG["base"].rstrip("/") + "/chat/completions"
    t0 = time.time()
    try:
        r = requests.post(url, headers={"Authorization": "Bearer sk-local"},
                          json={"model": mid, "max_tokens": 8, "temperature": 0,
                                "messages": [{"role": "user", "content": "回一个字：好"}]},
                          timeout=timeout)
        took = time.time() - t0
        if r.status_code != 200:
            return False, "", took, "HTTP %d %s" % (r.status_code, _pick_error(r))
        return True, (r.json().get("model") or ""), took, ""
    except Exception as e:                                   # noqa: BLE001
        return False, "", time.time() - t0, repr(e)


def _save_chat_model(mid):
    """把选择写进 config.json（openai.model + chatgpt.model 两处，兼容旧读取方）。"""
    try:
        d = json.load(open(CONFIG_PATH, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("openai", {})["model"] = mid
    d.setdefault("chatgpt", {})["model"] = mid
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


@app.get("/api/chat-model")
def api_chat_model_list():
    """对话模型清单 + 当前选择（设置页 /settings 的「模型」卡 + 桌面壳托盘子菜单）。

    清单来自 _chat_model_state()：顺序表 ∩ or_proxy 实况。返回里额外给出
      live / upstreams / warnings —— 让界面能说清"这台代理现在是什么状态"，
      而不是摆一排漂亮的选项让人以为都能用。"""
    cur = CFG.get("model") or "auto"
    opts, ctx = _chat_model_state()
    o = next((x for x in opts if x["id"] == cur), None)

    warnings = []
    if not ctx["live"]:
        warnings.append("连不上大脑（or_proxy %s），下面是静态兜底清单，可能已过期"
                        % CFG["base"])
    stale = False
    if o is None:
        # 当前选择已不在可用清单里（模型下线/上游被禁用）。必须照样返回并插在
        # auto 后面 —— 否则前端 <select> 找不到匹配项会静默显示成第一项，
        # 界面说的和真正在跑的不是一个东西。
        owner = (ctx["models"] or {}).get(cur, "")
        o = {"id": cur, "label": cur, "owner": owner,
             "upstream": UPSTREAM_LABELS.get(owner, owner),
             "latency": round(float(((ctx["lat"] or {}).get(cur) or {}).get("ema_s") or 0), 2),
             "note": "代理已不再提供该模型（下线或上游整档禁用）",
             "stale": True}
        opts.insert(1 if opts and opts[0]["id"] == "auto" else 0, o)
        stale = True
        warnings.append("当前模型「%s」已不可用：每轮对话都会被代理静默回退到别家，"
                        "建议改选「自动 · 按延迟择优」" % cur)
    left = (ctx["cooldown"] or {}).get(cur)
    if left:
        warnings.append("当前模型额度熔断中（剩 %d 分钟），期间会被跳过"
                        % max(1, int(left) // 60))

    out = {"ok": True, "current": cur, "stale": stale,
           "label": o.get("label") or cur,
           "upstream": o.get("upstream") or "",
           "brain": CFG["base"],
           "live": ctx["live"],
           "best_now": ctx["best_now"],
           "best_latency": ctx["best_latency"],
           "upstreams": [{"name": n, "label": UPSTREAM_LABELS.get(n, n),
                          "active": n in ctx["active"]}
                         for n in (ctx["active"] + [
                             n for n in ("openrouter", "ark", "arkplan", "modelscope")
                             if n not in ctx["active"]])],
           "warnings": warnings,
           "options": opts}
    return jsonify(out)


@app.post("/api/chat-model")
def api_chat_model_set():
    """切换对话模型：先按代理实况校验可选性，再真打一次探活，最后写入并持久化。"""
    body = request.get_json(force=True, silent=True) or {}
    mid = str(body.get("model") or "")
    opts, ctx = _chat_model_state(force=True)
    o = next((x for x in opts if x["id"] == mid), None)
    # 大脑没起时清单只是静态兜底：这时候放行具体模型，等于让可能失效的旧名单
    # 再把配置带回"选了但不可用"的老坑。auto 是路由策略、永远合法，予以放行。
    if not ctx["live"] and mid != "auto":
        return jsonify({"ok": False,
                        "error": "大脑（or_proxy）没响应，无法确认「%s」是否仍可用；"
                                 "先启动或检查 8787，或改选「自动 · 按延迟择优」" % mid}), 503
    if not o:
        owner = (ctx["models"] or {}).get(mid)
        if not ctx["live"]:
            why = "（大脑没起，清单是静态兜底，不接受新选择）"
        elif owner and owner in ctx["disabled"]:
            why = "（它所属的「%s」整档已被禁用）" % UPSTREAM_LABELS.get(owner, owner)
        elif owner:
            why = "（代理有该模型，但不在菜单顺序表 CHAT_MODEL_PREFERRED 里）"
        else:
            why = "（代理没有这个模型 id）"
        return jsonify({"ok": False, "error": "不可选：%s%s" % (mid, why)}), 404
    if o.get("stale"):
        return jsonify({"ok": False,
                        "error": "该模型已下线或上游禁用，不能选"}), 409
    ok, used, took, err = _probe_chat_model(mid)
    if ok and mid != "auto" and used and used != mid:
        evlog("后端", "对话模型切换被拒：%s 不可用，代理回退到了 %s" % (mid, used))
        return jsonify({"ok": False, "used": used,
                        "error": "目标模型当前不可用（代理已回退到 %s），已保持原设置" % used}), 409

    verified = bool(ok and (mid == "auto" or used == mid))
    CFG["model"] = mid
    _save_chat_model(mid)
    evlog("后端", "对话模型已切换 -> %s（上游 %s｜实测 %s｜实际应答 %s）"
         % (o["label"], o["upstream"], ("%.2fs" % took) if ok else "未验证", used or "?"))
    warn = ""
    if not verified:
        warn = ("探活失败但已切换（大脑可能没起）：%s" % err) if err else "探活未通过校验"
    return jsonify({"ok": True, "current": mid, "label": o["label"],
                    "used": used, "latency": round(took, 2),
                    "verified": verified, "warning": warn})


@app.post("/api/event")
def api_event():
    """前端页面 / 桌面壳上报的触发事件，统一落到 前端事件日志.txt。"""
    body = request.get_json(force=True, silent=True) or {}
    src = str(body.get("src") or "页")
    t = str(body.get("type") or "?")
    d = str(body.get("detail") or "")
    evlog(src, ("%s %s" % (t, d)).strip())
    return jsonify({"ok": True})


# ==================== token 用量看板 ====================
# 数据源只有一个：or_proxy /health 里的 token_usage（按模型累计
# prompt/completion/total/calls）。代理那边是在响应里抓 usage 记账的，流式靠
# 注入 stream_options.include_usage 才拿得到 —— 也就是说这个数是"真的发出去的
# token"，不是按字数估的。
#
# ★ 口径必须在界面上写清楚，否则会误导人：
#   1) 进程内累计，代理一重启就归零；
#   2) 方舟除控制台外没有额度查询接口（/usage、/account、/balance 全 404），
#      所以这里的"占额度"只是本进程记到的量 / 50 万，不等于控制台账单。
# 这两个前提决定了它适合看"谁在烧、烧得多快"，不适合当结算依据。
QUOTA_FREE_TOKENS = 500000                 # 方舟每个模型独立的 50 万 tokens
QUOTA_UPSTREAMS = ("ark", "arkplan")       # 只有方舟系按"每模型 50 万"算


@app.get("/api/usage")
def api_usage():
    """各模型 token 用量看板（设置页 /settings「用量」卡）。"""
    try:
        health = requests.get(BRAIN_HEALTH_URL, timeout=2).json() or {}
    except Exception:
        health = {}
    opts, ctx = _chat_model_state()
    known = {o["id"]: o for o in opts}
    usage = health.get("token_usage")
    rows = []
    if isinstance(usage, dict):
        for mid, e in usage.items():
            e = e or {}
            o = known.get(mid) or {}
            owner = (ctx["models"] or {}).get(mid) or o.get("owner") or ""
            total = int(e.get("total") or 0)
            calls = int(e.get("calls") or 0)
            quota = QUOTA_FREE_TOKENS if owner in QUOTA_UPSTREAMS else 0
            rows.append({
                "id": mid,
                "label": o.get("label") or mid,
                "owner": owner,
                "upstream": UPSTREAM_LABELS.get(owner, owner) or "-",
                "prompt": int(e.get("prompt") or 0),
                "completion": int(e.get("completion") or 0),
                "total": total,
                "calls": calls,
                "avg": round(total * 1.0 / calls, 1) if calls else 0.0,
                "quota": quota,
                "quota_pct": round(total * 100.0 / quota, 2) if quota else None,
                "cooling": int((ctx["cooldown"] or {}).get(mid) or 0),
            })
    rows.sort(key=lambda r: r["total"], reverse=True)
    tot = sum(r["total"] for r in rows) or 1
    bar_ref = max([r["total"] for r in rows], default=0) or 1
    for r in rows:
        # bar：相对"烧得最多的那个"，一眼看出量级差
        # share：占全部用量的百分比
        r["bar"] = round(r["total"] * 100.0 / bar_ref, 1)
        r["share"] = round(r["total"] * 100.0 / tot, 1)
    calls = sum(r["calls"] for r in rows)
    return jsonify({
        "ok": True,
        "live": bool(health),
        "supported": isinstance(usage, dict),
        "uptime_sec": int(health.get("uptime_sec") or 0),
        "requests": int(health.get("requests") or 0),
        "errors": int(health.get("errors") or 0),
        "fallbacks": int(health.get("fallbacks") or 0),
        "quota_free": QUOTA_FREE_TOKENS,
        "sum": {"total": sum(r["total"] for r in rows),
                "prompt": sum(r["prompt"] for r in rows),
                "completion": sum(r["completion"] for r in rows),
                "calls": calls, "models": len(rows),
                "avg": round(sum(r["total"] for r in rows) * 1.0 / calls, 1) if calls else 0.0},
        "rows": rows,
        "generated_at": int(time.time()),
    })



# 页面上报播放开始（带真实时长）。PLAYBACK 本身定义在文件顶部（必须每轮清零）。
# ★ 为什么必须由页面上报：后台消费者原来用 len(mp3)/16000 估算时长 —— 对 MP3
#   这个式子毫无意义且明显偏短（实测 37 字回复实际播约 8s，它只等 4.5s），
#   于是"她还在说话"时就把麦克风重新打开，正好录下她自己的声音并当成用户输入，
#   形成自问自答死循环（实测已复现：她的话被转写后又回一句）。
@app.post("/api/voice/playing")
def voice_playing():
    """页面上报"她正在出声"。

    ★ 流式播放必须周期性上报（每 0.5s），因为总时长要等最后一块音频排进队列才知道：
      - start 只认本轮第一次上报 —— 它是播放起点，后面的上报只刷新 dur；
      - dur 单调取最大 —— 流式只会越排越长，绝不能被后到的短值覆盖。
    这两条一起保证 `start + dur` 恒等于"预计播完时刻"，后端的等待与界面的
    「🔊 正在说… Ns」都据此算，不再靠字数估算。"""
    body = request.get_json(force=True, silent=True) or {}
    try:
        dur = float(body.get("dur") or 0.0)
    except Exception:
        dur = 0.0
    seq = body.get("seq")
    if seq is not None:
        try:
            seq = int(seq)
        except Exception:
            seq = None

    first = (PLAYBACK["start"] <= 0) or (seq is not None and PLAYBACK.get("seq") != seq)
    if first:
        PLAYBACK["start"] = time.time()
        PLAYBACK["seq"] = seq
    PLAYBACK["ts"] = time.time()
    prev = PLAYBACK["dur"] or 0.0
    PLAYBACK["dur"] = max(prev, max(0.0, min(dur, 300.0)))
    evlog("后端", "页面上报播放时长 %.2fs（seq=%s%s）"
         % (PLAYBACK["dur"], seq, "，起点已记" if first else ""))
    return jsonify({"ok": True})


@app.post("/api/voice/start")
def voice_start():
    _ensure_voice_worker()      # 开始收音前确保消费者在线（否则转写没人接）
    body = request.get_json(force=True) or {}
    mode = body.get("mode") or VOICE["mode"]
    if mode not in ("ptt", "vad"):
        mode = "ptt"
    # ★ 后台消费者正拿着一句转写在生成回复/等播放（首次对话大脑可能阻塞几十秒）。
    #   此时 ASR 显示 paused，旧的 LAST_REPLY_TS 也还是上上轮的旧值——两道旧守卫
    #   都拦不住用户此时点麦克风；而回复一到就要播放，麦克风开着必录到她自己。
    #   实测就是"第一次对话响应慢 -> 用户补点麦克风 -> 自问自答死循环"。
    if VOICE.get("busy"):
        evlog("后端", "拒绝开始(上一句回复还在生成/播放)")
        return jsonify({"ok": False,
                        "error": "她正在回复，等她说完再点"}), 425
    # 她还在说话就别急着收音：麦克风会把她自己的声音录进去，变成自问自答
    idle = time.time() - LAST_REPLY_TS
    if idle < LAST_REPLY_GUARD:
        evlog("后端", "拒绝开始(她在说话) 还需等 %.1fs" % (LAST_REPLY_GUARD - idle))
        return jsonify({"ok": False,
                        "error": "她还在说话，等 %.0f 秒再开始" % (LAST_REPLY_GUARD - idle)}), 425
    VOICE["mode"] = mode
    # 转写中/等待取走时不硬撞（ASR 会 409），先给个友好提示
    try:
        cur = _asr("/session/state", timeout=5)
        if cur.get("state") in ("thinking", "pending"):
            evlog("后端", "拒绝开始(上一句还在识别) state=%s" % cur.get("state"))
            return jsonify({"ok": False,
                            "error": "正在识别上一句，稍等一下"}), 409
    except Exception:
        pass
    try:
        j = _asr("/session/start", "POST", {"mode": mode}, timeout=15)
    except Exception as e:
        evlog("后端", "启动失败(ASR 未启动): %s" % e)
        return jsonify({"ok": False, "error": "ASR 服务未启动: %s" % e}), 503
    if not j.get("ok"):
        evlog("后端", "启动被 ASR 拒绝: %s" % j.get("msg"))
        return jsonify(j), 409
    evlog("后端", "开始收音 mode=%s" % mode)
    _set_stage("idle")          # 新一轮开始：清掉上一轮留下的阶段标记
    return jsonify({"ok": True, "mode": mode})


@app.post("/api/voice/stop")
def voice_stop():
    """停止收音。
    连续对话(vad)：结束当前这句话并**【立即识别】**（不是丢弃！之前走 cancel 会
    把刚说的话整段扔掉，用户看到"提示正在识别却永远没回复"就是这个 bug）；
    按键说话(ptt)：结束这次录音，马上转写。"""
    try:
        if VOICE["mode"] == "vad":
            j = _asr("/session/finish", "POST")
            evlog("后端", "结束这句话 -> 转写 (vad/finish)")
            return jsonify(j)
        j = _asr("/session/stop", "POST")
        evlog("后端", "停止收音 -> 转写 (ptt/stop)")
        return jsonify(j)
    except Exception as e:
        evlog("后端", "停止失败: %s" % e)
        return jsonify({"ok": False, "error": str(e)}), 503


@app.post("/api/voice/cancel")
def voice_cancel():
    try:
        return jsonify(_asr("/session/cancel", "POST"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.post("/api/voice/consume")
def voice_consume():
    try:
        return jsonify(_asr("/session/consume", "POST"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.post("/api/voice/unclaim")
def voice_unclaim():
    """页面播放被自动播放策略拦截时调用：交出播放权，让别的页面接着播。
    注意：**不要**同时上报"播完"——拦截意味着没出声，谎报播完会提前打开麦克风。"""
    body = request.get_json(force=True, silent=True) or {}
    seq = body.get("seq")
    try:
        seq = int(seq) if seq is not None else None
    except Exception:
        seq = None
    if seq is None or seq == LAST_CLAIM.get("seq"):
        LAST_CLAIM["seq"] = -1
    evlog("后端", "页面交出播放权 seq=%s（播放被拦截）" % seq)
    return jsonify({"ok": True})


@app.post("/api/voice/resume")
def voice_resume():
    """宠物窗播完语音后调用：恢复收音（仅连续对话模式有意义）。
    注意：只有会话真的在进行中才恢复 —— 用户点过"停止"之后，
    不能因为她上一句话播完就又把麦克风打开。"""
    global LAST_REPLY_GUARD
    # ★ 过期播完通知：页面把"这条回复的 seq"带上来，若它已不是最新一轮
    #   （新一轮回复在它播完之前就生成/开始播放了），直接忽略。
    #   实测事故：上一句的 onended 在 02:46:13 到达 -> 恢复收音；
    #   新一轮回复 02:46:14 开始播放 -> 麦克风开着 -> 录到她自己的声音 -> 自循环。
    body = request.get_json(force=True, silent=True) or {}
    ended_seq = body.get("seq")
    if ended_seq is not None:
        try:
            ended_seq = int(ended_seq)
        except Exception:
            ended_seq = None
    if ended_seq is not None and ended_seq != SEQ:
        evlog("后端", "忽略过期的播完通知 seq=%s（当前 seq=%s）" % (ended_seq, SEQ))
        return jsonify({"ok": True, "ignored": "stale_seq"})
    # ★ 本轮回复还在生成/等待播放时，任何"播完"都不作数 —— 恢复收音由消费者
    #   在她真正播完后自己执行。这才是 02:46 那次自循环的直接缺口：
    #   02:46:10 用户说完 -> 消费者关麦开始生成；02:46:13 上一句的 ended 到达
    #   -> 旧代码立刻恢复收音；02:46:14 本轮回复才开始播放 -> 麦克风正开着
    #   -> 录到她自己的声音 -> 转写成"好的,收下。" -> 自问自答。
    if VOICE.get("busy"):
        # ★ 关键修正：以前这里一律忽略，于是流式轮次唯一能用的兜底就是
        #   "按字数估算 + 4s"（40 字 ≈ 16s），而实际只讲 6s —— 她早就说完了，
        #   麦克风却还被锁着，用户喊半天没反应。
        #   现在：只要消费者已经进入"播放"阶段，页面报的"播完"就是**本轮**的真实
        #   结束信号（seq 已在上面校验过），直接解除等待，由消费者线程收尾。
        #   ★ 安全性不变：开麦这一步仍然由消费者的收尾闸（start+dur+0.8）决定，
        #     这里只是"别再用字数估算了"，不是"立刻开麦"。
        if VOICE.get("stage") in ("play", "echo", "playing"):
            VOICE["speaking"] = False
            LAST_REPLY_GUARD = 0.0
            evlog("后端", "页面报播完 -> 交回消费者线程收尾（不再按字数干等）")
            return jsonify({"ok": True, "delegated": True})
        evlog("后端", "播完通知暂缓：本轮回复还在生成（防提前开麦）")
        return jsonify({"ok": True, "ignored": "busy"})
    VOICE["speaking"] = False
    # ★ 硬闸：即使页面报"播完了"，若按**本轮上报的真实时长**算还没到点（例如页面
    #   onended 提前触发、或两条回复交错时旧音频的 ended 事件先到），就延迟到真正
    #   播完再恢复收音 —— 否则麦克风会在她还在发声时打开，录到她自己 -> 死循环。
    st0 = PLAYBACK["start"]
    if st0 > 0:
        left = st0 + (PLAYBACK["dur"] or 0.0) + 0.8 - time.time()
        if 0 < left <= 8:
            evlog("后端", "页面已报播完，但按上报时长还差 %.1fs，延迟恢复收音" % left)
            time.sleep(left)
            VOICE["speaking"] = False
    LAST_REPLY_GUARD = 0.0          # 她确实讲完了（页面精确上报），解除收音守卫
    _set_stage("idle")              # 播放确实结束：阶段归零，界面回到"待命"
    try:
        if VOICE["mode"] != "vad":
            return jsonify({"ok": True, "msg": "按键模式下无需恢复"})
        st = _asr("/session/state")
        if st.get("state") == "off":
            evlog("后端", "播完但会话已结束 -> 不恢复收音")
            return jsonify({"ok": True, "msg": "会话已结束，不恢复"})
        evlog("后端", "她讲完了 -> 恢复收音")
        return jsonify(_asr("/session/resume", "POST"))
    except Exception as e:
        evlog("后端", "恢复收音失败: %s" % e)
        return jsonify({"ok": False, "error": str(e)}), 503


@app.post("/api/voice/mode")
def voice_mode():
    body = request.get_json(force=True) or {}
    mode = body.get("mode")
    if mode not in ("ptt", "vad"):
        return jsonify({"ok": False, "error": "mode 只能是 ptt 或 vad"}), 400
    # 换模式等于换一种听法，先把当前会话收掉，否则旧的收音状态会把 start 挡住（409）
    try:
        _asr("/session/cancel", "POST")
    except Exception:
        pass
    VOICE["mode"] = mode
    VOICE["speaking"] = False
    _set_stage("idle")
    evlog("后端", "切换模式 -> %s" % mode)
    return jsonify({"ok": True, "mode": mode})


# 语音会话的后台消费者：盯着 ASR 服务，一有转写好的文字就接过来跑对话
# 默认"连续对话"：说完停顿自动识别，不用再点第二次
# ★ last_seq 只作日志/展示用，**不再当作"是否处理"的判据** —— 原因见 _voice_tick()。
# ★ stage 是"本轮进行到哪一步"的显式标记，供界面区分三种等待：
#     model —— 已取走转写、正在等大脑（LLM）返回；这段时间以前界面上没有任何提示，
#              用户以为"没反应"，点麦克风/再说一句，反而制造出重复轮次。
#     play  —— 回复已生成，等页面开始播
#     playing / echo —— 正在播 / 丢弃回声后等她播完
#   stage_ts 记录进入该阶段的时刻，界面上直接显示"已经等了 N 秒"。
VOICE = {"mode": "vad", "speaking": False, "last_seq": -1, "busy": False,
         "stage": "idle", "stage_ts": 0.0}
_VOICE_LOCK = threading.Lock()
_VOICE_THREAD = {"t": None}


def _set_stage(name, why=""):
    """切换本轮阶段（只为可观测性，不参与任何判定逻辑）。"""
    if VOICE.get("stage") != name:
        VOICE["stage"] = name
        VOICE["stage_ts"] = time.time()
        if why:
            evlog("后端", "阶段 -> %s（%s）" % (name, why))


def _voice_phase(now=None):
    """把内部状态收敛成一个给界面用的 phase。

    返回 (phase, 已持续秒数)：
      off            语音服务没起
      listening      正在收音
      transcribing   ASR 正在识别
      pending        识别完成，等着被取走
      waiting_model  ★ 已取走，正在等大脑返回（新增：模型响应等待态）
      waiting_play   回复已生成，等页面开口
      playing        正在播放她的语音
      idle           待命
    """
    now = time.time() if now is None else now
    if VOICE["busy"] or VOICE["speaking"]:
        stage = VOICE.get("stage") or "idle"
        since = max(0.0, now - (VOICE.get("stage_ts") or now))
        if stage == "model":
            return "waiting_model", since
        if stage in ("play", "echo"):
            st, d = PLAYBACK["start"], PLAYBACK["dur"]
            if st > 0 and now < st + (d or 0.0) + 0.3:
                return "playing", since
            # ★ 有上报且已经过了预计播完时刻 = 她在走收尾缓冲（约 0.8s）。
            #   这时候再说"准备开口"是错的（她早开口了），界面上要能区分。
            if st > 0:
                return "wrapping", since
            return "waiting_play", since
        if stage == "playing":
            return "playing", since
        return "idle", 0.0
    return None, 0.0      # 交给调用方按 ASR 状态推断


def _norm_text(s):
    """归一化比较用文本：只留文字/数字，去掉标点与空白。"""
    return re.sub(r"[\W_]+", "", (s or "").lower())


def _looks_like_echo(text):
    """刚转写出来的这句话，是不是她自己上一句回复？

    ★ 最后一道防自循环闸。即便播放/收音时序被各种竞态拉偏（多页面重复播放、
      自动播放被拦截、过期 ended 事件），只要"被转写的内容 ≈ 她刚说过的话"，
      就判定为麦克风录到了她自己的声音，直接丢弃，绝不拿去生成新一轮回复。
      实测漏网样本：她回"好的，收下。以后想起啦，我会等。"，
      转写结果"好的,收下。"（就是前 4 个字），按包含关系即可命中。
    """
    reply = LAST.get("reply") or ""
    if not reply:
        return False
    t, r = _norm_text(text), _norm_text(reply)
    if len(t) < 4:
        return False
    if t in r or r in t:
        return True
    # 对齐重合度：按连续匹配块计，不是"字符是否在回复里出现过"。
    # 旧实现 sum(ch in r) 会把"我就问我就问"这类每个字都常见的短句
    # 误判成她自己的声音（我/就/问 都出现在上一句），导致漏答。
    m = difflib.SequenceMatcher(None, t, r)
    matched = sum(b.size for b in m.get_matching_blocks())
    if matched >= 4 and matched / float(len(t)) >= 0.8:
        return True
    # 整体相似度：仅对长句（≥12字）有意义，短句序列相似度噪声大
    if len(t) >= 12 and m.ratio() >= 0.62:
        return True
    return False


def _wait_playback_done(max_wait=20.0):
    """等她这一轮真正播完（最多 max_wait 秒）。用于丢弃回声后交回麦克风前。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        st, d = PLAYBACK["start"], PLAYBACK["dur"]
        if st <= 0:
            return
        left = st + d + 0.8 - time.time()
        if left <= 0:
            return
        time.sleep(min(0.25, left))


def _voice_tick():
    """消费者的一轮：有转写就接管、生成回复、等她播完、再恢复收音。"""
    try:
        j = _asr("/session/state", timeout=4)
    except Exception:
        time.sleep(1.0)
        return

    if j.get("state") != "pending" or VOICE["busy"]:
        return
    text = (j.get("text") or "").strip()
    if not text:
        return

    # ★★ 这里**绝不能**再用 "seq != last_seq" 来判重（曾经就是它把功能整体打死的）：
    #   ASR 服务重启后，它内部的 seq 会从 0 重新计数，而后端进程没重启、
    #   VOICE["last_seq"] 还留着上一次会话的值（例如 2）。于是新会话里所有
    #   seq <= 2 的转写全都被当成"已经处理过"而**静默丢弃** —— 用户体感就是
    #   "我说了话她完全没反应"，而且日志里连"收到识别文字"都不会出现
    #   （因为它压根没进到那一步）。实测复现：用户说"你可以为我做什么?"被准确
    #   转写、状态 pending，却一直没人取走（consumed=-1）。
    #   判重其实不需要 seq：consume 之后 ASR 就不再是 pending，再加 busy 互斥，
    #   天然不会重复处理。
    VOICE["busy"] = True
    try:
        VOICE["last_seq"] = j.get("seq")     # 仅记录，便于排查
        try:
            _asr("/session/consume", "POST")          # 先取走，进入 paused
        except Exception as e:
            evlog("后端", "取走转写失败（跳过本轮）: %r" % (e,))
            return
        # ★ 回声过滤：转写内容 ≈ 她上一条回复 -> 麦克风录到了她自己。
        #   先等她本轮播完，再交回麦克风，绝不生成新回复（否则就是自问自答）。
        if _looks_like_echo(text):
            evlog("后端", "疑似录到她自己的声音，丢弃本轮: %s" % text[:40])
            _set_stage("echo", "丢弃回声，等她播完")
            _wait_playback_done()
            VOICE["speaking"] = False
            _set_stage("idle")
            try:
                if VOICE["mode"] == "vad":
                    _asr("/session/resume", "POST")
            except Exception:
                pass
            return
        VOICE["speaking"] = True
        # ★ 从这里到 do_chat 返回，是"等模型响应"阶段。界面上必须显式显示，
        #   否则用户面对一段没有任何反馈的空白期，会以为没反应而重复说话/点麦克风。
        _set_stage("model", "已取走转写，等大脑返回")
        # 趁"她在想事情"这几秒，先把到 TTS 的 TLS 连接热好（不合成、不计费）。
        # 冷连接首包要多付 0.5~0.7s，而这段空等正好白白浪费掉。
        try:
            import tts_stream as _ts
            threading.Thread(target=_ts.prewarm, daemon=True).start()
        except Exception:
            pass
        evlog("后端", "收到识别文字 -> 开始生成回复: %s" % text[:60])
        try:
            payload, mp3 = do_chat(text)
            print("[voice] 听到: %s -> %s" % (text, payload["reply"][:60]), flush=True)
            evlog("后端", "回复已生成(%.0f 字): %s"
                 % (len(payload["reply"]), payload["reply"][:60]))
        except Exception as e:
            print("[voice] 对话失败:", e, flush=True)
            evlog("后端", "生成回复失败: %s" % e)
            VOICE["speaking"] = False
            _set_stage("idle")
            return
        _set_stage("play", "回复已生成(耗时 %.1fs)，等页面开口"
                   % max(0.0, time.time() - (VOICE.get("stage_ts") or time.time())))
        # 等她说完。**只认本轮新上报的播放时长** —— do_chat 已把 PLAYBACK 清零，
        # 所以 start==0 就表示"这一轮页面还没报播放"。
        # ★ 两个必须遵守的点：
        #   1) 绝不能用 len(mp3)/16000 估算（对 MP3 无意义且偏短一半以上）；
        #   2) 绝不能用**上一条**的 PLAYBACK 记录来判断 —— 那正是死循环来源：
        #      旧记录的 start+dur 早已过期，兜底条件瞬间成立，于是她还没开口
        #      就把麦克风打开了。
        # ★ 流式轮次没有 mp3 可量（音频由页面自己拉）：必须退回按字数估算。
        #   否则 est 只有 3.0，页面取走回复后 est+4=7s 就兜底开麦，而 40 字的回复
        #   要讲约 10s —— 麦克风正好在她说话时打开，录到她自己 -> 自问自答。
        if mp3:
            est = len(mp3) / 6000.0          # 按 ~48kbps 估算播放秒数
        elif payload.get("stream"):
            est = _estimate_speech_seconds(payload["reply"])
        else:
            est = 3.0
        t0 = time.time()
        while VOICE["speaking"]:
            st = PLAYBACK["start"]
            if st > 0:
                d = PLAYBACK["dur"] or est
                # ★ 只有"上报已经静默 2s"才允许按上报时长兜底：
                #   页面还在每 0.3s 刷新总长时，最后一条 dur 一定是最新的；
                #   但若上报中断（网络抖动、页面卡住），拿旧 dur 算会在她还在
                #   说话时就把 mic 打开 —— 实测模拟里就这样提前了 0.22s。
                quiet = time.time() - (PLAYBACK.get("ts") or st)
                if time.time() >= st + d + 1.5 and quiet >= 2.0:
                    evlog("后端", "页面未报播放结束（已静默 %.1fs），按 %.1fs 兜底解除"
                          % (quiet, d))
                    break
            else:
                # ★ 页面还没上报播放时：兜底必须从"页面确实取到了这条回复"起算。
                #   首次对话场景：大脑响应慢 + WebView2 音频冷启动，页面从拿到回复
                #   到真正出声可能 >5s；旧逻辑 est+4 一到就恢复收音，她随后才开口
                #   -> 录到她自己 -> 自循环。现在：
                #     · 页面已取走回复但仍未报播放 -> 再给 est+4（含起播延迟余量）
                #     · 页面一直没来取（收进托盘/聊天窗发声）-> est+15 兜底
                page_got = (LAST_FETCH.get("seq") == SEQ
                            and LAST_FETCH.get("ts", 0) > t0)
                if ((page_got and time.time() - t0 > est + 4)
                        or time.time() - t0 > est + 15):
                    evlog("后端", "页面未报播放时长(%s)，按估算 %.1fs 兜底解除"
                          % ("已取走回复" if page_got else "未取走回复", est))
                    break
            if time.time() - t0 > est + 40:
                # 绝对上限：这里是"等她播完"的等待，绝不能无限等下去，
                # 否则 busy 永久为真 -> 后面所有转写都进不来（她彻底不回答了）
                evlog("后端", "等待播放结束超时（%.0fs），强制恢复收音" % (time.time() - t0))
                break
            time.sleep(0.25)
        # 收尾闸：页面确实在播（或刚报过）时，等真正播完 +0.8s 余量再交回收音，
        # 与 /api/voice/resume 的守卫同一口径，避免两条恢复路径的时序差。
        st = PLAYBACK["start"]
        if st > 0:
            left = st + (PLAYBACK["dur"] or 0.0) + 0.8 - time.time()
            if 0 < left <= 8:
                evlog("后端", "收尾等待 %.1fs 后再恢复收音" % left)
                time.sleep(left)
        VOICE["speaking"] = False
        try:
            if VOICE["mode"] == "vad":
                _asr("/session/resume", "POST")
        except Exception:
            pass
    finally:
        VOICE["busy"] = False
        _set_stage("idle", "本轮结束")


def _voice_worker():
    """消费者的主循环。★ 整轮兜住异常：这个线程是语音对话的"心脏"，
    一旦它悄悄退出，表现就是"我说话她永远没反应"，而且日志里不留痕迹。"""
    while True:
        try:
            _voice_tick()
        except Exception as e:
            evlog("后端", "语音消费者异常（已自动继续）: %r" % (e,))
        time.sleep(0.35)


def _start_voice_worker():
    with _VOICE_LOCK:
        t = _VOICE_THREAD.get("t")
        if t is not None and t.is_alive():
            return t
        t = threading.Thread(target=_voice_worker, daemon=True)
        t.start()
        _VOICE_THREAD["t"] = t
    print("[voice] 后台消费者已启动，ASR =", ASR_URL)
    return t


def _ensure_voice_worker():
    """看门狗：消费者线程要是没了（异常退出/被误杀），立刻把它拉回来。
    挂在 /api/voice/start 与 /api/voice/state 上 —— 都是必然经过的高频路径。"""
    t = _VOICE_THREAD.get("t")
    if t is None or not t.is_alive():
        evlog("后端", "语音消费者线程不在，已自动重启")
        _start_voice_worker()


@app.get("/favicon.ico")
def favicon():
    ico = os.path.join(XIAOYUN_DIR, "xiaoyun.ico")
    if os.path.exists(ico):
        return send_from_directory(PAGE_DIR, "xiaoyun.ico")
    return "", 404


def _preload():
    """后端起来后只探活大脑，不再做显存预热。
    or_proxy 由启动脚本（Start-Xiaoyun.ps1）或桌面「启动OpenRouter代理.bat」拉起。"""
    ok, detail = brain_warmup()
    print("[brain] 启动探活:", ok, detail, "->", BRAIN_HEALTH_URL)
    if not ok:
        print("[brain] 未在线！请双击桌面「启动OpenRouter代理.bat」，或等待启动脚本拉起。")


def _warm_embeddings(store, client=None):
    """后台预热语义检索：探真实维度 -> 迁移旧向量 -> 补建存量向量。

    为什么整段丢到后台：embed 走 GPU 后首次要加载 bge-m3（约 664MB 权重）
    并建 CUDA context，冷启动是秒级的；这段逻辑原先同步跑在启动路径上，
    会把"后端能响应 /api/health"的时间往后推，启动脚本容易误判成没起来。
    后台化的代价只是最初几秒记忆退化为关键词检索，随后自动恢复；
    整个流程幂等（只看 emb IS NULL / 维度不符），重复跑无副作用。
    """
    def _run():
        try:
            if client is not None:
                vecs = client.embed(["维度探测"], "doc")
                dim = client.dim or (len(vecs[0]) if vecs else 0)
                if not dim:
                    evlog("后端", "语义检索预热失败（embed 后端未响应），本次退化为关键词检索")
                    return
                # 维度迁移：换 embedding 模型（bge-small 512 -> bge-m3 1024）后，
                # 旧向量长度对不上，_vector_hits 会**静默跳过**它们 —— 不报错、
                # 也永远召回不到。所以先探真实维度，把不符的清掉再重嵌。
                purged = store.purge_mismatched_vectors(dim)
                if purged:
                    evlog("后端", "embedding 维度迁移：%d 条旧向量已清空（新维度 %d），开始重嵌"
                         % (purged, dim))
            t0 = time.time()
            n = store.backfill()
            evlog("后端", "语义检索预热完成（%s dim=%s）：补建 %d 条，耗时 %.1fs"
                 % (getattr(client, "gpu_state", "-"),
                    getattr(client, "dim", "-"), n, time.time() - t0))
        except Exception as e:
            evlog("后端", "语义检索预热失败（不影响使用，FTS5 仍兜底）: %r" % (e,))
    threading.Thread(target=_run, daemon=True).start()


def _init_memory_and_context():
    """初始化长期记忆与情境引擎。

    为什么要单独抽成函数而不是写在 `if __name__ == "__main__"` 里：
    初始化逻辑本身需要可测、可重入（比如单元测试里导入模块也想拿到一套完整的
    子系统），塞在 main 块里就只能"跑进程才生效"，无法在测试里覆盖。
    失败一律降级 + 打日志，绝不影响主服务起来。
    """
    global MEM, BRAIN_MEM, CTX, EMBED
    if MEMORY_ENABLED and MemoryStore is not None and MEM is None:
        try:
            # 语义检索增强：**始终**注入 embed 客户端（即使此刻服务不在线）。
            # 原因：启动顺序不确定——后端可能比 embed 服务先起来（embed 要加载模型），
            # 若按"启动那一刻探活"决定注入与否，这一整轮会话就永久退化成纯 FTS5。
            # EmbedClient 自带 30s 掉线缓存：服务不在时只付一次连接失败的代价，
            # 服务起来后下一次调用自动恢复，无需重启后端。MemoryStore 侧本来就
            # 对 None 做了降级，所以这条链路天然是"有无都能跑"。
            EMBED = EmbedClient(EMBED_URL, backend=EMBED_BACKEND,
                                model=EMBED_MODEL, num_gpu=EMBED_NUM_GPU,
                                keep_alive=EMBED_KEEP_ALIVE)
            MEM = MemoryStore(MEMORY_DB, embed_fn=EMBED.embed)
            BRAIN_MEM = MemoryBrain(MEM, _brain_prompt, log=_evlog, enabled=True)
            online = EMBED.health()
            evlog("后端", "长期记忆已启用（库=%s，存量事实 %d 条，语义检索 %s / %s）"
                 % (MEMORY_DB, MEM.count(),
                    "开" if online else "待命（embed 后端未就绪，自动降级关键词检索）",
                    ("%s@%s" % (EMBED_MODEL, EMBED_BACKEND)) if online else "-"))
            if online:
                # 维度探测 + 迁移 + 存量补建全部丢后台，见 _warm_embeddings 的说明。
                _warm_embeddings(MEM, EMBED)
        except Exception as e:
            evlog("后端", "记忆模块初始化失败，已降级为无记忆: %r" % (e,))
            MEM = BRAIN_MEM = None
    if CONTEXT_ENABLED and ContextEngine is not None and CTX is None:
        try:
            CTX = ContextEngine(
                _speak_proactively, log=_evlog,
                idle_seconds=int(os.environ.get("XIAOYUN_IDLE_SECS", "900")),
                randomize=True,
                min_gap=int(os.environ.get("XIAOYUN_MIN_GAP", "600")),
                greeting_after=int(os.environ.get("XIAOYUN_GREET_AFTER", "180")))
            CTX.start(is_busy=lambda: bool(VOICE.get("busy") or VOICE.get("speaking")),
                      is_listening=lambda: _asr_listening(),
                      is_working=lambda: bool(PERC is not None and PERC.is_working()))
            evlog("后端", "情境引擎已启用（空闲 %ds / 最小间隔 600s）" % CTX.idle_seconds)
        except Exception as e:
            evlog("后端", "情境引擎初始化失败，已降级为无主动搭话: %r" % (e,))
            CTX = None
    return {"memory": BRAIN_MEM is not None, "context": CTX is not None}


# ==================== 屏幕感知（P0） ====================

# 免费池模型名里"可能带视觉"的关键词。只当候选不当定论——名字带 vl 未必真能收图，
# 由 PerceptionEngine._try_vision 实测探测、失败自动换下一个、全挂进冷却。
_VISION_PATTERNS = ("vl", "vision", "gemini", "4o", "glm-4", "llava", "minicpm")


def _detect_vision_models():
    """从 or_proxy 模型池里挑视觉候选（最多 3 个）。探测不出 → 返回空，走关键词降级。"""
    try:
        r = requests.get(CFG["base"].rstrip("/") + "/models", timeout=5)
        ids = [m.get("id") or "" for m in (r.json().get("data") or [])]
    except Exception:
        return []
    hits = [mid for mid in ids
            if any(p in mid.lower() for p in _VISION_PATTERNS)]
    return hits[:3]


def _vision_chat(messages, model):
    """感知引擎的视觉通道（Step 2 主备分发）：方舟模型走 Agent Plan，其余走 or_proxy 免费池。"""
    if ARKPLAN is not None and model.startswith("doubao-seed"):
        return ARKPLAN.ark_chat(messages, model=model, temperature=0.1, max_tokens=300)
    url = CFG["base"].rstrip("/") + "/chat/completions"
    r = requests.post(url, json={
        "model": model, "messages": messages,
        "temperature": 0.1, "max_tokens": 300, "stream": False,
    }, headers={"Authorization": "Bearer sk-local"}, timeout=90)
    r.raise_for_status()
    return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def _build_vision_chain():
    """组装视觉通道链（Step 2）：方舟 doubao-seed（主，套餐稳定）→ or_proxy VL（备）。

    返回 (vision_fn, models)。两边都拿不到 → (None, [])，由感知引擎按 PROBE_GAP 周期重探。
    """
    models = []
    if ARKPLAN is not None:
        models.append(ARK_VISION_MODEL)
    models += _detect_vision_models()
    if not models:
        return None, []
    return _vision_chat, models


def _activity_context():
    """给大脑的"最近在干嘛"块（P1）。事件库不可用/为空返回 None，调用方跳过注入。"""
    if EVENTS is None:
        return None
    try:
        blk = EVENTS.context_block()
        return blk or None
    except Exception:
        return None


def _on_activity_signal(signal, evt):
    """屏幕报错/成功 → 关心一句。复用 _speak_proactively（时序铁律：不复制发布逻辑）。"""
    detail = (evt.get("detail") or evt.get("activity") or "")[:40]
    if signal == "error":
        intent = ("主人屏幕上好像出了问题（%s）。用关心的口吻问一句怎么了，"
                  "一到两句，不要追问技术细节。") % detail
    else:
        intent = "主人刚才那件事做成了（%s）。简短真诚地夸一句，一到两句。" % detail
    _speak_proactively(intent, "activity:" + signal)


def _on_activity_note(note, evt):
    """视觉模型说值得讲的观察 → 自然提一句。同走 _speak_proactively。"""
    intent = ("你从屏幕上注意到一件事：%s。用自己的口吻自然地提一句"
              "（不要复述这条指令本身），一到两句。") % (note or "")[:80]
    _speak_proactively(intent, "activity:note")


def _init_perception():
    """屏幕感知初始化：视觉链（方舟→or_proxy）拿不到 → 纯关键词降级 + 5 分钟自动重探。"""
    global PERC
    if not PERCEPTION_ENABLED or PerceptionEngine is None:
        return
    try:
        vis_fn, models = _build_vision_chain()
        events_path = os.path.join(XIAOYUN_DIR, "data", "events.jsonl")
        # 事件库（P1）：与 memory.db 同库分表；建库失败只降级，感知照常
        global EVENTS
        try:
            if EventStore is not None:
                EVENTS = EventStore(MEMORY_DB)
        except Exception as e:
            evlog("后端", "事件库初始化失败，今日回顾停用: %r" % (e,))
            EVENTS = None
        PERC = PerceptionEngine(
            vision_fn=vis_fn,
            vision_models=models,
            probe_fn=_build_vision_chain,      # 视觉缺失时按 PROBE_GAP 自动重探热接
            on_event=(lambda e: EVENTS.add(e)) if EVENTS is not None else None,
            on_signal=_on_activity_signal,
            on_note=_on_activity_note,
            events_path=events_path,
            interval=int(os.environ.get("XIAOYUN_PERC_INTERVAL", "60")),
            log=_evlog)
        PERC.start()
        evlog("后端", "屏幕感知已启用（视觉链 %s，事件流 %s）"
             % (" → ".join(models) if models else "无（纯关键词降级，每5分钟自动重探）",
                events_path))
    except Exception as e:
        evlog("后端", "屏幕感知初始化失败，已停用: %r" % (e,))
        PERC = None


if __name__ == "__main__":
    print("小芸桌面后端启动: http://127.0.0.1:%d" % PORT)
    _init_preset()          # 沿用上次选择的人设（人格 + 声线），没有就用 config.json
    _load_voice_override(log=True)   # 再盖上用户单独选过的音色（音色独立于人格）

    # ---- 记忆 / 情境 / 屏幕感知三个子系统初始化（失败只降级，不影响主服务） ----
    _init_perception()      # 先起感知：情境引擎的 is_working 门控引用 PERC
    _st = _init_memory_and_context()
    print("记忆:", "开" if _st["memory"] else "关",
          "| 情境主动搭话:", "开" if _st["context"] else "关",
          "| 屏幕感知:", "开" if PERC is not None else "关")

    print("人设:", CFG.get("preset_label"), "| TTS引擎:", CFG.get("tts_engine"),
          "| 音色:", (CFG.get("minimax_voice") or CFG["voice"]),
          "| 语速:", CFG.get("speed"), "| 音调:", CFG.get("pitch"), CFG.get("pitch_hz"))
    print("模型:", CFG["model"], " | 大脑:", CFG["base"], "（or_proxy/OpenRouter，不占显存）")
    threading.Thread(target=_preload, daemon=True).start()

    # ---- GPT-SoVITS 空闲回收（动态加载的配套）----
    # 启动链已不再拉起 GPT-SoVITS；只有切到 gsv: 音色才按需加载（省 5.3GB 内存）。
    # 这个守护负责"用完就走"：空闲超过 XIAOYUN_GSV_IDLE_TTL（默认 900s）就杀掉进程。
    print("GPT-SoVITS: 按需加载（切本地音色时才拉起），空闲 %ds 自动释放" % GSV_IDLE_TTL)
    threading.Thread(target=_gsv_idle_reaper, daemon=True).start()
    # 兜底：退出前尽量不留 GPT-SoVITS 孤儿进程（它自己会 fork worker）。
    atexit.register(_gsv_kill)
    _start_voice_worker()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
