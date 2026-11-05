# -*- coding: utf-8 -*-
"""接线冒烟测试：真实导入 xiaoyun_server，验证 _try_voice_command 的执行链
（路由 -> 执行 -> 确认话术 -> 冷却 -> 未命中回落）。
隔离措施（绝不碰正在运行的实例）：
- 动作持久化 current.txt 指到临时目录；
- _apply_voice 打桩（不写 voice_override.json、不动真实引擎）；
- 记忆/情境/感知全关（XIAOYUN_MEMORY/CONTEXT/PERCEPTION=0），不碰 memory.db；
- XIAOYUN_PORT=18090，app.run 在 __main__ 下不会被触发。
运行：Miniconda3\\python.exe tools/test_voice_command_wiring.py（需 flask/requests）。
"""
import os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

os.environ["XIAOYUN_MEMORY"] = "0"
os.environ["XIAOYUN_CONTEXT"] = "0"
os.environ["XIAOYUN_PERCEPTION"] = "0"
os.environ["XIAOYUN_PORT"] = "18090"

import xiaoyun_server as xs

# ---- 隔离落点 + 打桩 ----
# 只重定向 current.txt（写入目标）；MOTION_DIR/MODEL_DIR 保持真实（_list_motions/_models 只读）
xs.MOTION_STATE = os.path.join(tempfile.mkdtemp(prefix="xytest_motions_"), "current.txt")
xs.MODEL_STATE = os.path.join(tempfile.mkdtemp(prefix="xytest_models_"), "current.txt")
# ★ 人设状态必须一起隔离：换性格的指令会写 人设/current.txt，
#   不隔离的话跑一次测试就把用户当前人设改掉了（"测试顺手改了真机"这类最难查）。
xs.PRESET_STATE = os.path.join(tempfile.mkdtemp(prefix="xytest_presets_"), "current.txt")
# ★ 偏好文件同样要隔离：测试里连点几次"甘雨"不该把真机的推荐顺序改掉
xs.CMD_PREFS_PATH = os.path.join(tempfile.mkdtemp(prefix="xytest_prefs_"), "cmd_prefs.json")
xs.PET_VISIBLE = False            # 走非流式分支，不依赖宠物页在线
applied = []
def _fake_apply_voice(vid, persist=True, log=True):
    applied.append(vid)
    return {"id": vid, "label": "豆包 · 高冷御姐", "engine": "volc"}
xs._apply_voice = _fake_apply_voice

FAILS = []
def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)

# ---- 1. 切音色：解析 -> 执行 -> 确认话术 ----
out = xs._try_voice_command("换个御姐的声音")
check("切音色命中", out is not None)
check("音色id正确", applied == ["zh_female_gaolengyujie_uranus_bigtts"], str(applied))
check("确认话术", out and "高冷御姐" in out[0]["reply"], out and out[0]["reply"])
check("SEQ已推进", out and out[0]["seq"] == xs.SEQ)
check("守卫已上", xs.LAST_REPLY_GUARD > 1.0)
check("PLAYBACK已清零", xs.PLAYBACK["start"] == 0.0)
check("历史已写", xs.HISTORY[-2:][0]["role"] == "user"
      and "御姐" in xs.HISTORY[-1]["content"])

# ---- 2. 冷却：同一音色 6s 内只回应不执行 ----
out2 = xs._try_voice_command("换个御姐的声音")
check("冷却命中", out2 is not None and "刚刚已经弄过" in out2[0]["reply"])
check("冷却不重复执行", applied == ["zh_female_gaolengyujie_uranus_bigtts"])

# ---- 3. 做动作：语音 = 一次性播放（不覆盖菜单的持久选择） ----
open(xs.MOTION_STATE, "w", encoding="utf-8").write("idle")   # 基线：持久选择是待机
out3 = xs._try_voice_command("跳个小蛋糕")
check("动作命中", out3 is not None and "小蛋糕" in out3[0]["reply"])
check("oneshot已置", xs.ONESHOT["key"] == "xiaodangao", str(xs.ONESHOT))
check("oneshot seq 递增", xs.ONESHOT["seq"] > 0)
check("持久选择没被改", open(xs.MOTION_STATE, encoding="utf-8").read() == "idle")
# 冷却期间重复说：只回应不重播（防 VAD 抖动，这是冷却的本职）
seq1 = xs.ONESHOT["seq"]
out3b = xs._try_voice_command("再跳一次小蛋糕")
check("冷却挡住连发", out3b is not None and "刚刚已经弄过" in out3b[0]["reply"]
      and xs.ONESHOT["seq"] == seq1, str(xs.ONESHOT))

