#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_ark_capabilities.py — 方舟四类能力自检

换 Key、续期、被降权之后跑一遍，把「哪些真能用」的事实重新确认一遍，
而不是相信控制台写着 Published。

    python probe_ark_capabilities.py               # 目录 + 向量 + ASR + TTS 抽样
    python probe_ark_capabilities.py --all          # 再加全部对话模型连通性普查
    python probe_ark_capabilities.py --json out.json

结论口径（都来自 2026-11-05 实测，脚本可复现）：
  · /api/v3/models 是唯一可信的模型目录，返回 domain / modalities / features；
  · 目录里 status=Published 不等于有权限 —— qwen 系 7 个全 404 ModelNotOpen；
  · 文本向量 /embeddings 未开通，只有 /embeddings/multimodal 可用，
    dimensions 只认 1024 或 2048；
  · ASR 没有独立端点，走 chat/completions 的 input_audio；
  · TTS 必须 Plan Key（普通 ARK Key 401），资源只有 seed-tts-2.0，
    音色只有 uranus / saturn 系。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
PLAN_BASE = "https://ark.cn-beijing.volces.com/api/plan/v3"
LEGACY_BASE = "https://ark.cn-beijing.volces.com/api/v3"
TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"

# 凭证只从环境变量 / 命令行读，绝不写进源码（本脚本会 push 到公开 GitHub）
DEFAULT_ARK = os.environ.get("ARK_API_KEY", "")
DEFAULT_PLAN = (os.environ.get("ARK_PLAN_API_KEY")
                or os.environ.get("VOLC_SPEECH_API_KEY", ""))

SPEAKERS = [
    "zh_female_gaolengyujie_uranus_bigtts",
    "zh_female_vv_uranus_bigtts",
    "zh_female_shuangkuaisisi_uranus_bigtts",
    "zh_female_tianmeixiaoyuan_uranus_bigtts",
    "zh_male_yangguangqingnian_uranus_bigtts",
    "saturn_zh_female_keainvsheng_tob",
    # 反例：moon 系在 seed-tts-2.0 上必然失败，保留用来确认资源未变更
    "zh_female_qingxin_moon_bigtts",
]

RESULT = {"ark_models": {}, "chat": [], "embeddings": {}, "asr": {}, "tts": {}, "media": {}}


