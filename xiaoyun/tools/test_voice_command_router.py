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
    ("idle", "内置待机（呼吸 / 歪头 / 挥手）"),
    ("aini", "爱你 · 49 秒"),
    ("xiaodangao", "小蛋糕在哪呢 · 10 秒"),
    ("tankouwu", "弹跳扣扣舞 · 8 秒"),
    ("phut_hon", "Phut hon · 5 秒"),
    ("phut_hon_long", "Phut hon 慢版 · 24 秒"),
    ("yeyeye", "yeyeye · 23 秒"),
    ("yao", "摇 · 9 秒"),
    ("nya_fingers", "手指律动（只动手）· 82 秒"),
]
# 改名后的真实人设清单（label 里带「· 一句话定位」，与线上一致）
PRESETS = [
    ("aojiao", "傲娇 · 口是心非"),
    ("dushe", "毒舌 · 说全是大实话"),
    ("fuhei", "腹黑 · 我记着就好"),
    ("gaoleng-youhuo", "高冷诱惑御姐 × 经典女友感"),
    ("nvwang", "女王 · 再说一遍你做不到"),
    ("sanwu", "三无 · 嗯，知道了"),
    ("tiaopi", "调皮 · 不给～"),
    ("wenrou", "温柔 · 先接住情绪"),
    ("xiaoemo", "小恶魔 · 看你表现"),
    ("yisheng", "医生 · 十一点了，手机放下"),
    ("yonglan", "慵懒 · 再躺五分钟"),
    ("yuanqi", "元气 · 试试看嘛"),
]
MODELS = [
    ("huangquan", "黄泉"),
    ("kafka", "卡芙卡"),
    ("kafka_yoga", "卡芙卡 · 瑜伽裤"),
    ("kafka_vest", "卡芙卡 · 泳装"),
    ("ganyu", "甘雨"),
    ("feixue", "绯雪 · 一形态"),
    ("feixue2", "绯雪 · 二形态"),
    ("peier", "姵儿"),
    ("rover", "女漂泊者"),
    ("luotianyi", "洛天依 · 旗袍"),
    ("raiden", "雷电将军"),
    ("raiden_mdjsn", "雷电将军 · 素体"),
    ("elysia", "爱莉希雅 · 泳装"),
    ("qianxi", "千咲"),
    ("xiaoyu", "涂山小玉 · 睡裙"),
]

PASS = FAIL = 0
def check(name, text, want_intent, **want):
    global PASS, FAIL
    got = parse_command(text, VOICE_OPTIONS, MOTIONS, models=MODELS, presets=PRESETS)
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
# 改名后每个形象都有唯一整名（「卡芙卡」= 基础版，「卡芙卡 · 泳装」= 那套衣服），
# 所以同名前缀不再产生歧义 —— 整名命中优先，说全名就直接切。
check("整名直击基础版", "换成卡芙卡", "switch_model", model_key="kafka")
check("整名直击·带服装", "换成卡芙卡泳装", "switch_model", model_key="kafka_vest")
check("只说衣服名", "换成瑜伽裤", "switch_model", model_key="kafka_yoga")
check("前缀不吞后缀", "换成雷电将军素体", "switch_model", model_key="raiden_mdjsn")
check("前缀本身直击", "换成雷电将军", "switch_model", model_key="raiden")
check("同名前缀·绯雪", "换成绯雪", "ask_model", ambiguous=["绯雪 · 一形态", "绯雪 · 二形态"])
check("缺槽位->反问", "换个模型给我看看", "ask_model")
check("负例·聊角色", "你演过什么角色", None)
check("负例·人物话题", "这个故事里的人物真有意思", None)
check("音色优先于模型", "用黄泉的声音说话", "ask_voice")

print("== 换模型：ASR 友好别名 + 编号 ==")
check("误听卡夫卡->歧义", "换成卡夫卡", "ask_model",
      ambiguous=["卡芙卡", "卡芙卡 · 瑜伽裤"])
check("误听飞雪->歧义", "换成飞雪", "ask_model")
check("瑜伽裤直击", "换成瑜伽裤那套", "switch_model", model_key="kafka_yoga")
check("二阶段直击", "变成二阶段", "switch_model", model_key="feixue2")
check("雷神别名", "变成雷神", "switch_model", model_key="raiden")
check("将军别名", "想看将军", "switch_model", model_key="raiden")
check("爱丽丝误听", "换成爱丽丝", "switch_model", model_key="elysia")
check("千岁误听", "变成千岁", "switch_model", model_key="qianxi")
check("小玉别名", "换成小玉", "switch_model", model_key="xiaoyu")
check("阿拉伯编号", "变成4号", "switch_model", model_key="kafka_vest")
check("中文编号", "变成十二号", "switch_model", model_key="raiden_mdjsn")
check("编号十", "变成十号", "switch_model", model_key="luotianyi")
check("编号越界回落", "变成三十号", None)
check("本名爱莉希雅仍可", "换成爱莉希雅", "switch_model", model_key="elysia")
check("负例·将军棋局", "我不用将军这招", None)

print("== 换性格（人设）==")
check("名词锚点+性格词", "换个傲娇的性格", "switch_preset", preset_key="aojiao")
check("只说性格词", "换成傲娇", "switch_preset", preset_key="aojiao")
check("人设说法", "把人设切成毒舌", "switch_preset", preset_key="dushe")
check("脾气说法", "脾气调成女王那种", "switch_preset", preset_key="nvwang")
check("展示名直击", "换成小天使", "switch_preset", preset_key="xiaotianshi")
check("ASR误听粘人", "换成粘人的性格", "switch_preset", preset_key="nianren")
check("温柔必须带锚点", "换成温柔的性格", "switch_preset", preset_key="wenrou")
check("缺槽位->反问", "换个性格呗", "ask_preset")
check("缺槽位·人设说法", "换个新人设", "ask_preset")
check("负例·问她性格", "你什么性格呀", None)
check("负例·聊脾气", "我最近脾气不太好", None)
check("负例·温柔无锚点仍是音色", "换个温柔的声音", "switch_voice",
      voice_id="edge-xiaoxiao")
check("负例·御姐仍是音色", "换个御姐的声音", "switch_voice",
      voice_id="volc-yujie")
check("负例·长句闲聊", "你今天的性格怎么跟平时完全不一样啊，是不是发生什么事了", None)

print("== ASR 听岔：只报候选反问，绝不猜着执行（Phase 2 补齐） ==")
# 两字名在**执行层**禁用近似（"换成甘雨"曾被听成音色"晓雨"），
# 但不能因此让用户说完一动不动还以为切了 —— 近似只用来反问。
check("两字名听岔->报候选", "换成甘兩", "ask_clarify", kind="model",
      guess=True, ambiguous=["甘雨"])
check("变成两字名听岔", "变成甘兩", "ask_clarify", kind="model",
      guess=True, ambiguous=["甘雨"])
check("近音两字名也只反问", "换成小雨", "ask_clarify", kind="model", guess=True)
check("性格听岔->报候选", "换成傲骄的性格", "ask_preset", guess=True,
      ambiguous=["傲娇 · 口是心非"])
check("三字名听岔仍不猜着切", "换成卡芙咔", "ask_model", guess=True)
check("歧义候选最多三个", "换成绯雪", "ask_model", guess=False,
      ambiguous=["绯雪 · 一形态", "绯雪 · 二形态"])
check("不像名字的话不打扰", "换成现金", None)
check("问句不反问", "你会变成谁呀", None)
check("心情闲聊不误伤", "我今天心情很差", None)

print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
