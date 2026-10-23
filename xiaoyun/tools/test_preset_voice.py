# -*- coding: utf-8 -*-
"""冒烟：音色跟随人设 / 语感签名 / 句库 / 语速偏移 是否都接上了。

不启动 Flask 监听，只 import 模块后直接调内部函数。
输出全部 ASCII，避免 Windows 控制台编码问题。
"""
import os
import sys

os.environ["XIAOYUN_MEMORY"] = "0"
os.environ["XIAOYUN_CONTEXT"] = "0"
os.environ["XIAOYUN_PERCEPTION"] = "0"
os.environ["XIAOYUN_PORT"] = "8199"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))

S = __import__("xiaoyun_server")

fails = []


def chk(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (" | " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


print("---- voice follow default ----")
chk("follow_default_true", S._voice_follow() is True)

print("---- apply preset: dushe (volc_speaker=gaolengyujie) ----")
# 选两套"声线不同"的人设对照：dushe=高冷御姐声线，gaoleng-youhuo=通用VV
ok = S._apply_preset("dushe", persist=False)
chk("apply_dushe", ok)
spk = S.CFG.get("volc_speaker")
chk("speaker_taken_from_preset", spk == "zh_female_gaolengyujie_uranus_bigtts", spk)
chk("catchphrase_loaded", bool(S.CFG.get("preset_catchphrase")))
chk("catchphrase_block", S._catchphrase_block().startswith("\u3010"))
lines = (S.CFG.get("preset_lines") or {}).get("switch") or []
chk("switch_lines", len(lines) >= 3, len(lines))
chk("rate_offset", S.CFG.get("volc_rate_offset") is not None, S.CFG.get("volc_rate_offset"))
chk("speech_rate_clamped", -50 <= int(S.CFG.get("volc_speech_rate") or 0) <= 100,
    S.CFG.get("volc_speech_rate"))

print("---- apply preset: gaoleng-youhuo ----")
ok = S._apply_preset("gaoleng-youhuo", persist=False)
chk("apply_gaoleng", ok)
chk("speaker_switched_back", S.CFG.get("volc_speaker") == "zh_female_vv_uranus_bigtts",
    S.CFG.get("volc_speaker"))

print("---- all 19 presets have catchphrase + lines ----")
missing_cp, missing_ln = [], []
for p in S._list_presets():
    if not p.get("catchphrase"):
        missing_cp.append(p.get("key"))
    if not (p.get("lines") or {}).get("switch"):
        missing_ln.append(p.get("key"))
chk("all_have_catchphrase", not missing_cp, missing_cp)
chk("all_have_switch_lines", not missing_ln, missing_ln)
chk("preset_count", len(S._list_presets()) >= 19, len(S._list_presets()))

print("---- nickname hint ----")
chk("nickname_hint", "NICKNAME_HINT" in dir(S) and len(S.NICKNAME_HINT) > 10)

print("\nRESULT: %s" % ("ALL PASS" if not fails else "FAILED: %s" % fails))
sys.exit(1 if fails else 0)