def _req(url, payload=None, key="", timeout=60, method=None, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    r.add_header("Content-Type", "application/json")
    if key:
        r.add_header("Authorization", "Bearer " + key)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace")), time.time() - t0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        try:
            code = json.loads(body)["error"]["code"]
        except Exception:                                    # noqa: BLE001
            code = ""
        return exc.code, {"raw": body, "code": code}, time.time() - t0
    except Exception as exc:                                 # noqa: BLE001
        return -1, {"raw": repr(exc)[:200]}, time.time() - t0


def probe_catalog(key):
    st, obj, dt = _req(ARK_BASE + "/models", key=key, timeout=40)
    if st != 200:
        print("  目录拉取失败: %s %s" % (st, str(obj)[:160]))
        return []
    data = obj.get("data") or []
    from collections import Counter
    dom = Counter(x.get("domain") or "(空)" for x in data)
    print("  共 %d 个条目" % len(data))
    for k, v in dom.most_common():
        print("    %-18s %d" % (k, v))
    RESULT["ark_models"] = {"total": len(data), "domain": dict(dom)}
    return data


def probe_chat(data, key, only_ok=False):
    cands = [x["id"] for x in data if (x.get("api_supported") or {}).get("chat/completions")]
    if not cands:
        return
    import concurrent.futures as cf

    def ping(mid):
        st, obj, dt = _req(ARK_BASE + "/chat/completions",
                           {"model": mid, "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 4}, key, timeout=90)
        note = ""
        if st != 200:
            note = (obj.get("code") or str(obj.get("raw"))[:40])
        return {"model": mid, "http": st, "ms": round(dt * 1000), "note": note}

    with cf.ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(ping, cands))
    rows.sort(key=lambda r: (r["http"] != 200, r["ms"]))
    ok = [r for r in rows if r["http"] == 200]
    print("  可用 %d / %d" % (len(ok), len(rows)))
    for r in rows:
        if only_ok and r["http"] != 200:
            continue
        print("    %-42s %-4s %6dms %s" % (r["model"], r["http"], r["ms"], r["note"]))
    RESULT["chat"] = rows


def probe_embeddings(key):
    print("  多模态向量：")
    for dims in (1024, 2048, 1536):
        st, obj, dt = _req(ARK_BASE + "/embeddings/multimodal",
                           {"model": "doubao-embedding-vision-251215",
                            "input": [{"type": "text", "text": "小芸在写代码"}],
                            "dimensions": dims}, key)
        if st == 200:
            d = obj["data"]
            print("    dimensions=%-5d → dim=%d  %.0fms  keys=%s"
                  % (dims, len(d.get("embedding") or []), dt * 1000, list(d.keys())))
            RESULT["embeddings"]["dim_%s" % dims] = len(d.get("embedding") or [])
        else:
            print("    dimensions=%-5d → %s（预期：只支持 1024/2048）" % (dims, st))
    st, obj, _ = _req(ARK_BASE + "/embeddings",
                      {"model": "doubao-embedding-vision-251215", "input": ["x"]}, key)
    print("  纯文本走 /embeddings（预期失败）：")
    ok_ids = []
    for mid in ("doubao-embedding-text-240515", "doubao-embedding-text-240715",
                "doubao-embedding-large-text-240915", "doubao-embedding-large-text-250515"):
        s, o, _ = _req(ARK_BASE + "/embeddings", {"model": mid, "input": ["测试"]}, key)
        code = ""
        if isinstance(o, dict):
            code = (o.get("error") or {}).get("code") or ""
        print("    %-40s %-4s %s" % (mid, s, str(code)[:42]))
        if s == 200:
            ok_ids.append(mid)
    if not ok_ids:
        print("    → 控制台可能显示「已开通」，但 REST 侧调不到（本账号实测 48 个候选 ID 全 404）")
    RESULT["embeddings"]["text_ok"] = ok_ids


def probe_asr(ark_key, plan_key, wav_path=None):
    # 测试样本必须用 Plan Key 合成（TTS 归 Plan），识别本身用普通 Key
    pcm = _make_pcm(plan_key, "今天下午三点开会，记得带上笔记本。")
    if not pcm:
        print("  跳过：TTS 未产出音频，无法构造测试样本")
        return
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    wav_bytes = buf.getvalue()
    b64 = base64.b64encode(wav_bytes).decode()
    if wav_path:
        open(wav_path, "wb").write(wav_bytes)

    print("  chat+input_audio（普通 Key）：")
    st, obj, dt = _req(ARK_BASE + "/chat/completions", {
        "model": "doubao-seed-2-0-mini-260428", "max_tokens": 200,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
            {"type": "text", "text": "把这段音频逐字转成文字，只输出文字。"}]}]}, ark_key, timeout=180)
    if st == 200:
        txt = (obj["choices"][0]["message"].get("content") or "").strip()
        print("    %.0fms → %r" % (dt * 1000, txt[:60]))
        RESULT["asr"] = {"chat_http": st, "chat_ms": round(dt * 1000), "text": txt}
    else:
        print("    失败 %s %s" % (st, str(obj)[:120]))
        RESULT["asr"] = {"chat_http": st}

    print("  流式 ASR（WS，Plan Key，resource=volc.seedasr.sauc.duration）：")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "or_proxy_mod", os.path.join(os.path.dirname(os.path.abspath(__file__)), "or_proxy.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        t0 = time.time()
        txt = mod._asr_ws(wav_bytes, 24000)
        dt2 = time.time() - t0
        print("    %.0fms → %r" % (dt2 * 1000, (txt or "")[:60]))
        RESULT["asr"]["ws_ms"] = round(dt2 * 1000)
        RESULT["asr"]["ws_text"] = txt
    except Exception as exc:                                 # noqa: BLE001
        print("    失败 %s" % repr(exc)[:140])
        RESULT["asr"]["ws_error"] = repr(exc)[:200]


def _make_pcm(plan_key, text):
    payload = {"user": {"uid": "probe"},
               "req_params": {"text": text, "speaker": "zh_female_gaolengyujie_uranus_bigtts",
                              "audio_params": {"format": "pcm", "sample_rate": 24000}}}
    st, obj, _ = _tts_call(plan_key, payload)
    return obj if isinstance(obj, bytes) else None


def _tts_call(plan_key, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(TTS_URL, data=data, method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("X-Api-Key", plan_key)
    r.add_header("X-Api-Resource-Id", "seed-tts-2.0")
    r.add_header("X-Api-Request-Id", str(uuid.uuid4()))
    buf = bytearray()
    err = ""
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            for line in resp:
                s = line.decode("utf-8", "ignore").strip()
                if s.startswith("data:"):
                    s = s[5:].strip()
                try:
                    j = json.loads(s)
                except Exception:                            # noqa: BLE001
                    continue
                if isinstance(j.get("data"), str):
                    try:
                        buf.extend(base64.b64decode(j["data"]))
                    except Exception:                        # noqa: BLE001
                        pass
                if j.get("message"):
                    err = str(j["message"])[:80]
    except Exception as exc:                                 # noqa: BLE001
        return -1, repr(exc)[:120], 0
    if not buf:
        return -1, err or "无音频", time.time() - t0
    return 200, bytes(buf), time.time() - t0


def probe_tts(plan_key, ark_key):
    print("  普通 ARK Key 打 TTS（预期 401，说明语音只归 Plan Key）：")
    st, obj, _ = _tts_call(ark_key, {"user": {"uid": "p"}, "req_params": {
        "text": "测试", "speaker": "zh_female_gaolengyujie_uranus_bigtts",
        "audio_params": {"format": "pcm", "sample_rate": 24000}}})
    print("    ARK Key → %s %s" % (st, str(obj)[:60] if st != 200 else "有音频(意外)"))

    print("  Plan Key 逐个音色：")
    ok = []
    for spk in SPEAKERS:
        st, out, dt = _tts_call(plan_key, {"user": {"uid": "p"}, "req_params": {
            "text": "嗯，我知道了。", "speaker": spk,
            "audio_params": {"format": "pcm", "sample_rate": 24000}}})
        good = st == 200
        if good:
            ok.append(spk)
        print("    %-42s %s %7d bytes %.1fs" % (spk, "OK " if good else "XX ",
                                                len(out) if good else 0, dt))
    RESULT["tts"] = {"ok": ok, "ark_key_http": st}


def probe_media(ark_key, plan_key):
    st, obj, dt = _req(PLAN_BASE + "/images/generations",
                       {"model": "doubao-seedream-5-0-flash-260915", "prompt": "一只猫",
                        "size": "1024x1024", "response_format": "url"}, plan_key, timeout=120)
    print("  生图 5.0-flash（Plan）: %s %.1fs %s" % (st, dt, "OK" if st == 200 else str(obj)[:80]))
    RESULT["media"]["image_plan"] = st
    st2, obj2, dt2 = _req(ARK_BASE + "/images/generations",
                          {"model": "doubao-seedream-4-0-20260415", "prompt": "一只猫",
                           "size": "1K", "response_format": "url"}, ark_key, timeout=120)
    print("  生图 4.0（普通 Key）: %s %.1fs %s"
          % (st2, dt2, "OK" if st2 == 200 else str(obj2)[:80]))
    RESULT["media"]["image_ark_4_0"] = st2
    st3, obj3, _ = _req(PLAN_BASE + "/contents/generations/tasks",
                        {"model": "doubao-seedance-2-0-fast-260128",
                         "content": [{"type": "text", "text": "一只猫在走动"}]}, plan_key)
    print("  视频 2.0-fast（Plan）: %s %s" % (st3, (obj3.get("code") or "")[:50]))
    RESULT["media"]["video_plan"] = st3
    st4, obj4, _ = _req(ARK_BASE + "/contents/generations/tasks",
                        {"model": "doubao-seedance-1-0-pro-fast-251015",
                         "content": [{"type": "text", "text": "一只猫在走动"}]}, ark_key)
    print("  视频 1.0-pro-fast（普通 Key）: %s %s" % (st4, (obj4.get("code") or obj4.get("id") or "")[:40]))
    RESULT["media"]["video_ark"] = st4
    # 3D 只能图生：纯文本提交能过 model 校验，但任务会 failed(InvalidParameter)
    for mid in ("doubao-seed3d-2-0-260328", "hyper3d-gen2-260112", "hitem3d-2-0-251223"):
        s, o, _ = _req(ARK_BASE + "/contents/generations/tasks",
                       {"model": mid, "content": [{"type": "text", "text": "一个苹果"}]}, ark_key)
        print("  3D  %-28s 提交=%s %s" % (mid, s, "需 image 输入" if s == 200 else str(o)[:50]))
        RESULT["media"]["3d_" + mid] = s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ark-key", default=os.environ.get("ARK_API_KEY") or DEFAULT_ARK)
    ap.add_argument("--plan-key", default=os.environ.get("ARK_PLAN_API_KEY") or DEFAULT_PLAN)
    ap.add_argument("--all", action="store_true", help="普查全部对话模型（较慢，会消耗少量额度）")
    ap.add_argument("--json", default="", help="把结果写入 JSON")
    args = ap.parse_args()
    for _flag, _val in (("--ark-key / ARK_API_KEY", args.ark_key),
                        ("--plan-key / ARK_PLAN_API_KEY", args.plan_key)):
        if not _val:
            ap.error("缺少凭证：请设置 %s（Key 不写入源码，避免公开仓库泄露）" % _flag)

    print("=" * 78)
    print("[1/6] 模型目录")
    data = probe_catalog(args.ark_key)

    print("=" * 78)
    print("[2/6] 向量")
    probe_embeddings(args.ark_key)

    print("=" * 78)
    print("[3/6] 语音合成（音色池）")
    probe_tts(args.plan_key, args.ark_key)

    print("=" * 78)
    print("[4/6] 语音识别")
    probe_asr(args.ark_key, args.plan_key)

    print("=" * 78)
    print("[5/6] 生图 / 视频 / 3D")
    probe_media(args.ark_key, args.plan_key)

    if args.all and data:
        print("=" * 78)
        print("[6/6] 对话模型连通性普查")
        probe_chat(data, args.ark_key)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(RESULT, fh, ensure_ascii=False, indent=1)
        print("\n结果已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