# ---- 3b. 页面播完回报 -> oneshot 清掉 ----
with xs.app.test_client() as c:
    j = c.get("/api/motions").get_json()
    check("接口下发 oneshot", j.get("oneshot") == "xiaodangao", str(j.get("oneshot")))
    r = c.post("/api/motion_done", json={"key": "xiaodangao"}).get_json()
    check("回报后清掉", r.get("done") == "xiaodangao" and xs.ONESHOT["key"] == "")
    j2 = c.get("/api/motions").get_json()
    check("清掉后接口为空", j2.get("oneshot") == "")
    # 旧请求不能把新请求清掉
    xs._set_oneshot("yao")
    r2 = c.post("/api/motion_done", json={"key": "xiaodangao"}).get_json()
    check("旧回报被忽略", r2.get("stale") is True and xs.ONESHOT["key"] == "yao")

# 播完清冷却：跳完之后立刻能再跳（"跳完了再来一次"不该被当成抖动）
xs._CMD_COOLDOWN[("play_motion", "yao")] = time.time()
with xs.app.test_client() as c:
    c.post("/api/motion_done", json={})       # 清掉 yao
check("播完清掉该动作冷却", ("play_motion", "yao") not in xs._CMD_COOLDOWN,
      str([k for k in xs._CMD_COOLDOWN if k[1] == "yao"]))
out3c = xs._try_voice_command("来摇一个")
check("跳完能马上再跳", out3c is not None and "刚刚已经弄过" not in out3c[0]["reply"]
      and xs.ONESHOT["key"] == "yao", str(out3c and out3c[0]["reply"]))

# ---- 4. 停动作 -> idle 且顺手清掉未播完的 oneshot ----
out4 = xs._try_voice_command("停下来休息吧")
check("停动作命中", out4 is not None)
check("回到idle", open(xs.MOTION_STATE, encoding="utf-8").read() == "idle")
check("停动作清oneshot", xs.ONESHOT["key"] == "", str(xs.ONESHOT))

# ---- 5. 未命中：返回 None（照常走对话大脑） ----
check("闲聊回落None", xs._try_voice_command("今天天气怎么样") is None)
check("问句回落None", xs._try_voice_command("你会跳舞吗") is None)

# ---- 6. 缺槽位 -> 反问（不执行任何切换） ----
out6 = xs._try_voice_command("跳个舞呗")
check("缺槽位反问", out6 is not None and "想看哪个" in out6[0]["reply"])
check("反问不写状态", open(xs.MOTION_STATE, encoding="utf-8").read() == "idle")
out7 = xs._try_voice_command("换成台湾的声音")
check("歧义反问", out7 is not None and "我这儿有 2 个" in out7[0]["reply"]
      and "台湾柔嗓" in out7[0]["reply"] and "台湾少女" in out7[0]["reply"],
      str(out7 and out7[0]["reply"]))
check("歧义不切音色", applied == ["zh_female_gaolengyujie_uranus_bigtts"])

