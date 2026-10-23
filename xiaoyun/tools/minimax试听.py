# -*- coding: utf-8 -*-
"""MiniMax 语音合成：探活 + 批量试听生成。
key 从环境变量 MINIMAX_API_KEY 读取（不落盘、不进日志）。
用法：
    set MINIMAX_API_KEY=xxx && python minimax试听.py probe        # 只探活
    set MINIMAX_API_KEY=xxx && python minimax试听.py samples      # 生成候选试听
"""
import json
import os
import sys
import urllib.request

HOSTS = ["https://api.minimaxi.com/v1/t2a_v2", "https://api.minimax.cn/v1/t2a_v2"]
OUT = r"D:\LunaAI\xiaoyun\人设\试听"
TEXT = "这么晚还不睡，过来吧，坐我旁边。哼，谁等你了。汤还在锅里温着，先去洗手。"

CANDIDATES = [
    ("M1_妩媚御姐",      "wumei_yujie"),
    ("M2_御姐精品",      "female-yujie-jingpin"),
    ("M3_傲娇御姐",      "Chinese (Mandarin)_Mature_Woman"),
    ("M4_嘴硬竹马",      "Chinese (Mandarin)_Stubborn_Friend"),
    ("M5_成熟女性",      "female-chengshu-jingpin"),
    ("M6_阅历姐姐",      "Chinese (Mandarin)_Wise_Women"),
]


def call(key, url, voice_id, text, model="speech-2.8-hd", speed=0.95, pitch=-1, emotion=None):
    body = {
        "model": model, "text": text, "stream": False, "language_boost": "Chinese",
        "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1.0, "pitch": pitch},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        "output_format": "hex",
    }
    if emotion:
        body["voice_setting"]["emotion"] = emotion
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                headers={"Authorization": "Bearer " + key,
                                         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def probe(key):
    for url in HOSTS:
        try:
            j = call(key, url, "wumei_yujie", "测试。")
            br = j.get("base_resp") or {}
            code = br.get("status_code")
            audio = (j.get("data") or {}).get("audio") or ""
            print("[%s] status_code=%s msg=%s audio=%d bytes"
                  % (url.split("/")[2], code, br.get("status_msg"), len(audio) // 2))
            if code == 0 and audio:
                return url
        except Exception as e:
            print("[%s] 请求失败: %r" % (url.split("/")[2], e))
    return None


def main():
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        print("缺少环境变量 MINIMAX_API_KEY")
        return 1
    print("key 前缀=%s…  长度=%d" % (key[:7], len(key)))
    good = probe(key)
    if not good:
        print("=> 探活失败：key 无法通过 MiniMax 鉴权（或两个域名都不可用）")
        return 2
    print("=> 探活成功，使用域名 %s" % good.split("/")[2])
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        return 0
    os.makedirs(OUT, exist_ok=True)
    for name, vid in CANDIDATES:
        try:
            j = call(key, good, vid, TEXT)
            br = j.get("base_resp") or {}
            if br.get("status_code") != 0:
                print("[FAIL] %-14s %s: %s" % (name, vid, br.get("status_msg")))
                continue
            raw = bytes.fromhex((j.get("data") or {}).get("audio") or "")
            p = os.path.join(OUT, name + ".mp3")
            with open(p, "wb") as f:
                f.write(raw)
            info = j.get("extra_info") or {}
            print("[OK] %-14s %-38s %6d bytes  %.1fs" % (name, vid, len(raw),
                                                         (info.get("audio_length") or 0) / 1000.0))
        except Exception as e:
            print("[FAIL] %-14s %r" % (name, e))
    return 0


sys.exit(main())
