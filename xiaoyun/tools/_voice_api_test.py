# -*- coding: utf-8 -*-
"""联调：文字聊天回归 + 语音会话 + 语音全链路（含对话）"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8090"


def call(path, obj=None, method="GET", timeout=120):
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("=== 1. 文字聊天回归 ===", flush=True)
d = call("/api/chat", {"message": "你好呀，今天辛苦了"}, "POST")
print("ok=%s 回复=%s" % (d.get("ok"), (d.get("reply") or "")[:70]))
print("有语音=%s play_here=%s seq=%s" % (bool(d.get("audio")), d.get("play_here"), d.get("seq")), flush=True)
print("（等她说完话，等 5 秒再测语音）", flush=True)
time.sleep(5)

print("\n=== 2. 语音：按键说话 start -> 等2秒 -> stop ===", flush=True)
print("start:", call("/api/voice/start", {"mode": "ptt"}, "POST", 20), flush=True)
time.sleep(2)
st = call("/api/voice/state")
print("state=%s recording=%s device=%s blocks=%d" %
      (st["state"], st["recording"], st.get("device"), st.get("blocks", 0)), flush=True)
print("stop :", call("/api/voice/stop", method="POST"), flush=True)
time.sleep(3)
st = call("/api/voice/state")
print("stop后 state=%s detail=%s" % (st["state"], st.get("detail")), flush=True)

print("\n=== 3. 语音：连续对话模式开关 ===", flush=True)
print("mode:", call("/api/voice/mode", {"mode": "vad"}, "POST"), flush=True)
print("start:", call("/api/voice/start", {"mode": "vad"}, "POST", 20), flush=True)
time.sleep(2)
st = call("/api/voice/state")
print("state=%s mode=%s recording=%s blocks=%d" %
      (st["state"], st["mode"], st["recording"], st.get("blocks", 0)), flush=True)
print("cancel:", call("/api/voice/cancel", method="POST"), flush=True)
print("mode复位:", call("/api/voice/mode", {"mode": "ptt"}, "POST"), flush=True)

print("\n=== 4. 语音消费者线程是否正常（看后端日志有无报错） ===", flush=True)
print("last:", json.dumps({k: v for k, v in call("/api/last?since=0").items() if k != "audio"},
                          ensure_ascii=False)[:200], flush=True)