# ---- 7. 换人物模型（current.txt 为临时目录，真实模型目录只读） ----
out8 = xs._try_voice_command("变成甘雨吧")
check("换模型命中", out8 is not None and "甘雨" in out8[0]["reply"])
check("模型current已写", open(xs.MODEL_STATE, encoding="utf-8").read() == "ganyu")
open(xs.MODEL_STATE, "w", encoding="utf-8").write("huangquan")
out9 = xs._try_voice_command("变成黄泉")
check("当前形象守卫", out9 is not None and "我现在就是" in out9[0]["reply"])
check("守卫不重写状态", open(xs.MODEL_STATE, encoding="utf-8").read() == "huangquan")
out10 = xs._try_voice_command("想看看佩儿")
check("别名佩儿", out10 is not None and open(xs.MODEL_STATE, encoding="utf-8").read() == "peier")
out11 = xs._try_voice_command("换个模型嘛")
check("模型缺槽位反问", out11 is not None and "想看我变成谁" in out11[0]["reply"])
out12 = xs._try_voice_command("换成绯雪")     # 一形态 / 二形态 共用前缀 -> 真歧义
check("模型歧义反问", out12 is not None and "我这儿有 2 个" in out12[0]["reply"]
      and "一形态" in out12[0]["reply"] and "二形态" in out12[0]["reply"],
      str(out12 and out12[0]["reply"]))
out13 = xs._try_voice_command("变成雷神")
check("雷神别名", out13 is not None and open(xs.MODEL_STATE, encoding="utf-8").read() == "raiden")
_lst = xs._models()
open(xs.MODEL_STATE, "w", encoding="utf-8").write("huangquan")
out14 = xs._try_voice_command("变成5号")
check("编号命中(按展示序)", out14 is not None
      and open(xs.MODEL_STATE, encoding="utf-8").read() == _lst[4]["key"],
      "5号=%s" % _lst[4]["key"])
out15 = xs._try_voice_command("变成十一号")
check("中文编号", out15 is not None
      and open(xs.MODEL_STATE, encoding="utf-8").read() == _lst[10]["key"],
      "11号=%s" % _lst[10]["key"])
out16 = xs._try_voice_command("换个模型嘛")
check("反问带编号", out16 is not None and "报编号或说名字" in out16[0]["reply"])
check("闲聊回落None", xs._try_voice_command("今天天气怎么样") is None)

# ---- 8. 换性格（人设）：真跑 _apply_preset，但状态文件指向临时目录 ----
cur_preset = xs.CFG.get("preset_key") or ""
target = "dushe" if cur_preset != "dushe" else "aojiao"
say = "换成毒舌的性格" if target == "dushe" else "换个傲娇的性格"
out17 = xs._try_voice_command(say)
check("换性格命中", out17 is not None, str(out17 and out17[0]["reply"]))
check("人设已生效", xs.CFG.get("preset_key") == target,
      "现在=%s 期望=%s" % (xs.CFG.get("preset_key"), target))
check("人设current已写", open(xs.PRESET_STATE, encoding="utf-8").read() == target)
check("确认话术带名字",
      out17 is not None and (xs.CFG.get("preset_label") or "")[:2] in out17[0]["reply"])
out18 = xs._try_voice_command(say)
# 8 秒冷却会先拦住重复说，所以这里显式清掉冷却，专门验证"已经是这套就别重复加载"那道闸
check("重复说被冷却拦住", out18 is not None and "刚刚已经弄过" in out18[0]["reply"],
      str(out18 and out18[0]["reply"]))
xs._CMD_COOLDOWN.clear()
out18b = xs._try_voice_command(say)
check("同性格再说不重复加载", out18b is not None and "我现在就是" in out18b[0]["reply"],
      str(out18b and out18b[0]["reply"]))
out19 = xs._try_voice_command("换个性格呗")
check("性格缺槽位反问", out19 is not None and "想要哪种性格" in out19[0]["reply"])
check("负例·问她性格", xs._try_voice_command("你什么性格呀") is None)
# 恢复测试前的人设，别把真机留在测试状态
if cur_preset:
    xs._apply_preset(cur_preset, persist=False)

# ---- 9. ASR 听岔 -> 报候选反问 + 确认闭环（Phase 2 补齐） ----
xs._CMD_COOLDOWN.clear()
xs.CLARIFY_PENDING.update(labels=[], kind="", at=0.0)
open(xs.MODEL_STATE, "w", encoding="utf-8").write("huangquan")
voices_before = len(applied)
out20 = xs._try_voice_command("换成甘兩")     # "甘雨"被听错一个字
check("听岔不静默", out20 is not None and "甘雨" in out20[0]["reply"],
      str(out20 and out20[0]["reply"]))
