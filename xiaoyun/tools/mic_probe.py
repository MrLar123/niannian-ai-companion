# -*- coding: utf-8 -*-
"""麦克风探针：列出所有输入设备，并对每个设备录 3 秒，报告电平。
用于判断 ASR 选中的设备是否是"用户在说话的那只麦"。"""
import sys, time
import numpy as np
import sounddevice as sd

print("=" * 70)
print("输入设备清单")
print("=" * 70)
devs = sd.query_devices()
for i, d in enumerate(devs):
    if d["max_input_channels"] > 0:
        mark = ""
        if i == sd.default.device[0]:
            mark = "  <== 系统默认输入"
        print("[%2d] %-58s ch=%d  sr=%.0f%s"
              % (i, d["name"][:58], d["max_input_channels"], d["default_samplerate"], mark))
print()
hostapis = {}
for i, d in enumerate(devs):
    if d["max_input_channels"] > 0:
        hostapis.setdefault(d["hostapi"], []).append(i)
for k, v in hostapis.items():
    try:
        print("hostapi[%d]=%s  设备: %s" % (k, sd.query_hostapis(k)[0]["name"], v))
    except Exception:
        pass

print()
print("=" * 70)
print("电平实测：请对每个设备读秒时【正常音量说话】，看 peak / rms")
print("=" * 70)
REC = 3.0
for i, d in enumerate(devs):
    if d["max_input_channels"] <= 0:
        continue
    for sr in (16000, int(d["default_samplerate"])):
        try:
            data = sd.rec(int(REC * sr), samplerate=sr, channels=1,
                          dtype="float32", device=i, blocking=True)
            break
        except Exception as e:
            data = None
            last = e
    if data is None:
        print("[%2d] %-50s 打开失败: %s" % (i, d["name"][:50], last))
        continue
    a = data[:, 0]
    rms = float(np.sqrt(np.mean(a * a)))
    peak = float(np.max(np.abs(a)))
    # 数字静默检测：连续多少个采样点完全相同
    uniq = len(np.unique(a))
    print("[%2d] %-50s sr=%-6d rms=%.5f peak=%.5f 唯一采样值=%d%s"
          % (i, d["name"][:50], sr, rms, peak, uniq,
             "   <== 数字静默/无声" if peak < 1e-5 else ""))
print()
print("判读：安静环境 rms 应 <0.005；正常说话 rms 应在 0.02~0.15。")
print("若说话时 rms < 0.01，说明该设备增益过低或选错了设备。")
