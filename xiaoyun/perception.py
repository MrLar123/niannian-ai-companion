# -*- coding: utf-8 -*-
"""
念念 · 屏幕/活动感知（P0）

设计来源（借鉴，非照搬）：
- WhalePal（github.com/Jessie-1939/whalepal，MIT）的 src/main/context/{dhash,engine,analyzer}.js：
  周期截屏 → 感知哈希窗口去重 → 云端视觉模型理解（13 类活动）→ 事件流；
  无 Key / 失败时退化为「基础感知」——仅按前台窗口标题 + 进程名做关键词分类。
- MineContext（Apache-2.0，经 WhalePal 转述）的哈希窗口去重思路：阈值 4、窗口 6。

与借鉴对象的关键差异（为什么不直接照搬）：
1. **传输与模型不硬编码在模块里**：视觉调用由服务端注入 `vision_fn`（走 or_proxy），
   模型候选在 env 覆盖 / 内置优先级之间逐个探测——探测成功的模型才继续用，
   全部失败冷却 10 分钟再试。模块本身不认识 or_proxy 的地址与 Key。
2. **台词不在这里生成**：视觉模型只产出结构化事实（活动/类别/信号/speak 建议/note），
   真正的开口仍走服务端 `_speak_proactively`——人设（念念）、记忆注入、
   SEQ/LAST_REPLY_GUARD/PLAYBACK 时序铁律都在那条共用链路上，这里复制一份就是坑。
3. **截图只活在内存**：算完 dhash 与 JPEG 即弃，不落盘（WhalePal 可选保留 latest.jpg，
   念念默认一张不留——桌宠场景没有"回看截图"的需求，少一份隐私面）。
4. **基础感知常驻、视觉增强**：关键词分类每轮都跑（本地、零成本），视觉只在画面
   变化且探测到可用模型时叠加——or_proxy 没起 / 模型不支持图像时行为与纯本地一致。

依赖：PIL + mss（Miniconda 运行时实测已装）；前景窗用 ctypes，零第三方。
"""
import base64
import io
import json
import os
import threading
import time

# ------------------------------------------------------------------ 可调参数
# 全部集中在文件头（项目惯例：stage_blink.py / context_engine.py 同风格）。
# 环境变量覆盖见 xiaoyun_server.py；这里只是模块默认值。

INTERVAL_SECS = 60        # 感知周期（秒）。服务端 clamp 到 15~3600
SIMILARITY_THRESHOLD = 4  # dhash 汉明距离 ≤ 此值视为同画面（WhalePal 同值，取 2 与 6 之间）
HASH_WINDOW = 6           # 与最近几张指纹比对（滑窗）
NOTE_SPEAK_GAP = 300      # 「观察说出口」最小间隔（秒）：防刷屏
SIGNAL_SPEAK_GAP = 180    # 报错/成功反应最小间隔（秒）
VISION_RETRY_SECS = 600   # 视觉模型全部失败后的冷却（秒）
PROBE_GAP = 300           # 视觉通道缺失时，重探链路的间隔（秒）——修复"启动时探测不到就永久关闭"
MAX_SHOT_W = 960          # 发给视觉模型的最长边（压 token/带宽；dhash 用的是 9x8 不受影响）
JPEG_QUALITY = 60         # 视觉用 JPEG 质量（WhalePal 用 72，这里更省）
EVENTS_KEEP = 200         # 内存里保留的事件条数（API 展示用）
WORKING_FRESH = 180       # isWorking 判定的"新鲜度"：最后一条事件超过这么久就不算数


# ------------------------------------------------------------------ 感知哈希

def dhash(img):
    """8x8 差分哈希（水平梯度），返回 64 位 int。借鉴 WhalePal dhash.js：
    缩到 9x8 灰度，左像素 > 右像素则置位。对亮度整体漂移鲁棒，对布局变化敏感。"""
    g = img.convert("L").resize((9, 8))
    px = list(g.getdata())
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            if px[row + x] > px[row + x + 1]:
                bits |= 1 << (y * 8 + x)
    return bits


def hamming(a, b):
    """两个 64 位指纹的汉明距离。py3.10 起 int.bit_count() 是原生最快写法。"""
    return (a ^ b).bit_count()


