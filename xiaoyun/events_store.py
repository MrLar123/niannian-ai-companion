# -*- coding: utf-8 -*-
"""events_store —— 活动事件流存储（P1：让她答得出"我刚才/今天在干嘛"）。

与 memory_store 同库（memory.db）分表：facts 是"关于你的长期事实"，
events 是"你什么时候在干嘛"的时序记录——WhalePal 的 L0/L1 事件层对应物。
两者互补：事实答"你是什么样的人"，事件答"你刚才在做什么"。

设计约束：
- 只增不改：感知每轮 append 一条，60s 一条，一天 ~1440 条，量级很小；
- 查询走 SQL 聚合不走 LLM：今日回顾是确定性计算（按类别/应用汇总时长），
  只有"讲给用户听"那一步才轮到她自己的口吻（由大脑在对话里现组织）；
- 过期清理在初始化和每日首查时各跑一次，DELETE 带 WHERE，不做无界增长；
- 纯 SQLite 单文件，无新依赖；库损坏/表缺失自动重建，绝不拖垮感知。

环境变量：
  XIAOYUN_EVENTS_KEEP   事件保留天数（默认 7）
"""
import json
import os
import sqlite3
import threading
import time

EVENTS_KEEP_DAYS = int(os.environ.get("XIAOYUN_EVENTS_KEEP", "7"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    ts         REAL PRIMARY KEY,   -- unix 秒（感知引擎的 evt["ts"]）
    activity   TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'other',
    app        TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    isWorking  INTEGER NOT NULL DEFAULT 0,
    signal     TEXT NOT NULL DEFAULT 'none',
    source     TEXT NOT NULL DEFAULT 'basic',
    detail     TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_cat ON events(category);
"""


class EventStore(object):
    """活动事件存储。线程安全：感知引擎线程写、Flask 线程读，用一把锁。"""

    def __init__(self, db_path, keep_days=EVENTS_KEEP_DAYS, log=None):
        self.path = db_path
        self.keep_days = max(1, int(keep_days))
        self.log = log or (lambda *a: None)
        self._lock = threading.Lock()
        d = os.path.dirname(db_path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._last_prune_day = -1

    # ------------------------------------------------ 写入

    def add(self, evt):
        """存一条感知事件。缺字段兜底，坏数据不挡感知。"""
        row = (
            float(evt.get("ts") or time.time()),
            (evt.get("activity") or "未知")[:200],
            (evt.get("category") or "other")[:40],
            (evt.get("app") or "")[:120],
            (evt.get("title") or "")[:200],
            1 if evt.get("isWorking") else 0,
            (evt.get("signal") or "none")[:20],
            (evt.get("source") or "basic")[:20],
            (evt.get("detail") or "")[:500],
            (evt.get("note") or "")[:500],
        )
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", row)
                self._maybe_prune()
                self._conn.commit()
        except Exception as e:
            self.log("事件入库失败: %r" % (e,))

    # ------------------------------------------------ 查询

    def last(self):
        """最近一条事件（dict 或 None）。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT 1").fetchone()
        return self._row(r)

    def recent(self, n=10, max_age=None):
        """最近 n 条（新→旧）。max_age 秒内过滤。"""
        q = "SELECT * FROM events"
        args = []
        if max_age:
            q += " WHERE ts >= ?"
            args.append(time.time() - max_age)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, int(n)))
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._row(r) for r in rows]

    def count(self):
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def aggregate(self, window_secs=None, day_start=None):
        """聚合一份"在干嘛"的摘要（今日回顾 / 注入上下文共用）。

        window_secs  最近 N 秒（如 7200 = 最近两小时）
        day_start    今天 0 点的时间戳（与 window_secs 二选一）
        返回 dict：total_secs / by_category / by_app / working_ratio /
                   signals / span（首尾事件）
        时长口径：相邻事件 ts 差，最后一条按感知周期补齐（~60s）。
        """
        if day_start is not None:
            lo = float(day_start)
        elif window_secs is not None:
            lo = time.time() - float(window_secs)
        else:
            lo = 0.0
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, category, app, isWorking, signal FROM events "
                "WHERE ts >= ? ORDER BY ts ASC", (lo,)).fetchall()
        if not rows:
            return {"total_secs": 0, "by_category": {}, "by_app": {},
                    "working_ratio": 0.0, "signals": 0, "span": None}

        by_cat, by_app, working_secs, signals = {}, {}, 0.0, 0
        total = 0.0
        for i, (ts, cat, app, work, sig) in enumerate(rows):
            # 事件时长 = 到下一条的间隔；最后一条按一个感知周期估
            dur = (rows[i + 1][0] - ts) if i + 1 < len(rows) else 60.0
            dur = min(dur, 600.0)   # 断档（感知停过/休眠）不把时长记到上一段头上
            total += dur
            by_cat[cat] = by_cat.get(cat, 0.0) + dur
            key = app or "未知"
            by_app[key] = by_app.get(key, 0.0) + dur
            if work:
                working_secs += dur
            if sig in ("error", "success"):
                signals += 1
        return {
            "total_secs": round(total),
            "by_category": {k: round(v) for k, v in
                            sorted(by_cat.items(), key=lambda x: -x[1])},
            "by_app": {k: round(v) for k, v in
                       sorted(by_app.items(), key=lambda x: -x[1])[:6]},
            "working_ratio": round(working_secs / total, 2) if total else 0.0,
            "signals": signals,
            "span": {"from": rows[0][0], "to": rows[-1][0]},
        }

    def today_summary(self, now=None):
        """今天 0 点起的聚合（/api/context/today 用）。"""
        now = now or time.time()
        lt = time.localtime(now)
        day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        return self.aggregate(day_start=day_start)

    # ------------------------------------------------ 维护

    def _maybe_prune(self):
        """每日首次写入时清一次过期（调用方已持锁）。"""
        day = int(time.time() // 86400)
        if day == self._last_prune_day:
            return
        self._last_prune_day = day
        try:
            self._conn.execute("DELETE FROM events WHERE ts < ?",
                               (time.time() - self.keep_days * 86400,))
        except Exception:
            pass

    def prune_now(self):
        """强制清理一次（不等每日守卫）：运维/测试用。"""
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE ts < ?",
                               (time.time() - self.keep_days * 86400,))
            self._conn.commit()

    # ------------------------------------------------ 上下文注入

    def context_block(self, recent_secs=7200):
        """给大脑的"最近在干嘛"短文本块。空库返回 ""（调用方跳过注入）。

        只给结论不给流水账：最后一件事 + 时间窗聚合的前两类 + 工作占比。
        一两行内说完，别把 system prompt 撑肥。"""
        last = self.last()
        if last is None:
            return ""
        ago = int((time.time() - last["ts"]) / 60)
        if ago > 30:
            # 事件太旧（感知停了/锁屏很久）：只说"最近一次"，不冒充现在
            head = "主人最近一次被看到是在 %d 分钟前：%s" % (ago, last["activity"])
        else:
            head = "主人此刻正在：%s" % last["activity"]
        agg = self.aggregate(window_secs=recent_secs)
        if not agg["total_secs"]:
            return head + "。"
        parts = []
        for cat, secs in list(agg["by_category"].items())[:2]:
            parts.append("%s约%d分钟" % (CAT_TEXT.get(cat, cat), secs // 60))
        line = "%s；近两小时：%s（工作占比 %d%%）。" % (
            head, "、".join(parts), int(agg["working_ratio"] * 100))
        return line

    @staticmethod
    def _row(r):
        if r is None:
            return None
        keys = ("ts", "activity", "category", "app", "title", "isWorking",
                "signal", "source", "detail", "note")
        return dict(zip(keys, r))

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


CAT_TEXT = {
    "coding": "写代码", "office": "处理文档/办公", "writing": "写东西",
    "design": "做设计", "meeting": "开会", "reading": "看资料",
    "video": "看视频", "game": "打游戏", "music": "听歌",
    "browse": "逛网页", "chat": "聊天", "terminal": "敲命令行",
    "other": "日常操作",
}


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    ok = True

    def check(name, cond):
        global ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    import tempfile
    tmp = tempfile.mktemp(suffix=".db")

    def evt(ts, cat="coding", app="VSCode", work=True, sig="none"):
        return {"ts": ts, "activity": "在%s里忙" % app, "category": cat,
                "app": app, "title": "t", "isWorking": work, "signal": sig,
                "source": "vision", "detail": "d", "note": "n"}

    print("[1] 写入与读取")
    st = EventStore(tmp)
    base = time.time() - 600
    for i in range(6):
        st.add(evt(base + i * 60, "coding" if i < 4 else "video",
                   "VSCode" if i < 4 else "PotPlayer", work=i < 4,
                   sig="error" if i == 2 else "none"))
    check("count=6", st.count() == 6)
    check("last 是最新", st.last()["app"] == "PotPlayer")
    check("recent 限量", len(st.recent(3)) == 3)
    check("recent 时序", st.recent(3)[0]["ts"] > st.recent(3)[2]["ts"])
    # 事件分布在 now-600 ~ now-300；450s 窗口应命中 3 条（i=3,4,5）
    check("max_age 过滤", len(st.recent(10, max_age=450)) == 3)

    print("[2] 聚合")
    agg = st.aggregate(window_secs=700)
    check("总时长≈300s", abs(agg["total_secs"] - 300) <= 65)
    check("类别排序 coding 在前", list(agg["by_category"])[0] == "coding")
    check("工作占比≈2/3", 0.5 < agg["working_ratio"] < 0.85)
    check("信号计数=1", agg["signals"] == 1)
    today = st.today_summary(now=base + 600)
    check("今日聚合有数据", today["total_secs"] > 0)

    print("[3] 上下文块")
    blk = st.context_block()
    check("含『此刻正在』", "此刻正在" in blk)
    check("含近两小时", "近两小时" in blk)
    check("含类别中文", "写代码" in blk)
    old = EventStore(tempfile.mktemp(suffix=".db"))
    old.add(evt(time.time() - 3600))
    check("旧事件改口『最近一次』", "最近一次" in old.context_block())
    empty = EventStore(tempfile.mktemp(suffix=".db"))
    check("空库返回空串", empty.context_block() == "")

    print("[4] 清理与容错")
    st.add(evt(1.0))     # 极旧时间戳 → 强制清理应清掉
    st.prune_now()
    check("过期已清", st.count() == 6)
    st.add({"ts": None})  # 缺 ts 用当前时间兜底，缺字段全有默认 → 不炸
    check("坏事件被兜底", st.count() == 7)

    print("\n结果:", "全部通过" if ok else "存在失败")
    raise SystemExit(0 if ok else 1)
