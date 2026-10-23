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
import os, sys, tempfile

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

# ---- 3. 做动作：写 current.txt（临时目录） ----
out3 = xs._try_voice_command("跳个小蛋糕")
check("动作命中", out3 is not None and "小蛋糕" in out3[0]["reply"])
check("current.txt已写", open(xs.MOTION_STATE, encoding="utf-8").read() == "xiaodangao")

# ---- 4. 停动作 -> idle ----
out4 = xs._try_voice_command("停下来休息吧")
check("停动作命中", out4 is not None)
check("回到idle", open(xs.MOTION_STATE, encoding="utf-8").read() == "idle")

# ---- 5. 未命中：返回 None（照常走对话大脑） ----
check("闲聊回落None", xs._try_voice_command("今天天气怎么样") is None)
check("问句回落None", xs._try_voice_command("你会跳舞吗") is None)

# ---- 6. 缺槽位 -> 反问（不执行任何切换） ----
out6 = xs._try_voice_command("跳个舞呗")
check("缺槽位反问", out6 is not None and "想看哪个" in out6[0]["reply"])
check("反问不写状态", open(xs.MOTION_STATE, encoding="utf-8").read() == "idle")
out7 = xs._try_voice_command("换成台湾的声音")
check("歧义反问", out7 is not None and "想让我变成什么声音" in out7[0]["reply"])
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
out12 = xs._try_voice_command("换成卡芙卡")
check("模型歧义反问", out12 is not None and "想看我变成谁" in out12[0]["reply"])
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

print("\n结果: %s" % ("全部通过" if not FAILS else "失败: %r" % FAILS))
sys.exit(1 if FAILS else 0)