def find_similar(h, recent, threshold=SIMILARITY_THRESHOLD):
    """在滑窗里找相似指纹，返回下标；-1 = 画面变了。"""
    for i, old in enumerate(recent):
        if hamming(h, old) <= threshold:
            return i
    return -1


# ------------------------------------------------------------------ 屏幕采集

class ScreenCapturer(object):
    """mss 封装：实例非线程安全，故 capturer 只在引擎线程里创建与使用。
    monitors[0] 是跨所有显示器的虚拟大矩形（WhalePal 截整屏，同）。"""

    def __init__(self):
        self._mss = None

    def grab(self):
        if self._mss is None:
            import mss
            self._mss = mss.mss()
        from PIL import Image
        shot = self._mss.grab(self._mss.monitors[0])
        return Image.frombytes("RGB", shot.size, shot.rgb)


# ------------------------------------------------------------------ 前台窗口

def foreground_window():
    """返回 (窗口标题, 进程名)。仅 Windows；任何一步失败都返回 ("", "")——
    锁屏/权限不足时亨有 idle 兜底，不能让感知循环因为取不到标题而停。"""
    if os.name != "nt":
        return "", ""
    try:
        import ctypes
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        buf = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value or ""
        pid = ctypes.c_ulong(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        # PROCESS_QUERY_LIMITED_INFORMATION(0x1000) 即可拿全路径，比 0x0400 权限要求低
        h = k32.OpenProcess(0x1000, False, pid.value)
        if h:
            try:
                pbuf = ctypes.create_unicode_buffer(260)
                size = ctypes.c_ulong(260)
                if k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                    proc = os.path.basename(pbuf.value or "")
            finally:
                k32.CloseHandle(h)
        return title, proc
    except Exception:
        return "", ""


# ------------------------------------------------------------------ 关键词分类（基础感知）
# 表来自 WhalePal analyzer.js 的 RULES，补了中国用户常见进程名
# （weixin=新版微信进程、wxwork=企业微信、wemeet=腾讯会议、剪映/抖音/网易云等）。

WORKING = set(["coding", "office", "writing", "design", "meeting", "reading", "terminal"])

RULES = [
    ("coding", ["visual studio code", "vscode", "cursor", "pycharm", "intellij", "idea",
                "webstorm", "goland", "clion", "rider", "datagrip", "android studio",
                "xcode", "devenv", "code.exe", "sublime", "notepad++", "neovim", "vim",
                "emacs", "matlab", "simulink", "eclipse", "deveco", "arduino", "trae"]),
    ("terminal", ["windowsterminal", "windows terminal", "powershell", "pwsh", "cmd.exe",
                  "conhost", "wsl", "git-bash", "mintty", "terminal", "命令提示符",
                  "xshell", "finalshell", "mobaxterm", "windowsterminal.exe"]),
    ("office", ["winword", "word", "excel", "powerpnt", "powerpoint", "wps", "onenote",
                "outlook", "keynote", "numbers", "pages", "et.exe", "wpp.exe"]),
    ("writing", ["typora", "obsidian", "notion", "语雀", "印象笔记", "思源笔记",
                 "siyuan", "joplin", "飞书文档", "腾讯文档"]),
    ("reading", ["acrord", "adobe reader", "sumatra", "foxit", "calibre", "zotero",
                 "pdf", "知云", "微信读书", "kindle"]),
    ("design", ["photoshop", "illustrator", "figma", "blender", "maya", "3ds max",
                "autocad", "sketch", "adobexd", "cad", "剪映", "jianying",
                "premiere", "after effects", "达芬奇", "davinci"]),
    ("meeting", ["zoom", "teams", "tencent meeting", "腾讯会议", "webex",
                 "meet.google", "wemeet", "voovmeeting"]),
    ("chat", ["wechat", "weixin", "微信", "qq", "telegram", "discord", "slack",
              "feishu", "飞书", "dingtalk", "钉钉", "企业微信", "wxwork", "tim"]),
    ("video", ["potplayer", "vlc", "mpv", "mpc-hc", "bilibili", "哔哩哔哩", "youku",
               "优酷", "iqiyi", "爱奇艺", "netflix", "youtube", "腾讯视频", "qqlive",
               "芒果", "douyin", "抖音", "cloudmusic", "网易云音乐"]),
    ("gaming", ["steam", "epic games", "league of legends", "leagueclient", "valorant",
                "genshin", "原神", "minecraft", "崩坏", "王者荣耀", "游戏", "csgo",
                "cs2", "永劫无间", "lol.exe"]),
    ("browsing", ["chrome", "msedge", "edge", "firefox", "brave", "opera", "360se",
                  "qqbrowser", "safari", "arc", "浏览器"]),
]

ACTIVITY_TEXT = {
    "coding": "编写代码", "office": "处理文档/表格", "writing": "写作/记录",
    "design": "做设计", "meeting": "开会", "reading": "阅读资料",
    "video": "看视频", "chat": "聊天/沟通", "browsing": "浏览网页",
    "gaming": "玩游戏", "terminal": "跑命令/调试", "idle": "暂时空闲",
    "other": "日常操作",
}

CATEGORIES = set(ACTIVITY_TEXT.keys())


def classify_window(title="", proc=""):
    """基础感知：仅按窗口标题 + 进程名分类（本地、零成本、每轮都跑）。
    返回 (category, isWorking)。空标题 = 锁屏/无前台 → idle。"""
    hay = (proc + " " + title).lower()
    for category, keywords in RULES:
        for k in keywords:
            if k in hay:
                return category, category in WORKING
    if not title.strip():
        return "idle", False
    return "other", False


# ------------------------------------------------------------------ 视觉理解（增强）

# 契约与 WhalePal analyzer.js 的 ANALYZE_SYSTEM 同构，但做了两处念念化裁剪：
# 1. note 不指定"鲸鱼娘口吻"——开口走 _speak_proactively，人设由 preset 统一给，
#    这里让模型把观察本身说清楚就行，避免两种人格提示打架。
# 2. 去掉 entities/topics（那是 WhalePal 为跨天实体记忆准备的）；
#    念念的跨天记忆走 memory_store 的事实库，这里保留 detail 已够开口与展示。
ANALYZE_SYSTEM = """你是桌面宠物的屏幕理解模块。根据屏幕截图与前台窗口信息，判断用户此刻在做什么，输出严格 JSON（不要输出任何多余文字）：
{"activity":"一句话描述用户正在做什么（中文，不超过20字）","category":"coding|office|writing|design|meeting|reading|video|chat|browsing|gaming|terminal|idle|other","app":"最相关的应用名","isWorking":true或false,"signal":"none|error|success","speak":true或false,"detail":"更具体的描述（不超过60字）：在做什么、屏幕上的关键信息（文件名/页面/报错关键行）","note":"一句给同伴的观察（不超过30字，可为空字符串）"}
判断规则：写代码、写文档、做设计、开会、读资料、终端命令等生产/学习行为 isWorking=true；看视频、游戏、聊天、浏览、发呆、桌面空闲 isWorking=false。
signal 判定：屏幕上出现明确的报错/失败/异常（红色错误提示、异常堆栈、测试失败、构建失败）→ "error"；出现明确的完成/成功（构建通过、测试全绿、任务完成提示）→ "success"；其余一律 "none"。
detail 请只写截图里真实出现过的名字（文件名、项目名、报错关键行），提取不到就留空，不要编造。
隐私红线：绝不复述屏幕上的密码、密钥、金额、身份证号或聊天内容原文。
note 请写具体的观察（在做什么、卡在哪、节奏如何），每次换一个说法，不要与下方「最近已用过的观察」重复。
speak 判定：只有这条观察真的值得此刻在桌面上说出来（有趣的发现、值得提醒的事）才 true；日常重复的操作、无话可说时一律 false——沉默也是一种陪伴。"""


def parse_json_loose(text):
    """从模型输出里尽量抠出一个 JSON 对象。容错：```json 围栏、前后多余文字。"""
    if not text:
        return None
    s = str(text).strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
    if s.endswith("```"):
        s = s[:-3]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return None


def normalize_vision(parsed, title="", proc=""):
    """清洗视觉模型输出：字段缺失/越界一律回退到基础感知的口径，绝不信任裸值。"""
    if not isinstance(parsed, dict):
        return None
    category = parsed.get("category")
    if category not in CATEGORIES:
        category, _w = classify_window(title, proc)
    signal = parsed.get("signal")
    if signal not in ("error", "success", "none"):
        signal = "none"
    return {
        "activity": str(parsed.get("activity") or ACTIVITY_TEXT[category])[:60],
        "category": category,
        "app": str(parsed.get("app") or proc or "")[:40],
        "isWorking": bool(parsed.get("isWorking")) if isinstance(parsed.get("isWorking"), bool)
                     else category in WORKING,
        "signal": signal,
        "speak": parsed.get("speak") is True,
        "detail": str(parsed.get("detail") or "")[:120],
        "note": str(parsed.get("note") or "")[:80],
        "source": "vision",
    }


def build_vision_messages(title, proc, jpeg_b64, recent_notes):
    """组视觉请求的消息体（OpenAI 多模态格式，or_proxy 原样转发 messages）。"""
    recent = "；".join((n or "")[:40] for n in recent_notes[-5:] if n)
    recent_line = ("最近已用过的观察（务必换说法，不要重复）：" + recent) if recent \
                  else "最近已用过的观察：无"
    text = "当前前台窗口：%s（进程：%s）。\n%s\n请理解这张屏幕截图并输出 JSON。" % (
        title or "未知", proc or "未知", recent_line)
    return [
        {"role": "system", "content": ANALYZE_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + jpeg_b64}},
        ]},
    ]


