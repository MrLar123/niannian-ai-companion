# -*- coding: utf-8 -*-
"""voice_command_router 纯函数单测：意图识别 + 槽位填充 + 负例（绝不能误拦闲聊）。
运行：python tools/test_voice_command_router.py（零依赖，不 import 服务端）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from voice_command_router import parse_command

# 与 xiaoyun_server.py 同构的音色注册表（id 用短代称，label 逐字一致）
VOICE_OPTIONS = [
    {"id": "edge-xiaoxiao", "label": "晓晓 · 温暖御姐"},
    {"id": "edge-xiaoyi", "label": "晓伊 · 活泼少女"},
    {"id": "edge-xiaobei", "label": "晓北 · 东北口音"},
    {"id": "gsv:niannian", "label": "念念 · 本地原声"},
    {"id": "gsv:shouanren", "label": "守岸人 · 清冷御姐"},
    {"id": "volc-vv", "label": "豆包VV · 通用女声"},
    {"id": "volc-yujie", "label": "豆包 · 高冷御姐"},
    {"id": "volc-rou", "label": "豆包 · 柔美女友"},
    {"id": "volc-taiwan", "label": "晓雨 · 台湾少女"},
    {"id": "edge-tw", "label": "晓臻 · 台湾柔嗓"},
]
MOTIONS = [
    ("idle", "内置待机"),
    ("aini", "爱你（49 秒）"),
    ("xiaodangao", "小蛋糕在哪呢（10 秒）"),
    ("tankouwu", "弹跳扣扣舞（8 秒）"),
    ("phut_hon", "Phut hon（5 秒·循环）"),
    ("phut_hon_long", "Phut hon 长版（24 秒）"),
    ("yeyeye", "yeyeye（23 秒）"),
    ("yao", "摇（9 秒）"),
    ("nya_fingers", "手指律动（82 秒·仅手部）"),
]
MODELS = [
    ("huangquan", "黄泉（星穹铁道）"),
    ("kafka", "卡芙卡（星穹铁道）"),
    ("kafka_yoga", "卡芙卡（瑜伽裤）"),
    ("ganyu", "甘雨（原神）"),
    ("feixue", "绯雪（一形态）"),
    ("feixue2", "绯雪（二阶段）"),
    ("peier", "peier"),
    ("rover", "女漂泊者（鸣潮）"),
    ("luotianyi", "洛天依（旗袍）"),
    ("raiden", "雷电将军（原神）"),
    ("elysia", "爱莉希雅（泳装）"),
    ("qianxi", "千咲（鸣潮 1.03）"),
    ("xiaoyu", "涂山小玉（睡裙）"),
]

PASS = FAIL = 0
def check(name, text, want_intent, **want):
    global PASS, FAIL
    got = parse_command(text, VOICE_OPTIONS, MOTIONS, models=MODELS)
    ok = (got or {}).get("intent") == want_intent
    if ok and want:
        for k, v in want.items():
            if got.get(k) != v:
                ok = False
                print("  [%s] %s: %s=%r (期望 %r)" % ("FAIL", name, k, got.get(k), v))
    if ok and want_intent is None:
        ok = got is None
    if ok:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s -> %r" % (name, got))

print("== 换音色 ==")
check("完整label", "把声音换成豆包高冷御姐吧", "switch_voice",
      voice_id="volc-yujie", voice_label="豆包 · 高冷御姐")
check("短名·御姐别名", "换个御姐的声音", "switch_voice", voice_id="volc-yujie")
check("短名·晓晓", "用晓晓的声音说话", "switch_voice", voice_id="edge-xiaoxiao")
check("音色倒装", "音色切成东北的吧", "switch_voice", voice_id="edge-xiaobei")
check("温柔别名", "我想让你温柔一点，换个音色", "switch_voice", voice_id="edge-xiaoxiao")
check("本地原声", "换成念念的本地原声", "switch_voice", voice_id="gsv:niannian")
check("原声别名->通用女声", "换成你的原声吧", "switch_voice", voice_id="volc-vv")
check("清冷御姐直击守岸人", "换成清冷御姐", "switch_voice", voice_id="gsv:shouanren")
check("歧义·台湾->反问", "换成台湾的声音", "ask_voice",
      ambiguous=["晓雨 · 台湾少女", "晓臻 · 台湾柔嗓"])
check("缺槽位->反问", "换个声音嘛", "ask_voice")

print("== 做动作 ==")
check("动作全名带注记", "跳个小蛋糕", "play_motion", motion_key="xiaodangao")
check("动作中文名", "来一段爱你", "play_motion", motion_key="aini")
check("跳个舞缺槽位", "跳个舞呗", "ask_motion")
check("做个动作缺槽位", "给我做个动作看看", "ask_motion")
check("扣扣舞别名", "跳个扣扣舞", "play_motion", motion_key="tankouwu")
check("扑通别名", "来个扑通", "play_motion", motion_key="phut_hon")
check("长版别名", "来个长版", "play_motion", motion_key="phut_hon_long")
check("耶耶耶别名", "跳个耶耶耶", "play_motion", motion_key="yeyeye")
check("摇别名", "来摇一个", "play_motion", motion_key="yao")
check("手指舞别名", "来个手指舞", "play_motion", motion_key="nya_fingers")
check("摇单字无动词不触发", "摇了吧你", None)

print("== 停动作 ==")
check("停下", "停下来休息吧", "stop_motion")
check("别跳了", "别跳了，好累", "stop_motion")

print("== 负例：闲聊绝不能被拦 ==")
check("夸声音", "你的声音真好听", None)
check("换话题", "我们换个话题吧", None)
check("聊动作片", "推荐几个动作片呗", None)
check("问她会不会跳舞", "你会跳舞吗", None)
check("长句闲聊", "我觉得你今天声音听起来格外温柔，是不是心情特别好呀，跟我说说", None)
check("舞蹈话题闲聊", "你最喜欢什么舞蹈", None)
check("动词无槽位闲聊", "你来自哪里", None)
check("空文本", "", None)

print("== 优先级 ==")
check("停>做(别跳了休息)", "别跳了休息一下吧", "stop_motion")

print("== 换人物模型 ==")
check("去括号短名", "换成甘雨吧", "switch_model", model_key="ganyu")
check("变成+全名", "变成黄泉（星穹铁道）", "switch_model", model_key="huangquan")
check("想看+别名佩儿", "我想看看佩儿", "switch_model", model_key="peier")
check("别名漂泊者", "变成漂泊者", "switch_model", model_key="rover")
check("同名歧义->反问", "换成卡芙卡", "ask_model",
      ambiguous=["卡芙卡（星穹铁道）", "卡芙卡（瑜伽裤）"])
check("同名歧义·绯雪", "换成绯雪", "ask_model")
check("缺槽位->反问", "换个模型给我看看", "ask_model")
check("当前形象点名", "变成甘雨（原神）", "switch_model", model_key="ganyu")
check("负例·聊角色", "你演过什么角色", None)
check("负例·人物话题", "这个故事里的人物真有意思", None)
check("音色优先于模型", "用黄泉的声音说话", "ask_voice")

print("== 换模型：ASR 友好别名 + 编号 ==")
check("误听卡夫卡->歧义", "换成卡夫卡", "ask_model",
      ambiguous=["卡芙卡（星穹铁道）", "卡芙卡（瑜伽裤）"])
check("误听飞雪->歧义", "换成飞雪", "ask_model")
check("瑜伽裤直击", "换成瑜伽裤那套", "switch_model", model_key="kafka_yoga")
check("二阶段直击", "变成二阶段", "switch_model", model_key="feixue2")
check("雷神别名", "变成雷神", "switch_model", model_key="raiden")
check("将军别名", "想看将军", "switch_model", model_key="raiden")
check("爱丽丝误听", "换成爱丽丝", "switch_model", model_key="elysia")
check("千岁误听", "变成千岁", "switch_model", model_key="qianxi")
check("小玉别名", "换成小玉", "switch_model", model_key="xiaoyu")
check("阿拉伯编号", "变成4号", "switch_model", model_key="ganyu")
check("中文编号", "变成十二号", "switch_model", model_key="qianxi")
check("编号十", "变成十号", "switch_model", model_key="raiden")
check("编号越界回落", "变成三十号", None)
check("本名爱莉希雅仍可", "换成爱莉希雅", "switch_model", model_key="elysia")
check("负例·将军棋局", "我不用将军这招", None)

print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
