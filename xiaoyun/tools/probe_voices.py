# -*- coding: utf-8 -*-
"""逐音色实测豆包 2.0（与后端 tts_volc 同一端点/同一个 Key/同一个 Resource-Id）。

判定口径（照抄文档第 8 节）：
  · HTTP 200 但 0 字节 = 音色与资源档不匹配，**不算通过**；
  · 有音频字节 = 通过，并记录首包/总耗时，方便判断哪个音色更跟手。
"""
import base64
import json
import os
import sys
import time
import uuid

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 项目根（tools 的上一级）
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "agentplan"))
import agentplan  # noqa: E402

KEY = agentplan.get_plan_key() or os.environ.get("VOLC_SPEECH_API_KEY") or ""
URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
RES = "seed-tts-2.0"
TEXT = "今天外面有点冷，你出门记得加件外套。"

VOICES = [
    ("zh_female_gaolengyujie_uranus_bigtts", "高冷御姐(已接入)"),
    ("zh_female_vv_uranus_bigtts", "VV 甜美女声(已接入)"),
    ("zh_female_roumeinvyou_uranus_bigtts", "柔美女友(已接入)"),
    ("zh_female_meilinvyou_uranus_bigtts", "美女友(已接入)"),
    ("zh_female_xiaohe_uranus_bigtts", "小何(已接入)"),
    ("zh_female_shuangkuaisisi_uranus_bigtts", "爽快思思(新增)"),
    ("zh_female_kailangjiejie_uranus_bigtts", "开朗姐姐(新增)"),
    ("zh_female_linjianvhai_uranus_bigtts", "邻家女孩(新增)"),
    ("zh_female_tianmeixiaoyuan_uranus_bigtts", "甜美小源(新增)"),
    ("saturn_zh_female_keainvsheng_tob", "可爱女生 saturn(新增)"),
    ("saturn_zh_female_tiaopigongzhu_tob", "调皮公主 saturn(新增)"),
]

OK = {}


def synth(spk, out_path):
    body = {"user": {"uid": "xiaoyun-probe"},
            "req_params": {"text": TEXT, "speaker": spk,
                           "audio_params": {"format": "mp3", "sample_rate": 24000,
                                            "speech_rate": 0, "loudness_rate": 0}}}
    headers = {"Content-Type": "application/json", "X-Api-Key": KEY,
               "X-Api-Resource-Id": RES, "X-Api-Request-Id": str(uuid.uuid4())}
    t0 = time.time()
    ttfb = None
    buf = bytearray()
    with requests.post(URL, json=body, headers=headers, stream=True,
                       timeout=(5, 40), proxies={"http": None, "https": None}) as r:
        if r.status_code != 200:
            return None, "HTTP %d %s" % (r.status_code, (r.text or "")[:120])
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
                return None, "code=%s %s" % (code, (j.get("message") or "")[:120])
            d = j.get("data")
            if d:
                if ttfb is None:
                    ttfb = round(time.time() - t0, 2)
                buf.extend(base64.b64decode(d))
    if not buf:
        return None, "0 字节（音色与资源档不匹配）"
    open(out_path, "wb").write(bytes(buf))
    return {"bytes": len(buf), "ttfb": ttfb, "total": round(time.time() - t0, 2)}, ""


def main():
    out_dir = os.environ.get("VOICE_OUT") or "."
    for spk, cn in VOICES:
        p = os.path.join(out_dir, "voice_%s.mp3" % spk.split("_")[2] if "_" in spk else spk)
        try:
            info, err = synth(spk, p)
        except Exception as e:
            info, err = None, repr(e)
        if info:
            print("%-46s OK   %6dB 首包%ss 总%.2fs  %s" %
                  (spk, info["bytes"], info["ttfb"], info["total"], cn))
            OK[spk] = dict(info, cn=cn)
        else:
            print("%-46s FAIL %s  %s" % (spk, err, cn))
    print("\n通过 %d/%d" % (len(OK), len(VOICES)))
    json.dump(OK, open(os.path.join(out_dir, "voice_probe_result.json"), "w",
                       encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if not KEY:
        print("没有 Plan Key，无法实测")
        sys.exit(2)
    main()