# ------------------------------------------------------------------ 感知引擎

class PerceptionEngine(object):
    """周期截屏 → 哈希去重 → （视觉增强）→ 事件流 + 回调。

    与情境引擎同一个注入式风格：不直接持有 LLM/HTTP，全部由外部注入，
    所以这一层可以用假 capture/假 vision 完全离线单测（见文件末尾）。

    回调（都在引擎线程里触发，调用方注意线程安全）：
      on_change(event)          活动类别变了（coding → video …）
      on_event(event)           每轮事件（含去重之外的每一帧新画面），入库用
      on_signal(signal, event)  屏幕出现报错/成功（带 SIGNAL_SPEAK_GAP 节流）
      on_note(note, event)      视觉模型说这条观察值得讲（带 NOTE_SPEAK_GAP 节流）
    """

    def __init__(self, on_change=None, on_event=None, on_signal=None, on_note=None,
                 vision_fn=None, vision_models=(), log=None,
                 interval=INTERVAL_SECS, enabled=True,
                 speak_gap=NOTE_SPEAK_GAP, signal_gap=SIGNAL_SPEAK_GAP,
                 capture_fn=None, window_fn=None, events_path=None,
                 probe_fn=None):
        self.on_change = on_change
        self.on_event = on_event
        self.on_signal = on_signal
        self.on_note = on_note
        self.vision_fn = vision_fn
        self.vision_models = list(vision_models or [])
        self.probe_fn = probe_fn              # 无参函数：返回 (vision_fn, models) 或 (None, [])
        self.log = log or (lambda *a: None)
        self.interval = max(15, min(3600, int(interval)))
        self.enabled = enabled
        self.speak_gap = speak_gap
        self.signal_gap = signal_gap
        self.events_path = events_path

        self._capture_fn = capture_fn          # 测试注入；None = 真截屏
        self._window_fn = window_fn or foreground_window
        self._capturer = None                  # 真截屏时惰性创建（引擎线程内）
        self._recent_hashes = []
        self._events = []                      # 内存事件环（最新在尾部）
        self._last = None                      # 最近一条事件（dict）
        self._signal_seq = 0                   # 反应序号：页面轮询它播表情（同 poke.seq 机制）
        self._last_signal_ts = 0.0
        self._last_note_ts = 0.0
        self._vision_idx = 0                   # 视觉模型候选下标（探测成功就停在这）
        self._vision_dead_until = 0.0          # 全部候选失败后的冷却截止
        self._vision_model = None              # 当前探测成功的模型名（state 里展示）
        self._last_probe = 0.0                 # 上次重探视觉链路的时间
        self._stop = threading.Event()
        self._thread = None
        self.stats = {"ticks": 0, "dup": 0, "vision_ok": 0, "vision_fail": 0,
                      "signals": 0, "notes": 0, "changes": 0}

    # ------------------------------------------------ 采集

    def _maybe_probe(self):
        """视觉通道缺失时按 PROBE_GAP 周期重探（探到即热接，无需重启）。"""
        if (self.vision_fn and self.vision_models) or not self.probe_fn:
            return
        now = time.time()
        if now - self._last_probe < PROBE_GAP:
            return
        self._last_probe = now
        try:
            fn, models = self.probe_fn() or (None, [])
        except Exception as e:  # noqa: BLE001
            self.log("视觉链路重探失败: %r" % (e,))
            return
        if fn and models:
            self.vision_fn = fn
            self.vision_models = list(models)
            self._vision_dead_until = 0.0
            self.log("视觉通道已热接：候选 %s" % (models,))

    def _grab(self):
        if self._capture_fn is not None:
            return self._capture_fn()
        if self._capturer is None:
            self._capturer = ScreenCapturer()
        return self._capturer.grab()

    # ------------------------------------------------ 视觉

    def _vision_jpeg(self, img):
        """压到最长边 MAX_SHOT_W 的 JPEG base64。截图不出内存。"""
        w, h = img.size
        if max(w, h) > MAX_SHOT_W:
            r = MAX_SHOT_W / float(max(w, h))
            img = img.resize((max(1, int(w * r)), max(1, int(h * r))))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode()

    def _try_vision(self, img, title, proc):
        """在候选模型间探测；全部失败进入冷却。返回归一化结果或 None。"""
        if not self.vision_fn or not self.vision_models:
            return None
        now = time.time()
        if now < self._vision_dead_until:
            return None
        jpeg_b64 = self._vision_jpeg(img)
        recent_notes = [e.get("note") for e in self._events[-5:]]
        messages = build_vision_messages(title, proc, jpeg_b64, recent_notes)
        n = len(self.vision_models)
        for step in range(n):
            idx = (self._vision_idx + step) % n
            model = self.vision_models[idx]
            try:
                content = self.vision_fn(messages, model)
                parsed = normalize_vision(parse_json_loose(content), title, proc)
                if parsed is None:
                    raise RuntimeError("返回无法解析为 JSON")
                self._vision_idx = idx
                self._vision_model = model
                self.stats["vision_ok"] += 1
                return parsed
            except Exception as e:
                self.stats["vision_fail"] += 1
                self.log("视觉模型 %s 失败: %r" % (model, e))
        self._vision_dead_until = now + VISION_RETRY_SECS
        self._vision_model = None
        self.log("视觉候选全部失败，冷却 %ds（基础感知继续）" % VISION_RETRY_SECS)
        return None

    # ------------------------------------------------ 事件

    def _append_event(self, evt):
        self._events.append(evt)
        if len(self._events) > EVENTS_KEEP:
            del self._events[:-EVENTS_KEEP]
        if self.events_path:
            try:
                d = os.path.dirname(self.events_path)
                if d and not os.path.isdir(d):
                    os.makedirs(d)
                with open(self.events_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            except Exception:
                pass      # 落盘只是锦上添花，写失败不挡感知

    # ------------------------------------------------ 主流程

    def tick(self):
        """跑一轮感知。返回 (changed, event)。抽出来不做私有的理由：测试要逐轮驱动。"""
        self.stats["ticks"] += 1
        self._maybe_probe()
        try:
            img = self._grab()
        except Exception as e:
            self.log("截屏失败: %r" % (e,))
            return False, self._last
        if img is None:
            return False, self._last

        h = dhash(img)
        if find_similar(h, self._recent_hashes) >= 0:
            # 画面没变：把这一轮间隔累进当前活动的驻留时长（WhalePal 同口径），
            # 不重新分类、不调视觉 —— 省钱也省 CPU。
            self.stats["dup"] += 1
            if self._last is not None:
                self._last["durationMs"] = self._last.get("durationMs", 0) + self.interval * 1000
            return False, self._last
        self._recent_hashes.append(h)
        if len(self._recent_hashes) > HASH_WINDOW:
            self._recent_hashes.pop(0)

        title, proc = self._window_fn()
        category, is_working = classify_window(title, proc)

        vision = self._try_vision(img, title, proc)
        if vision is not None:
            base = vision
        else:
            base = {
                "activity": ACTIVITY_TEXT[category],
                "category": category,
                "app": proc or "",
                "isWorking": is_working,
                "signal": "none",
                "speak": False,
                "detail": ("前台应用：%s；窗口：%s" % (proc or "未知", (title or "")[:40]))[:120],
                "note": "",
                "source": "basic",
            }

        now = time.time()
        prev = self._last
        evt = {
            "ts": now,
            "activity": base["activity"],
            "category": base["category"],
            "app": base.get("app") or proc or "",
            "title": (title or "")[:120],
            "isWorking": bool(base.get("isWorking")),
            "signal": base.get("signal") or "none",
            "detail": base.get("detail") or "",
            "note": base.get("note") or "",
            "source": base.get("source") or "basic",
            "durationMs": 0,
        }
        self._last = evt
        self._append_event(evt)

        # ---- 回调：每轮事件（事件流入库用，与节流无关） ----
        if self.on_event is not None:
            try:
                self.on_event(evt)
            except Exception as e:
                self.log("on_event 回调异常: %r" % (e,))

        # ---- 回调：类别变化（只上报，不说话——说话由 signal/note 两个口出） ----
        if prev is None or prev.get("category") != evt["category"]:
            self.stats["changes"] += 1
            if self.on_change is not None:
                try:
                    self.on_change(evt)
                except Exception as e:
                    self.log("on_change 回调异常: %r" % (e,))

        # ---- 回调：报错/成功反应（节流） ----
        if evt["signal"] in ("error", "success") and \
                now - self._last_signal_ts >= self.signal_gap:
            self._last_signal_ts = now
            self._signal_seq += 1
            self.stats["signals"] += 1
            if self.on_signal is not None:
                try:
                    self.on_signal(evt["signal"], evt)
                except Exception as e:
                    self.log("on_signal 回调异常: %r" % (e,))

        # ---- 回调：观察说出口（仅视觉给了 speak 且过了节流） ----
        if base.get("speak") and evt["note"] and \
                now - self._last_note_ts >= self.speak_gap:
            self._last_note_ts = now
            self.stats["notes"] += 1
            if self.on_note is not None:
                try:
                    self.on_note(evt["note"], evt)
                except Exception as e:
                    self.log("on_note 回调异常: %r" % (e,))

        return True, evt

    # ------------------------------------------------ 后台循环

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            if self._stop.is_set():
                return
            try:
                self.tick()
            except Exception as e:
                self.log("感知循环异常: %r" % (e,))

    def start(self):
        if not self.enabled:
            self.log("屏幕感知未启用")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # 首轮立即跑一次（不等一个周期），让她尽快"知道你在干嘛"
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log("屏幕感知已启用（周期=%ds 视觉=%s）"
                 % (self.interval, "候选×%d" % len(self.vision_models)
                    if self.vision_fn else "关（纯基础感知）"))

    def stop(self):
        self._stop.set()

    # ------------------------------------------------ 状态上报

    def is_working(self):
        """供给情境引擎的工作态闸门：事件过期（锁屏久了/感知停了）不算在工作。"""
        if not self.enabled or self._last is None:
            return False
        if not self._last.get("isWorking"):
            return False
        return (time.time() - self._last["ts"]) < max(WORKING_FRESH, self.interval * 3)

    @property
    def signal_seq(self):
        return self._signal_seq

    def state(self):
        """给 /api/context/activity 的完整快照。page 侧主要吃 signal / signal_seq。"""
        evt = dict(self._last) if self._last else None
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "interval": self.interval,
            "vision_model": self._vision_model,
            "vision_cooling": time.time() < self._vision_dead_until,
            "signal": (evt or {}).get("signal", "none"),
            "signal_seq": self._signal_seq,
            "isWorking": self.is_working(),
            "event": evt,
            "stats": dict(self.stats),
        }

    def recent(self, n=10):
        return [dict(e) for e in self._events[-n:]]


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    from PIL import Image

    ok = True

    def check(name, cond):
        global ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    def solid(color, size=(64, 64)):
        return Image.new("RGB", size, color)

    def stripes_vertical(size=(64, 64), invert=False):
        """竖条纹：每行内部有明暗交替 → dhash 有真实结构。
        ★ 为什么不用纯色图：dhash 看的是行内水平梯度，纯色/横条纹每行均匀，
          指纹恒为 0，彼此无法区分（这正是早期自测全挂的原因）。"""
        img = Image.new("RGB", size)
        px = img.load()
        hi, lo = ((0, 0, 0), (255, 255, 255)) if invert else ((255, 255, 255), (0, 0, 0))
        for y in range(size[1]):
            for x in range(size[0]):
                px[x, y] = hi if (x // 4) % 2 == 0 else lo
        return img

    def checker(size=(64, 64), cell=8):
        img = Image.new("RGB", size)
        px = img.load()
        for y in range(size[1]):
            for x in range(size[0]):
                px[x, y] = (255, 255, 255) if ((x // cell) + (y // cell)) % 2 == 0 else (0, 0, 0)
        return img

    def noisy(seed_img, amount=6):
        # 轻微噪声：同画面的小漂移（模拟鼠标微动/光标闪烁），不应触发重分析
        img = seed_img.copy()
        p2 = img.load()
        for y in range(img.size[1]):
            for x in range(img.size[0]):
                if (x * 7 + y * 13) % amount == 0:
                    r, g, b = p2[x, y]
                    p2[x, y] = (min(255, r + 4), g, b)
        return img

    print("[1] dhash 稳定与区分")
    img_v = stripes_vertical()
    img_c = checker()
    img_s = solid((120, 120, 120))
    h_v, h_c, h_s = dhash(img_v), dhash(img_c), dhash(img_s)
    check("同图同指纹", h_v == dhash(stripes_vertical()))
    check("微扰仍相似(≤阈值)", hamming(h_v, dhash(noisy(img_v))) <= SIMILARITY_THRESHOLD)
    check("布局不同→距离>阈值", hamming(h_v, h_c) > SIMILARITY_THRESHOLD)
    check("结构化vs纯色→距离>阈值", hamming(h_v, h_s) > SIMILARITY_THRESHOLD)
    check("find_similar 命中滑窗", find_similar(h_v, [h_c, dhash(noisy(img_v))], 4) == 1)
    check("find_similar 未命中", find_similar(h_v, [h_c, dhash(checker(cell=16))], 4) == -1)

    print("[2] 关键词分类表")
    check("VSCode→coding/工作", classify_window("main.py - Visual Studio Code", "Code.exe") == ("coding", True))
    check("新版微信进程→chat", classify_window("微信", "Weixin.exe") == ("chat", False))
    check("腾讯会议→meeting/工作", classify_window("腾讯会议", "wemeetapp.exe")[0] == "meeting")
    check("B站→video", classify_window("哔哩哔哩 (゜-゜)つロ 干杯", "chrome.exe") == ("video", False))
    check("锁屏空标题→idle", classify_window("", "") == ("idle", False))
    check("未知窗口→other", classify_window("随便一个窗口", "whatever.exe") == ("other", False))
    check("PowerShell→terminal/工作", classify_window("Windows PowerShell", "powershell.exe") == ("terminal", True))

    print("[3] JSON 容错解析")
    check("围栏可解", parse_json_loose('```json\n{"a":1}\n```') == {"a": 1})
    check("前后废话可解", parse_json_loose('好的{"a":2}完毕') == {"a": 2})
    check("垃圾返回 None", parse_json_loose("not json at all") is None)
    check("空返回 None", parse_json_loose("") is None)
    v = normalize_vision({"category": "nope", "signal": "weird", "isWorking": "yes",
                          "activity": "在写代码", "detail": "x" * 200}, "t - Visual Studio Code", "Code.exe")
    check("非法 category 回退基础分类", v["category"] == "coding")
    check("非法 signal 归 none", v["signal"] == "none")
    check("非布尔 isWorking 按类别推", v["isWorking"] is True)
    check("detail 截断", len(v["detail"]) <= 120)

    print("[4] 引擎：去重 / 视觉探测 / 节流")
    calls = {"vision": 0, "signal": [], "note": [], "change": []}
    frame = {"img": solid((40, 40, 40))}

    def fake_cap():
        return frame["img"]

    def fake_win():
        return ("main.py - Visual Studio Code", "Code.exe")

    def fake_vision(messages, model):
        calls["vision"] += 1
        if model == "bad-model":
            raise RuntimeError("不支持图像")
        return '{"activity":"调试桌宠","category":"coding","app":"Code","isWorking":true,' \
               '"signal":"none","speak":true,"detail":"在跑 npm test","note":"测试红了两个"}'

    eng = PerceptionEngine(
        on_change=lambda e: calls["change"].append(e["category"]),
        on_signal=lambda s, e: calls["signal"].append(s),
        on_note=lambda n, e: calls["note"].append(n),
        on_event=lambda e: calls.setdefault("event", []).append(e),
        vision_fn=fake_vision, vision_models=["bad-model", "good-model"],
        interval=60, speak_gap=300, signal_gap=180,
        capture_fn=fake_cap, window_fn=fake_win)

    changed, evt = eng.tick()
    check("首轮画面变化", changed is True)
    check("坏模型失败后换好模型", eng._vision_model == "good-model")
    check("视觉只调了两次(坏+好各一)", calls["vision"] == 2)
    check("事件来自视觉", evt["source"] == "vision")
    check("类别变化回调", calls["change"] == ["coding"])
    check("speak+note 触发开口回调", calls["note"] == ["测试红了两个"])
    check("is_working 为真", eng.is_working() is True)

    eng.tick()   # 同一张图 → 去重
    check("重复画面不重分析", calls["vision"] == 2)
    check("驻留时长累计", eng._last["durationMs"] == 60 * 1000)

    # 换画面 + 视觉报 error → signal 回调一次；再换画面仍 error → 被节流
    # （画面必须是有结构的图：纯色指纹恒为 0，会被滑窗判成重复帧）
    frame["img"] = checker()
    def fake_vision_err(messages, model):
        calls["vision"] += 1
        return '{"activity":"修 bug","category":"coding","isWorking":true,' \
               '"signal":"error","speak":false,"detail":"构建失败","note":""}'
    eng.vision_fn = fake_vision_err
    changed, evt = eng.tick()
    check("error 信号触发反应", calls["signal"] == ["error"])
    check("signal_seq 递增", eng.signal_seq == 1)
    frame["img"] = stripes_vertical(invert=True)
    eng.tick()
    check("间隔不足不再反应", calls["signal"] == ["error"])
    eng._last_signal_ts -= 999   # 模拟过了节流期
    frame["img"] = checker(cell=4)
    eng.tick()
    check("过了节流期可再反应", calls["signal"] == ["error", "error"])

    # 视觉全灭 → 冷却 + 基础感知兜底
    def fake_vision_dead(messages, model):
        calls["vision"] += 1
        raise RuntimeError("全部不可用")
    eng.vision_fn = fake_vision_dead
    eng._vision_dead_until = 0
    frame["img"] = stripes_vertical()
    changed, evt = eng.tick()
    check("视觉全灭退基础感知", evt["source"] == "basic")
    check("冷却后进入冷却期", eng._vision_dead_until > time.time())
    frame["img"] = checker(cell=16)
    v_before = calls["vision"]
    eng.tick()
    check("冷却期内零视觉调用", calls["vision"] == v_before)  # _try_vision 前置冷却直接返回 None
    # 新画面 tick 共 6 次（首轮/换画面×4/基础感知兜底），去重那次不发事件
    check("on_event 每轮新画面触发", len(calls.get("event", [])) == 6)

    print("[5] 视觉链路热重探")
    probes = {"n": 0}

    def probe_ok():
        probes["n"] += 1
        return (fake_vision, ["late-model"])

    eng2 = PerceptionEngine(vision_fn=None, vision_models=[], probe_fn=probe_ok,
                            interval=60, capture_fn=fake_cap, window_fn=fake_win)
    frame["img"] = checker(cell=8)
    changed, evt = eng2.tick()
    check("重探探到即热接", eng2.vision_models == ["late-model"])
    check("热接后当轮即用视觉", evt["source"] == "vision")
    check("probe 只调一次", probes["n"] == 1)
    eng2._maybe_probe()
    check("PROBE_GAP 内不重复探", probes["n"] == 1)

    def probe_none():
        probes["n"] += 1
        return (None, [])

    eng3 = PerceptionEngine(vision_fn=None, vision_models=[], probe_fn=probe_none,
                            interval=60, capture_fn=fake_cap, window_fn=fake_win)
    frame["img"] = stripes_vertical(invert=True)
    changed, evt = eng3.tick()
    check("探不到退基础感知", evt["source"] == "basic")
    check("probe 失败同样计入节流", probes["n"] == 2)

    print("[6] 状态快照")
    st = eng.state()
    check("state 含 signal_seq", st["signal_seq"] == 2)
    check("recent 限量", len(eng.recent(3)) == 3)

    print("\n结果:", "全部通过" if ok else "存在失败")
    raise SystemExit(0 if ok else 1)