check("听岔不切模型", open(xs.MODEL_STATE, encoding="utf-8").read() == "huangquan")
check("听岔不切音色", len(applied) == voices_before)
check("挂了确认待办", len(xs.CLARIFY_PENDING["labels"]) == 1
      and xs.CLARIFY_PENDING["kind"] == "model")
out21 = xs._try_voice_command("对")
check("说对就执行", out21 is not None and "甘雨" in out21[0]["reply"],
      str(out21 and out21[0]["reply"]))
check("确认后真的切了", open(xs.MODEL_STATE, encoding="utf-8").read() == "ganyu")
check("确认待办已清", xs.CLARIFY_PENDING["labels"] == [])

# 直接把名字重报一遍也要接得上（不必非说"对"）
open(xs.MODEL_STATE, "w", encoding="utf-8").write("huangquan")
xs._CMD_COOLDOWN.clear()
xs._try_voice_command("换成甘兩")
out22 = xs._try_voice_command("甘雨")
check("重报名字也接上", out22 is not None
      and open(xs.MODEL_STATE, encoding="utf-8").read() == "ganyu",
      str(out22 and out22[0]["reply"]))

# 反问待办期间说的是一条新指令：pending 作废，正常解析，别被确认态吞掉
xs._CMD_COOLDOWN.clear()
xs._try_voice_command("换成甘兩")
open(xs.MODEL_STATE, "w", encoding="utf-8").write("huangquan")
out23 = xs._try_voice_command("变成雷神")
check("新指令不被确认态吞掉", out23 is not None
      and open(xs.MODEL_STATE, encoding="utf-8").read() == "raiden",
      str(out23 and out23[0]["reply"]))
check("新指令后待办已作废", xs.CLARIFY_PENDING["labels"] == [])

# ---- 10. 闲聊零误伤 + 偏好记忆（Phase 3） ----
xs.CLARIFY_PENDING.update(labels=[], kind="", at=0.0)
xs._CMD_COOLDOWN.clear()
check("换成现金不打扰", xs._try_voice_command("换成现金") is None)
check("问句不反问", xs._try_voice_command("你会变成谁呀") is None)
check("长句闲聊不打扰", xs._try_voice_command("我今天心情很差想换个心情") is None)
prefs = xs._load_cmd_prefs()
check("偏好已落盘", prefs.get("model", {}).get("ganyu", {}).get("count", 0) >= 1,
      str(prefs.get("model", {}).get("ganyu")))
hint = xs._pref_hint("model", [m["label"] for m in xs._models()], 4)
check("常用项排反问清单前面", bool(hint) and "甘雨" in hint[0], str(hint))
check("反问清单不写死", len(hint) == 4 and all(hint), str(hint))

# ---- 11. 真 HTTP 入口（走 Flask 路由，不直接调函数） ----
xs._CMD_COOLDOWN.clear()
xs.CLARIFY_PENDING.update(labels=[], kind="", at=0.0)
open(xs.MODEL_STATE, "w", encoding="utf-8").write("huangquan")
with xs.app.test_client() as c:
    r1 = c.post("/api/chat", json={"message": "换成甘兩"}).get_json()
    check("HTTP入口·听岔反问", bool(r1) and "甘雨" in (r1.get("reply") or ""),
          str(r1 and r1.get("reply")))
    r2 = c.post("/api/chat", json={"message": "对"}).get_json()
    check("HTTP入口·确认执行", bool(r2) and "甘雨" in (r2.get("reply") or "")
          and open(xs.MODEL_STATE, encoding="utf-8").read() == "ganyu",
          str(r2 and r2.get("reply")))

print("\n结果: %s" % ("全部通过" if not FAILS else "失败: %r" % FAILS))
# 收尾：删掉本次测试建的临时目录，别在 %TEMP% 里越堆越多
import shutil
for _p in (xs.MOTION_STATE, xs.MODEL_STATE, xs.PRESET_STATE, xs.CMD_PREFS_PATH):
    try:
        shutil.rmtree(os.path.dirname(_p))
    except Exception:
        pass
sys.exit(1 if FAILS else 0)
