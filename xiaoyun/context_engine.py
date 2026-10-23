# -*- coding: utf-8 -*-
"""
念念 · 情境引擎（时段感知 + 主动搭话 + 节奏调节）

设计来源（借鉴，非照搬）：
- 「时段细分 + 到点触发」的骨架，借鉴本机 D:/LunaAI/app/AI-Vtuber-20240229/api.py
  的 schedule_task()（第 107 行起）与 idle_time_task()（第 249 行起）。
  其原始实现是"从配置里随机挑一条固定文案念出来"，用在直播场景（面向弹幕）。
- 「按关键词条件注入设定」的思路，参考 SillyTavern Character Card V2 的
  character_book / lorebook 机制（条目带 keys + content，命中才注入上下文）。

与借鉴对象的关键差异（为什么不直接照搬）：
1. AI-Vtuber 是**固定文案轮播**（copy 列表 + random.choice）。念念要做的是
   "主动关心你"，固定文案第二天就腻了。故改为**给 LLM 一句话意图，由她现生成**
   ——同一时段不同日子说的话不一样，且能带上记忆（"你又加班到这么晚"）。
2. AI-Vtuber 的 idle 计数是全局秒表，只在直播推流时走。念念的"空闲"定义更细：
   既要判断**距上次对话**多久，也要判断**当前是否已到该开口的时机**，
   还要避开她正在说话 / 正在收音的时间窗（否则会自问自答，这个坑项目里踩过）。
3. 时段分档沿用其七分段（凌晨/早晨/上午/中午/下午/傍晚/晚上），
   但每段额外补了"该时段适合说什么"的语义提示，让 LLM 有据可依。

纯标准库，无外部依赖。
"""
import datetime
import random
import threading
import time


# ------------------------------------------------------------------ 时段

# 借鉴 AI-Vtuber schedule_task 的分段边界，并补充语义提示。
# (起始小时, 结束小时不含, 名称, 语气提示)
SLOTS = [
    (0,  6,  "凌晨", "已经很晚了，她该劝我睡觉，语气要克制但明显担心，不要长篇大论"),
    (6,  9,  "早晨", "我刚起床，她可以有刚醒的慵懒感，问一句睡得好不好"),
    (9,  12, "上午", "工作时段，她在忙自己的事，随口搭一句，不打扰"),
    (12, 13, "中午", "午饭时间，可以问吃了没，别催"),
    (13, 18, "下午", "下午容易犯困，她可以轻轻逗一下我，节奏活跃些"),
    (18, 20, "傍晚", "快下班了，她可以问今天累不累，语气软一点"),
    (20, 24, "晚上", "夜里她话会多一点，适合聊心事，语气亲近"),
]


def time_slot(now=None):
    """返回 (时段名, 语气提示)。按小时落档，凌晨跨零点已正确归入 0-6 档。"""
    h = (now or datetime.datetime.now()).hour
    for a, b, name, hint in SLOTS:
        if a <= h < b:
            return name, hint
    return "深夜", "夜很深了，她该催我去睡"


def time_text(now=None):
    """人类可读的当前时间，给提示词用。"""
    n = now or datetime.datetime.now()
    return n.strftime("%Y年%m月%d日 %H:%M")


# ------------------------------------------------------------------ 主动搭话意图

# 每种触发场景对应一句"给 LLM 的指令"。她据此**现生成**，而非念固定台词。
# 这是与 AI-Vtuber 固定文案轮播最本质的差别。
IDLE_INTENTS = [
    "你已经有一阵子没吭声了，主动跟他说一句话，关心一下他手头在做什么。",
    "你们沉默了一会儿，随口说一句你在想什么，或者吐槽一下自己的状态。",
    "主动问他一句今天过得怎么样，语气自然，不要像客服。",
    "有一会儿没讲话了，可以说一句简短的话让他知道你还在，比如提一句天气、时间或自己的小动作。",
]

# 深夜专属：这个时段她最该开口（用户熬夜是真实痛点）
NIGHT_INTENTS = [
    "已经这么晚了，催他去睡觉，语气嗔怪但藏不住担心，一到两句就够。",
    "深夜了，提醒他别熬了，可以带一点'我陪着你但你得睡了'的意思。",
]

