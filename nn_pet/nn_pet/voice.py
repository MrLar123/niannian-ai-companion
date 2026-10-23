# -*- coding: utf-8 -*-
"""HistoryStore + VoiceSession — 会话状态与语音时序。

HistoryStore：seq 广播式历史（原 LAST / LAST_CLAIM / HISTORY 全局态）。
VoiceSession：语音会话时序（原 VOICE / PLAYBACK / 守卫逻辑）——
  语音时序铁律：页面周期 POST /api/voice/playing {seq,dur}；resume 在
  busy 下委派；主动开口走 publish_reply 共用函数。
"""
import threading
import time


class HistoryStore:
    """最近 N 轮对话 + 最新回复广播（带播放权认领）。"""

    def __init__(self, max_items=24):
        self._lock = threading.Lock()
        self._items = []          # [{"role","content"}...]
        self._seq = 0             # 每条新回复自增
        self._last = None         # 最新回复 {"seq","text","ts"}
        self._claim = {"seq": 0, "ts": 0.0}  # 播放权（防双页同播）
        self.max_items = max_items

    def append_user(self, text):
        with self._lock:
            self._items.append({"role": "user", "content": text})
            self._trim()

    def append_reply(self, text):
        """发布新回复：入库 + seq 自增广播。返回 seq。"""
        with self._lock:
            self._items.append({"role": "assistant", "content": text})
            self._trim()
            self._seq += 1
            self._last = {"seq": self._seq, "text": text, "ts": time.time()}
            return self._seq

    def _trim(self):
        if len(self._items) > self.max_items:
            self._items[:] = self._items[-self.max_items:]

    def messages(self):
        with self._lock:
            return list(self._items)

    def last(self, since=0):
        with self._lock:
            if not self._last or self._last["seq"] <= since:
                return None
            return dict(self._last)

    def claim_playback(self, seq, ttl=2.5):
        """同一 seq 只给一个页面播（原 LAST_CLAIM）。返回 True=认领成功。"""
        now = time.time()
        with self._lock:
            if self._claim["seq"] == seq and now - self._claim["ts"] < ttl:
                return False
            self._claim.update({"seq": seq, "ts": now})
            return True

    def unclaim(self, seq):
        with self._lock:
            if self._claim["seq"] == seq:
                self._claim.update({"seq": 0, "ts": 0.0})


class VoiceSession:
    """语音会话时序状态机（原 VOICE/PLAYBACK 容器，线程安全）。

    状态: off → listening → pending(转写完) → model(等大脑)
          → playing(她说话) → wrapping(0.8s 收尾) → listening
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.state = "off"
        self.busy = False            # 她正在回复（voice_start 硬闸）
        self.mode = "vad"            # vad / ptt
        self.last_seq = 0
        self.last_reply_ts = 0.0     # 自听守卫：刚回复完不收音
        self.playback = {"seq": 0, "start": 0.0, "dur": 0.0, "ts": 0.0}
        self.last_text = ""

    # ---- 生命周期 ----
    def start(self):
        with self._lock:
            if self.busy or self.state in ("listening", "pending", "model"):
                return False, "busy" if self.busy else "already_active"
            self.state = "listening"
            return True, ""

    def set_pending(self, text):
        with self._lock:
            self.last_text = text or ""
            self.state = "pending" if self.last_text else "off"

    def set_model(self):
        with self._lock:
            self.state = "model"
            self.busy = True

    def report_playing(self, seq, dur):
        """页面上报真实播放时长（dur 单调最大，只认本轮 seq）。"""
        with self._lock:
            if seq != self.playback["seq"]:
                self.playback = {"seq": seq, "start": time.time(),
                                 "dur": float(dur or 0), "ts": time.time()}
            else:
                self.playback["dur"] = max(self.playback["dur"],
                                           float(dur or 0))
                self.playback["ts"] = time.time()
            self.state = "playing"

    def finish_reply(self, seq):
        """回复播完收尾：清 busy，落 last_reply_ts（自听守卫用）。"""
        with self._lock:
            self.busy = False
            self.last_seq = seq
            self.last_reply_ts = time.time()
            self.state = "wrapping"

    def resume_ok(self):
        """resume 委派判定：busy 时委派给收尾闸（返回 False）。"""
        with self._lock:
            if self.busy:
                return False
            self.state = "listening"
            return True

    def stop(self):
        with self._lock:
            self.state = "off"
            self.busy = False

    # ---- 快照（/api/voice/state 输出）----
    def snapshot(self):
        with self._lock:
            return {
                "state": self.state, "busy": self.busy, "mode": self.mode,
                "last_seq": self.last_seq,
                "playback_dur": self.playback["dur"],
                "last_text": self.last_text,
            }
