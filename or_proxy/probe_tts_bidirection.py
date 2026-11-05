#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verify the bidirectional streaming TTS really produces audio on the plan gateway.

A 101 handshake only proves auth. This speaks the event protocol:
  1 StartConnection -> 50 ConnectionStarted
  100 StartSession  -> 150 SessionStarted
  200 TaskRequest   -> 350/352(audio)/351
  102 FinishSession -> 152 SessionFinished

Frame = header(4) + event(int32) + sid_len(int32) + sid + payload_len(int32) + payload

Usage:
  python probe_tts_bidirection.py [text] [--out file.mp3]
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import time
import uuid

URL = "wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection"
# 凭证只从环境变量读，绝不写进源码（本脚本会 push 到公开 GitHub）
PLAN_KEY = os.environ.get("ARK_PLAN_API_KEY") or os.environ.get("VOLC_SPEECH_API_KEY") or ""
if not PLAN_KEY:
    sys.exit("[凭证缺失] 请设置 ARK_PLAN_API_KEY 或 VOLC_SPEECH_API_KEY；Key 不写入源码")
VOICE = "zh_female_gaolengyujie_uranus_bigtts"

MT_FULL_CLIENT = 0b0001
MT_AUDIO_ONLY = 0b1011
MT_FULL_SERVER = 0b1001
MT_ERROR = 0b1111
FLAG_EVENT = 0b0100


def hdr(mtype: int, flags: int = FLAG_EVENT) -> bytes:
    return bytes([0x11, (mtype << 4) | flags, 0x10, 0x00])


def build(mtype: int, event: int, payload: dict, session_id: str = "") -> bytes:
    """header(4) + event(int32) + [session_id_len + session_id] + payload_size + payload.

    文档：「session id size / session id 字段，用于描述**会话类事件**的额外信息」。
    - Connect 类（1 建连 / 2 断连）不带该字段
    - Session 类（100 开 / 200 请求 / 101 取消 / 102 结束）**必须带**
    漏掉它时服务端会把 payload_size 误读成 session id 长度，跳过去以后报
    "parse payload size failed: body too short"。
    """
    p = json.dumps(payload, ensure_ascii=False).encode()
    b = hdr(mtype) + struct.pack(">i", event)
    if event >= 100:
        s = (session_id or "").encode()
        b += struct.pack(">I", len(s)) + s
    return b + struct.pack(">I", len(p)) + p


def parse(buf: bytes):
    if len(buf) < 4:
        return None
    mtype = buf[1] >> 4
    flags = buf[1] & 0x0F
    pos = 4
    event = None
    sid = ""
    if flags & FLAG_EVENT:
        if pos + 4 > len(buf):
            return None
        event = struct.unpack(">i", buf[pos:pos + 4])[0]
        pos += 4
        # 下行同理：连接类事件（50/51/52）无 session id，会话类才有
        if event is not None and event >= 100:
            if pos + 4 > len(buf):
                return None
            sl = struct.unpack(">I", buf[pos:pos + 4])[0]
            pos += 4
            if pos + sl > len(buf):
                return None
            sid = buf[pos:pos + sl].decode("utf-8", "replace")
            pos += sl
    if pos + 4 > len(buf):
        return None
    size = struct.unpack(">I", buf[pos:pos + 4])[0]
    pos += 4
    payload = buf[pos:pos + size]
    return {"type": mtype, "event": event, "sid": sid, "payload": payload}


async def run(text: str, out_path: str) -> int:
    import aiohttp
    rid = str(uuid.uuid4())
    headers = {"X-Api-Key": PLAN_KEY, "X-Api-Resource-Id": "seed-tts-2.0",
               "X-Api-Request-Id": rid, "X-Api-Connect-Id": rid}
    audio = bytearray()
    t0 = time.time()
    first_audio_ms = None
    seen = []
    async with aiohttp.ClientSession() as sess:
        async with sess.ws_connect(URL, headers=headers, timeout=30) as ws:
            await ws.send_bytes(build(MT_FULL_CLIENT, 1, {"event": 1}))
            sid = str(uuid.uuid4())
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.BINARY:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print("  ws 关闭: %s" % msg.data)
                        break
                    continue
                f = parse(msg.data)
                if not f:
                    print("  << 未解析 %d bytes  head=%s" % (len(msg.data), msg.data[:24].hex()))
                    continue
                ev = f["event"]
                if ev is not None:
                    seen.append(ev)
                if ev == 50:
                    print("  50 ConnectionStarted")
                    await ws.send_bytes(build(MT_FULL_CLIENT, 100, {
                        "event": 100, "namespace": "BidirectionalTTS",
                        "req_params": {"speaker": VOICE,
                                       "audio_params": {"format": "mp3", "sample_rate": 24000}}}, sid))
                elif ev == 150:
                    print("  150 SessionStarted")
                    await ws.send_bytes(build(MT_FULL_CLIENT, 200, {
                        "event": 200, "namespace": "BidirectionalTTS",
                        "req_params": {"text": text}}, sid))
                    # 文档：没有更多文本可发时要**立即**发 finish session，
                    # 否则服务端一直等文本、不吐音频（实测卡死 40s）
                    await ws.send_bytes(build(MT_FULL_CLIENT, 102, {"event": 102}, sid))
                    print("  200 TaskRequest + 102 FinishSession 已发送")
                elif ev == 350:
                    print("  350 SentenceStart")
                elif ev == 352:
                    n = len(f["payload"])
                    if n:
                        if first_audio_ms is None:
                            first_audio_ms = int((time.time() - t0) * 1000)
                        audio.extend(f["payload"])
                elif ev == 351:
                    print("  351 SentenceEnd  累计音频 %d bytes" % len(audio))
                elif ev in (152, 51, 153):
                    body = f["payload"][:300].decode("utf-8", "replace")
                    print("  %s %s" % (ev, body[:180]))
                    break
                elif f["type"] == MT_ERROR:
                    print("  ERROR %s" % f["payload"][:300].decode("utf-8", "replace"))
                    break
            try:
                await ws.send_bytes(build(MT_FULL_CLIENT, 102, {"event": 102}, sid))
                await ws.send_bytes(build(MT_FULL_CLIENT, 2, {"event": 2}))
            except Exception:                                # noqa: BLE001
                pass

    total_ms = int((time.time() - t0) * 1000)
    print("\n事件序列: %s" % seen)
    print("音频: %d bytes  首包 %sms  总耗时 %sms" % (len(audio), first_audio_ms, total_ms))
    if audio:
        with open(out_path, "wb") as fh:
            fh.write(audio)
        print("已保存: %s" % out_path)
        return 0
    print("未收到音频")
    return 1


async def _main(txt: str, out: str) -> int:
    try:
        return await asyncio.wait_for(run(txt, out), timeout=40)
    except asyncio.TimeoutError:
        print("\n总超时 40s：服务端没有关闭连接（事件流可能已结束但 ws 未断开）")
        return 2


if __name__ == "__main__":
    txt = "你好，我是念念。今天也想和你聊聊天。"
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bidir_test.mp3")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        txt = args[0]
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            out = sys.argv[i + 1]
    sys.exit(asyncio.run(_main(txt, out)))