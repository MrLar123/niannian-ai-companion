# -*- coding: utf-8 -*-
"""真实全链路测试：放测试语音 -> 麦克风采集 -> VAD -> 转写 -> 取走文字
轮询一直等到 pending，避免 race。"""
import json, time, urllib.request, wave
import numpy as np, av, sounddevice as sd

BASE = "http://127.0.0.1:8091"


def post(path, obj=None):
    data = json.dumps(obj).encode() if obj is not None else b""
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=180))


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=10))


c = av.open("tools/_asr_test.mp3")
parts = []
for f in c.decode(c.streams.audio[0]):
    a = f.to_ndarray()
    if a.ndim > 1:
        a = a.mean(axis=0)
    parts.append(np.asarray(a, dtype=np.float32).reshape(-1))
a = np.concatenate(parts)
if np.abs(a).max() > 1.5:
    a = a / 32768.0
n = int(len(a) * 16000 / c.streams.audio[0].rate)
a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)

in_dev = sd.query_devices(kind="input")
# 输出设备优先选和麦克风同一个 USB 设备的那个（耳机放音，耳机麦收音，物理上必然能听到）
out_idx = None
for i, d in enumerate(sd.query_devices()):
    if d["max_output_channels"] > 0 and "USB Audio Device" in d["name"]:
        out_idx = i
        break
print("输入:", in_dev["name"], flush=True)
print("输出:", sd.query_devices(out_idx)["name"] if out_idx is not None else "(系统默认)", flush=True)

print("start:", post("/session/start", {"mode": "ptt"}), flush=True)
time.sleep(0.5)
sd.play(a, 16000, device=out_idx)
sd.wait()
time.sleep(1.0)
print("stop :", post("/session/stop"), flush=True)

last = None
t0 = time.time()
while time.time() - t0 < 90:
    st = get("/session/state")
    key = (st["state"], st.get("detail", "")[:20], round(st.get("level", 0), 3))
    if key != last:
        print("  [%.1fs] %s" % (time.time() - t0, json.dumps(
            {k: st.get(k) for k in ("state", "level", "blocks", "detail")}, ensure_ascii=False)), flush=True)
        last = key
    if st["state"] == "pending":
        break
    if st["state"] == "listening" and st.get("detail", "").startswith(("没听清", "识别失败")):
        break
    time.sleep(0.4)
print("consume:", json.dumps(post("/session/consume"), ensure_ascii=False), flush=True)
post("/session/cancel")
print("done", flush=True)
