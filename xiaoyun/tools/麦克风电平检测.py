# -*- coding: utf-8 -*-
"""小芸 · 麦克风电平检测 / 选麦工具

用法：
    python 麦克风电平检测.py                  # 列设备 + 量默认设备 6 秒
    python 麦克风电平检测.py 8                # 量默认设备 8 秒
    python 麦克风电平检测.py 8 USB            # 量名字含 "USB" 的设备 8 秒
    python 麦克风电平检测.py 8 Realtek

量的时候请**对着麦克风正常说一句话**。输出给出每 0.5 秒 RMS、全局峰值，
以及"峰值有没有超过小芸的 VAD 触发阈值（默认 0.0015）"的结论。

换设备：在 Start-Xiaoyun.ps1 的 ASR 环境变量里加（按名字子串匹配，改完重启）
    "XIAOYUN_ASR_INPUT" = "USB Audio Device"
调灵敏度：同上加
    "XIAOYUN_ASR_THR"   = "0.001"     # 越低越灵敏
"""
import sys
import time

import numpy as np
import sounddevice as sd

SR = 16000
BLK = 480
VAD_DEFAULT = 0.0015

secs = 6.0
want = ""
for a in sys.argv[1:]:
    try:
        secs = float(a)
    except ValueError:
        want = a

devs = sd.query_devices()


def can_open(i):
    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                            blocksize=BLK, device=i) as st:
            st.read(BLK)
        return True
    except Exception:
        return False


dev = None
if want:
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0 and want.lower() in d["name"].lower():
            dev = i
            break
    if dev is None:
        print("找不到名字含 %r 的输入设备。" % want)

print("=== 可用输入设备（16k 能否打开）===")
for i, d in enumerate(devs):
    if d["max_input_channels"] > 0:
        mark = "  <== 当前默认" if i == sd.default.device[0] else ""
        print("  %2d  %-46s %-12s%s" % (i, d["name"][:46],
                                        "可录" if can_open(i) else "打不开", mark))
print()

if dev is None:
    dev = sd.default.device[0]
info = sd.query_devices(dev)
print("正在录制 %.1f 秒：%s（现在对着它说话）" % (secs, info["name"]))

peak = 0.0
rows = []
try:
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                        blocksize=BLK, device=dev) as st:
        t0 = time.time()
        last = t0
        while time.time() - t0 < secs:
            x, _ = st.read(BLK)
            r = float(np.sqrt(np.mean(x[:, 0] ** 2)) + 1e-12)
            peak = max(peak, r)
            rows.append(r)
            if time.time() - last >= 0.5:
                last = time.time()
                print("  第 %4.1fs  RMS=%.6f" % (time.time() - t0, r))
except Exception as e:
    raise SystemExit("打不开设备：%s" % e)

a = np.array(rows) if rows else np.array([0.0])
print("---- 汇总 ----")
print("均值=%.6f  P90=%.6f  峰值=%.6f" % (a.mean(), float(np.percentile(a, 90)), peak))
print("小芸 VAD 触发阈值 = %.4f" % VAD_DEFAULT)
if peak >= VAD_DEFAULT * 2:
    print("结论：说话能稳稳触发，VAD 应正常。若仍不回，问题不在麦克风。")
elif peak >= VAD_DEFAULT:
    print("结论：刚够触发，容易漏字；建议把 XIAOYUN_ASR_THR 调到 0.001。")
else:
    print("结论：够不到触发线 —— 抬 Windows 输入音量/开麦克风加强，或改用更灵敏的设备。")
