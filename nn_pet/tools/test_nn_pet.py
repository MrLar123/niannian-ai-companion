# -*- coding: utf-8 -*-
"""重构包单元测试（无头、不依赖外部服务）。

覆盖：配置加载 / 舞台指令清洗 / 历史与播放权 / 语音状态机 /
Flask 端点（test_client）。运行：python tools/test_nn_pet.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, os.pardir)))

from nn_pet.pipeline import strip_stage_text  # noqa: E402
from nn_pet.voice import HistoryStore, VoiceSession  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS" if cond else "FAIL"), name, detail)


# ---- 1. 舞台指令清洗 ----
check("strip: 中文括号动作",
      strip_stage_text("（歪头）才不是叫你") == "才不是叫你")
check("strip: 英文括号", strip_stage_text("(笑)你好") == "你好")
check("strip: 星号动作", strip_stage_text("*拍拍你的头*乖") == "乖")
check("strip: 书名号保留", strip_stage_text("放《客官不可以》") == "放《客官不可以》")
check("strip: 全动作返回原文",
      strip_stage_text("（歪头）（笑）") == "（歪头）（笑）")

# ---- 2. HistoryStore ----
hs = HistoryStore(max_items=4)
hs.append_user("a")
hs.append_user("b")
seq1 = hs.append_reply("r1")
check("history: seq 自增", seq1 == 1)
check("history: last(since=1) 空", hs.last(1) is None)
check("history: last(since=0) 有", hs.last(0)["text"] == "r1")
check("history: claim 首次成功", hs.claim_playback(1))
check("history: claim 重复失败", not hs.claim_playback(1))
hs.unclaim(1)
check("history: unclaim 后可再 claim", hs.claim_playback(1))
for i in range(6):
    hs.append_user("u%d" % i)
check("history: 上限裁剪", len(hs.messages()) <= 4)

# ---- 3. VoiceSession ----
vs = VoiceSession()
ok, _ = vs.start()
check("voice: start ok", ok and vs.state == "listening")
ok, reason = vs.start()
check("voice: 重复 start 拒绝", not ok)
vs.set_pending("你好")
check("voice: pending", vs.state == "pending")
vs.set_model()
check("voice: busy 闸", vs.busy and vs.state == "model")
vs.report_playing(3, 5.0)
vs.report_playing(3, 7.0)
check("voice: dur 单调最大", vs.snapshot()["playback_dur"] == 7.0)
vs.report_playing(4, 2.0)
check("voice: 换 seq 重置 start", vs.playback["start"] > 0)
vs.finish_reply(3)
check("voice: finish 清 busy", not vs.busy)
ok, _ = vs.start()
check("voice: finish 后可再 start", ok)

# ---- 4. Flask 端点（test_client，不依赖外部服务）----
tmpdir = tempfile.mkdtemp(prefix="nn_pet_test_")
# 造最小人设目录
import json
preset_dir = os.path.join(tmpdir, "人设")
os.makedirs(preset_dir)
with open(os.path.join(preset_dir, "test.json"), "w", encoding="utf-8") as fh:
    json.dump({"preset": "你是测试人设", "label": "测试"}, fh, ensure_ascii=False)
with open(os.path.join(preset_dir, "current.txt"), "w", encoding="utf-8") as fh:
    fh.write("test")

from nn_pet.server import PetServer  # noqa: E402

srv = PetServer(tmpdir)
c = srv.app.test_client()

r = c.get("/api/health")
check("flask: /api/health", r.status_code == 200 and r.json["ok"] is True)

r = c.get("/api/preset")
check("flask: /api/preset GET", r.status_code == 200
      and r.json["current"] == "test"
      and any(p["key"] == "test" for p in r.json["presets"]))

r = c.post("/api/preset", json={"key": "nope"})
check("flask: /api/preset 未知 key 404", r.status_code == 404)

r = c.post("/api/chat", json={"text": ""})
check("flask: /api/chat 空文本 400", r.status_code == 400)

# brain 不在线 → 502。注意：本机 8787 可能真跑着 or_proxy（返回 200 且真合成），
# 所以这里只断言"请求被完整处理且返回合法 JSON"，不强依赖大脑在线状态。
r = c.post("/api/chat", json={"text": "你好"})
check("flask: /api/chat 处理链完整",
      r.status_code in (200, 502) and "ok" in r.json)

# 用全新 PetServer 实例验证"无新回复"（上面的 chat 测试可能已写入历史）
srv2 = PetServer(tmpdir)
c2 = srv2.app.test_client()
r = c2.get("/api/last?since=0")
check("flask: /api/last 无新回复", r.json["fresh"] is False and r.json["ok"] is True)

seq = srv.history.append_reply("测试回复")
r = c.get("/api/last?since=0")
j = r.json
check("flask: /api/last 有新回复+播放权", j["fresh"] and j["play_here"] is True)
r = c.get("/api/last?since=0")
check("flask: /api/last 播放权独占", r.json["play_here"] is False)

r = c.post("/api/pet_state", json={"visible": False})
check("flask: /api/pet_state", srv.pet_visible is False)

r = c.get("/api/voice/state")
check("flask: /api/voice/state", r.status_code == 200 and r.json["ok"])

r = c.post("/api/voice/playing", json={"seq": 9, "dur": 3.5})
snap = srv.voice.snapshot()
check("flask: /api/voice/playing", snap["playback_dur"] == 3.5)

srv.voice.busy = True
r = c.post("/api/voice/resume", json={"seq": 9})
check("flask: resume busy 委派", r.json.get("delegated") is True)
srv.voice.busy = False

# ---- 结果 ----
print("\n===== %d passed, %d failed =====" % (len(PASS), len(FAIL)))
if FAIL:
    for name, detail in FAIL:
        print("  FAILED:", name, detail)
    sys.exit(1)