GREETING_INTENTS = [
    "他刚回来/刚打开你，跟他打个招呼，语气像等了一会儿的老朋友，自然一点。",
    "主动迎接他，可以提一下现在的时间或你刚才在做什么，一句话就够。",
]


class ContextEngine(object):
    """情境判定与主动开口调度。

    不直接持有 LLM 或后端——通过注入的 callbacks 与外界交互，
    好处是这一层可以完全离线单测（见文件末尾）。
    """

    def __init__(self, speak, log=None, enabled=True, idle_seconds=900,
                 randomize=True, min_gap=600, greeting_after=180):
        """
        speak(intent, trigger) -> 由外部负责生成并播放这句话
        idle_seconds   多少秒没对话算"空闲"，可以主动开口
        randomize      是否把空闲阈值随机化（避免每天同一时刻冒出来）
        min_gap        两次主动开口之间的最小间隔，防止絮叨
        greeting_after 距上次交互超过这么久，重新出现时算"刚回来"
        """
        self.speak = speak
        self.log = log or (lambda *a: None)
        self.enabled = enabled
        self.idle_seconds = idle_seconds
        self.randomize = randomize
        self.min_gap = min_gap
        self.greeting_after = greeting_after

        self.last_user_ts = time.time()     # 最近一次用户说话
        # ★ 初值给当前时间而不是 0：否则 since_speak 会是个天文数字，
        #   既让 /api/context/state 读起来莫名其妙，也让"刚开口过"的判断失真。
        self.last_speak_ts = time.time()     # 最近一次她开口（主动或被动）
        self._next_idle = self._roll_idle()
        self._stop = threading.Event()
        self._thread = None
        self.stats = {"proactive": 0, "greetings": 0, "skipped": 0}

    # ------------------------------------------------ 状态上报

    def touch_user(self, ts=None):
        """用户说话了：重置空闲计时，并重新掷下一次空闲阈值。"""
        self.last_user_ts = ts or time.time()
        self._next_idle = self._roll_idle()

    def note_speak(self, ts=None):
        """她开口了（无论被动回答还是主动）：记录时间，避免紧接着又主动说。"""
        self.last_speak_ts = ts or time.time()

    def _roll_idle(self):
        """空闲阈值。随机化时取 0.6~1.4 倍基础值——固定周期会让主动搭话像闹钟。"""
        if not self.randomize:
            return self.idle_seconds
        return int(self.idle_seconds * random.uniform(0.6, 1.4))

    # ------------------------------------------------ 触发判定（纯函数，可测）

    def decide(self, now=None, busy=False, listening=False, working=False):
        """判定此刻是否该主动开口。返回 (是否开口, 触发类型, 意图文本)。

        纯函数：不产生副作用，便于用固定时间戳穷举测试。
        busy/listening 分别表示"她正在说话"与"麦克风正在收音"——
        两者任一为真都必须闭嘴，否则会出现自己录到自己、或抢话的怪象。
        working 表示"主人正在专注工作"（屏幕感知给的 isWorking）——
        借鉴 WhalePal 的"工作态不插嘴"：闲聊与问候都压住。
        ★ 唯一的例外是凌晨档的催睡：那是"关怀"不是闲聊（WhalePal 的深夜劝休息
          同样在工作态放行），用户熬夜是真实痛点，不该被工作闸门一起掐掉。
        """
        now = now or time.time()
        since_user = now - self.last_user_ts
        since_speak = now - self.last_speak_ts

        # ★ 时段必须用同一个 now 推导：否则传入的时间戳与真实时钟不一致时
        #   （跨时段测试、时钟回拨），计时判定和意图选择会打架。
        # night_care：凌晨档的催睡是"关怀"不是闲聊（WhalePal 的深夜劝休息
        #   同样在工作态放行）——用户熬夜是真实痛点，不该被工作闸门一起掐掉。
        name, _hint = time_slot(datetime.datetime.fromtimestamp(now))
        night_care = (name == "凌晨")

        # 硬闸门：她正在发声或正在收音，任何情况都不主动开口
        if busy or listening:
            return False, "blocked", ""

        # 刚开口过，别絮叨
        if since_speak < self.min_gap:
            return False, "cooldown", ""

        # 场景 A：久未交互后重新出现 —— 先问候，不直接进关心的正题
        if since_user >= self.greeting_after and since_user >= self._next_idle * 0.5:
            if not working:
                intent = random.choice(GREETING_INTENTS)
                return True, "greeting", intent
            if not night_care:
                return False, "working", ""
            # 凌晨 + 工作态：问候让位给催睡关怀，落入下方深夜意图池

        # 场景 B：正常空闲 —— 按当前时段挑意图
        if since_user >= self._next_idle:
            if name == "凌晨":
                pool = NIGHT_INTENTS      # 关怀类：工作态也放行
            elif working:
                return False, "working", ""
            else:
                pool = IDLE_INTENTS
            return True, "idle", random.choice(pool)

        return False, "waiting", ""

    # ------------------------------------------------ 后台循环

    def start(self, is_busy, is_listening, is_working=None):
        """启动后台判定线程。is_busy / is_listening 由外部注入（读后端实时状态）；
        is_working 可选（屏幕感知的"主人在专注工作"），不传则与旧行为完全一致。"""
        if not self.enabled:
            self.log("情境引擎未启用")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(is_busy, is_listening, is_working), daemon=True)
        self._thread.start()
        self.log("情境引擎已启动（空闲阈值=%ds 最小间隔=%ds）"
                 % (self.idle_seconds, self.min_gap))

    def stop(self):
        self._stop.set()

    def _loop(self, is_busy, is_listening, is_working=None):
        while not self._stop.is_set():
            try:
                time.sleep(10)              # 10s 粒度足够；再密就是白耗 CPU
                if self._stop.is_set():
                    return
                ok, trigger, intent = self.decide(
                    busy=bool(is_busy()), listening=bool(is_listening()),
                    working=bool(is_working()) if is_working else False)
                if not ok:
                    continue
                self.last_speak_ts = time.time()
                self._next_idle = self._roll_idle()
                if trigger == "greeting":
                    self.stats["greetings"] += 1
                else:
                    self.stats["proactive"] += 1
                self.log("主动开口(%s): %s" % (trigger, intent[:40]))
                try:
                    self.speak(intent, trigger)
                except Exception as e:
                    self.log("主动开口失败: %r" % (e,))
            except Exception as e:
                self.log("情境循环异常: %r" % (e,))

    # ------------------------------------------------ 提示词片段

    def prompt_block(self):
        """给主对话的时段块。让她知道"现在几点、该是什么状态"——
        这是"主动关怀比被动问答更像伴侣"的底层前提：不知道时间就不可能共情。"""
        name, hint = time_slot()
        return "【当前情境】现在是%s（%s）。%s。" % (
            time_text(), name, hint)


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    ok = True

    def check(name, cond):
        global ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    print("[1] 时段分档（逐小时穷举）")
    cases = {0: "凌晨", 3: "凌晨", 5: "凌晨", 6: "早晨", 8: "早晨",
             9: "上午", 11: "上午", 12: "中午", 13: "下午", 17: "下午",
             18: "傍晚", 19: "傍晚", 20: "晚上", 23: "晚上", 22: "晚上"}
    bad = []
    for h, want in cases.items():
        got = time_slot(datetime.datetime(2026, 10, 19, h, 30))[0]
        if got != want:
            bad.append("%d点->%s(期望%s)" % (h, got, want))
    check("15 个小时档全对", not bad)
    if bad:
        print("     偏差:", bad)
    check("24点时边界", time_slot(datetime.datetime(2026, 10, 19, 0, 0))[0] == "凌晨")

    print("[2] 空闲未到不开口")
    said = []
    eng = ContextEngine(lambda i, t: said.append((t, i)),
                        idle_seconds=100, randomize=False, min_gap=0)
    # ★ 基准时间用真实时钟：引擎内部 last_speak_ts 初值就是 time.time()，
    #   测试若用合成小时间戳，since_speak 会变成负数、min_gap 判定失真。
    #   用真实基准 = 与生产行为一致，测的才是真逻辑。
    base = time.time()
    eng.touch_user(base)
    check("刚说完话不开口", eng.decide(now=base + 50)[0] is False)
    check("状态为等待", eng.decide(now=base + 50)[1] == "waiting")

    print("[3] 空闲到点开口")
    okd, trig, intent = eng.decide(now=base + 120)
    check("到点开口", okd is True)
    check("类型=idle", trig == "idle")
    check("有意图文本", bool(intent))

    print("[4] 硬闸门：说话中/收音中永不开口")
    eng2 = ContextEngine(lambda i, t: None, idle_seconds=100,
                         randomize=False, min_gap=0)
    eng2.touch_user(base)
    for kw in [{"busy": True}, {"listening": True}]:
        r = eng2.decide(now=base + 5000, **kw)
        check("%s 时不开口" % list(kw)[0], r[0] is False and r[1] == "blocked")

    print("[5] 最小间隔防絮叨")
    eng3 = ContextEngine(lambda i, t: None, idle_seconds=10,
                         randomize=False, min_gap=600)
    eng3.touch_user(base)
    eng3.note_speak(base + 20)
    check("刚开口过不重复", eng3.decide(now=base + 120)[0] is False)
    check("超间隔后恢复", eng3.decide(now=base + 700)[0] is True)

    print("[6] 久别重逢走问候")
    eng4 = ContextEngine(lambda i, t: None, idle_seconds=100,
                         randomize=False, min_gap=0, greeting_after=180)
    eng4.touch_user(base)
    r = eng4.decide(now=base + 900)
    check("久未交互=greeting", r[1] == "greeting")

    print("[7] 深夜走催促睡觉的意图池")
    eng5 = ContextEngine(lambda i, t: None, idle_seconds=100,
                         randomize=False, min_gap=0)
    # 用凌晨时间戳构造：今天 0:30。两个时间戳都要设，否则 since_speak
    # 仍指向真实当前时刻，会被 min_gap 误判成"刚开口过"。
    t0 = datetime.datetime.now().replace(hour=0, minute=30).timestamp()
    eng5.last_user_ts = t0
    eng5.last_speak_ts = t0
    okd, trig, intent = eng5.decide(now=t0 + 120)
    check("凌晨触发", okd is True and trig == "idle")
    check("深夜意图池命中", any(k in intent for k in ["睡觉", "熬", "陪着"]))

    print("[8] 后台线程真跑（1 次触发）")
    fired = []
    eng6 = ContextEngine(lambda i, t: fired.append(t), idle_seconds=1,
                         randomize=False, min_gap=0)
    eng6.touch_user(0)          # 初始就把上次交互设为很早
    eng6.start(is_busy=lambda: False, is_listening=lambda: False)
    for _ in range(30):
        time.sleep(0.5)
        if fired:
            break
    eng6.stop()
    check("后台线程触发了主动开口", len(fired) >= 1)
    check("统计已记录", eng6.stats["proactive"] + eng6.stats["greetings"] >= 1)

    print("[9] 提示词块")
    blk = eng6.prompt_block()
    check("含【当前情境】", "【当前情境】" in blk)
    check("含具体时刻", "现在" in blk)

    print("[10] 工作态门控（屏幕感知 isWorking）")
    # 白天工作态：闲聊压住
    t_day = datetime.datetime.now().replace(hour=14, minute=0).timestamp()
    eng7 = ContextEngine(lambda i, t: None, idle_seconds=100,
                         randomize=False, min_gap=0)
    eng7.last_user_ts = t_day
    eng7.last_speak_ts = t_day
    r = eng7.decide(now=t_day + 200, working=True)
    check("工作态闲聊被压住", r[0] is False and r[1] == "working")
    check("同一时刻非工作态放行", eng7.decide(now=t_day + 200, working=False)[0] is True)
    # 工作态：久别问候也压住
    eng8 = ContextEngine(lambda i, t: None, idle_seconds=100,
                         randomize=False, min_gap=0, greeting_after=180)
    eng8.last_user_ts = t_day
    eng8.last_speak_ts = t_day
    r = eng8.decide(now=t_day + 900, working=True)
    check("工作态问候被压住", r[0] is False and r[1] == "working")
    # 凌晨工作态：催睡（关怀）仍放行
    t_night = datetime.datetime.now().replace(hour=1, minute=30).timestamp()
    eng9 = ContextEngine(lambda i, t: None, idle_seconds=100,
                         randomize=False, min_gap=0)
    eng9.last_user_ts = t_night
    eng9.last_speak_ts = t_night
    r = eng9.decide(now=t_night + 200, working=True)
    check("凌晨工作态催睡仍放行", r[0] is True and r[1] == "idle")
    check("凌晨命中深夜意图池", any(k in r[2] for k in ["睡觉", "熬", "陪着"]))

    print("\n结果:", "全部通过" if ok else "存在失败")
    raise SystemExit(0 if ok else 1)
