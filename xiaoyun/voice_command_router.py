# -*- coding: utf-8 -*-
"""voice_command_router.py — 语音指令路由（L1 确定性规则层，分层路由 Phase 1）。

职责：在对话大脑之前，用纯规则识别三类指令并填充槽位：
  1. 切音色  "换个御姐的声音"  -> switch_voice(voice_id)
  2. 做动作  "跳个小蛋糕"      -> play_motion(motion_key)
  3. 停动作  "停下来休息"      -> stop_motion(idle)

设计约束（调研与方案见 xiaoyun/docs/语音指令路由调研报告.md）：
- **纯函数、零依赖**：不 import 服务端任何东西，可独立单测（tools/test_voice_command_router.py）。
- **不确定就交还**：说法不含触发词 -> None（正常走对话）；槽位模糊 -> ask_*（服务端让她反问）。
  绝不瞎切。工作态门控、时序铁律都在服务端，与本模块无关。
- 延迟目标 <1ms：全部是正则 + 子串匹配，无 LLM、无网络。

已知取舍（Phase 1 明确不做）：
- 别名匹配是**精确子串**（如"御姐"），不做拼音/同音纠错——ASR 对这些常用词转写稳定；
  未来实测出现系统性误听再按误听样本扩别名表。
- "唱首歌"这类无触发词的自然说法留给 Phase 2 的 LLM 兜底，本层不猜。
"""

import difflib
import re

# ---------------- 意图触发词 ----------------
# 停动作最先判（"别跳了"里也有"跳"，顺序反了会被误判成做动作）
_STOP_RE = re.compile(
    r"(停下|停一停|别跳了|别动了?|休息(一下|一会|吧)|待机|恢复待机|回到待机|站好)")
# 换音色：动词 + 0~6 字 + 声/音色类词，或"音色/声音换成/切成…"
_VOICE_RE = re.compile(
    r"(?:换|切|要|用|变成?|调成?|想要)[^。？?！!]{0,6}?(?:一个)?(?:声音|音色|嗓音|嗓子|原声)"
    r"|(?:声音|音色|嗓音|原声)(?:换成?|切(?:换成?)?|调成?)")
# 做动作：动词 + 量词(可省) + 舞/动作；"跳个舞/来段舞蹈/做个动作"都能命中。
# 动词 + 直接点名动作（"跳小蛋糕"，后面没有"舞/动作"字样）由 parse_command 里
# "动词正则 + 槽位命中"的组合分支兜住 —— 不能写进正则，否则"你来自哪里"也会误命中。
_MOTION_RE = re.compile(
    r"(?:跳|来|做|演|展示|耍)[个一两段下把]{0,2}[^。？?！!]{0,6}?(?:舞|舞蹈|动作)|跳舞|跳支舞")
_MOTION_VERB_RE = re.compile(r"跳|来|做|演|展示|耍|表演")
# 疑问/闲聊语境守卫：这些词出现且槽位没填上时，多半是在"聊"而不是在"命令"
# （"你会跳舞吗" vs "跳个舞呗"）。槽位填上了（点了名）则照常执行。
_QUESTION_RE = re.compile(r"会不会|能不?能|喜欢|想不想|要不要|什么|哪个|怎么样|如何|吗|呢")

# ---------------- 音色别名表 ----------------
# key = 用户口语里的叫法（精确子串命中），value = 候选音色 label 片段。
# value 有多个 = 歧义（如"台湾"有两个台湾音色）-> 服务端让她反问，不瞎切。
_VOICE_ALIAS = {
    "御姐": ["高冷御姐"],          # 只说"御姐"时给最贴人设的豆包高冷御姐
    "高冷": ["高冷御姐"],
    "温柔": ["温暖御姐"],
    "温暖": ["温暖御姐"],
    "东北": ["东北口音"],
    "陕西": ["陕西口音"],
    "台湾": ["台湾柔嗓", "台湾少女"],
    "女友": ["柔美女友", "美女女友"],
    "少女": ["活泼少女", "台湾少女"],
    "萝莉": ["活泼少女"],
    "甜妹": ["美女女友"],
    "原声": ["通用女声"],
    "自然": ["通用女声"],
    "默认": ["通用女声"],
}

# ---------------- 动作别名表 ----------------
# key = 口语叫法（ASR 会稳定转出的词），value = 动作 key。
# 覆盖"展示名语音够不着"的动作：单字名（摇）、英文/拼音名（Phut hon/yeyeye）。
_MOTION_ALIAS = {
    "扣扣舞": "tankouwu",
    "扑通": "phut_hon",
    "长版": "phut_hon_long",
    "耶耶耶": "yeyeye",
    "夜夜夜": "yeyeye",
    "摇": "yao",
    "手指舞": "nya_fingers",
}


# 换人物模型：与音色同构；"模型/人物/角色/形象"作名词锚点
_MODEL_RE = re.compile(
    r"(?:换|切|要|用|变成?|调成?|想要|想看)[^。？?！!]{0,6}?(?:一个)?(?:模型|人物|角色|形象)"
    r"|(?:模型|人物|角色|形象)(?:换成?|切(?:换成?)?|调成?)")
_MODEL_VERB_RE = re.compile(r"换成?|切成?|变成?|调成?|换个?|切个?|来个?|要个?|想看")
# 人物模型别名：ASR 友好化。key = 口语常用词（ASR 稳定转出），value = 动作 key 或
# [key,...]（列表 = 同分多候选 → 她反问二选一，如"卡夫卡/飞雪"这种同 IP 多形态）。
# 设计原则（用户反馈：造型名生僻易被误听）：用日常高频词当别名，生僻本名交给编号方案。
_MODEL_ALIAS = {
    "佩儿": "peier",
    "漂泊者": "rover",
    "天依": "luotianyi",
    "小玉": "xiaoyu",
    "涂山": "xiaoyu",
    "千岁": "qianxi",          # ASR 常把"千咲"转成"千岁"
    "雷神": "raiden",
    "将军": "raiden",
    "爱莉": "elysia",
    "爱丽丝": "elysia",        # ASR 常把"爱莉希雅"转成"爱丽丝"
    "艾莉希雅": "elysia",
    "瑜伽裤": "kafka_yoga",
    "卡夫卡": ["kafka", "kafka_yoga"],   # "卡芙卡"常被转成作家"卡夫卡"
    "飞雪": ["feixue", "feixue2"],       # "绯雪"常被转成"飞雪"
    "一形态": "feixue",
    "一阶段": "feixue",
    "二阶段": "feixue2",
    "二形态": "feixue2",
}
# 编号方案：服务端按 _models() 顺序传入 models 列表，"变成5号/十二号"按序取。
# 数字是 ASR 最稳的词，生僻名（庚辰/千咲/武罗）一律可走编号。
_MODEL_NUM_RE = re.compile(r"(\d{1,2})\s*号|([一二三四五六七八九十]{1,3})号")
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s):
    """中文数字 -> int（支持一~十九，够 16 个形象用）。"""
    if not s:
        return 0
    if s in _CN_NUM:
        return _CN_NUM[s]
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _CN_NUM.get(s[1:], 0)
    m = re.match(r"([一二三四五六七八九])十([一二三四五六七八九]?)", s)
    if m:
        return _CN_NUM[m.group(1)] * 10 + (_CN_NUM.get(m.group(2), 0) if m.group(2) else 0)
    return 0

def clean_label(label):
    """去掉展示名里的时长注记："爱你（49 秒）" -> "爱你"。"""
    return re.sub(r"[（(][^（）()]*[)）]", "", label or "").strip()


def _voice_cands(option):
    """一个音色的全部匹配候选：完整 label + 按"·"拆出的短名。
    "晓晓 · 温暖御姐" -> ["晓晓 · 温暖御姐", "晓晓", "温暖御姐"]"""
    label = option.get("label") or ""
    parts = [p.strip() for p in label.split("·") if p.strip()]
    out, seen = [], set()
    for c in [label] + parts:
        c = clean_label(c)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _resolve_voice(text, voice_options):
    """在文本里找音色槽位。
    返回 (option, ambiguous_options, matched)：三选一非空。"""
    best = None
    for o in voice_options:
        for c in _voice_cands(o):
            if len(c) >= 2 and c in text:
                if best is None or len(c) > best[2]:
                    best = (o, c, len(c))
    if best:
        return best[0], None, best[1]
    for word, parts in _VOICE_ALIAS.items():
        if word in text:
            hits = [o for o in voice_options
                    if any(part in _voice_cands(o) for part in parts)]
            if len(hits) == 1:
                return hits[0], None, word
            if len(hits) > 1:
                return None, hits, word
    return None, None, None


def _resolve_motion(text, motions):
    """在文本里找动作槽位，motions = [(key, label), ...]。
    返回 (key, label, matched) 或 None。
    展示名常带后缀（"小蛋糕在哪呢（10 秒）"），口语只说开头（"跳小蛋糕"）——
    精确子串打不中时退到最长公共子串（>=3 字才算命中，两字名必须精确命中）。"""
    best = None
    for key, label in motions:
        for c in [key, clean_label(label)]:
            if not c:
                continue
            score = 0
            if len(c) >= 2 and c in text:
                score = len(c)
            else:
                sm = difflib.SequenceMatcher(None, text, c)
                score = sm.find_longest_match(0, len(text), 0, len(c)).size
                if score < min(3, len(c)):
                    score = 0
            if score and (best is None or score > best[2]):
                best = (key, label, score, c)
    # 别名（口语叫法 -> 动作 key）。动词分支已要求句中有动词在场，单字别名
    # （"摇"）的误伤面被"来摇一个/摇一个"这类祈使句兜住。
    for word, key in _MOTION_ALIAS.items():
        if word in text:
            label = next((lb for k, lb in motions if k == key), key)
            if best is None or len(word) > best[2]:
                best = (key, label, len(word), word)
    return (best[0], best[1], best[3]) if best else None


def _resolve_model(text, models):
    """在文本里找人物模型槽位，models = [(key, label), ...]（服务端已按展示序排列）。
    返回 {"key","label","matched"} 或 {"amb":[label,...]}（同分多候选歧义）或 None。
    匹配顺序：展示名去括号精确子串 → 别名表（ASR 友好词，值可为列表表歧义）。"""
    byk = {}      # key -> (score, label, matched)
    for key, label in models:
        best = None
        for c in [key, clean_label(label)]:
            if c and len(c) >= 2 and c in text:
                if best is None or len(c) > best[0]:
                    best = (len(c), c)
        if best:
            byk[key] = (best[0], label, best[1])
    for word, keys in _MODEL_ALIAS.items():
        if word not in text:
            continue
        for key in (keys if isinstance(keys, list) else [keys]):
            if key not in byk:
                lb = next((lb for k, lb in models if k == key), key)
                byk[key] = (len(word), lb, word)
    if not byk:
        return None
    top = max(v[0] for v in byk.values())
    winners = [(k, v[1], v[2]) for k, v in byk.items() if v[0] == top]
    if len(winners) > 1:
        return {"amb": [lb for _, lb, _ in winners]}
    k, lb, w = winners[0]
    return {"key": k, "label": lb, "matched": w}


def parse_command(text, voice_options, motions, models=None):
    """指令解析主入口（纯函数）。

    参数：
      text          ASR 转写文本（原样，未清洗）
      voice_options 服务端音色注册表（VOICE_OPTIONS + GSV_OPTIONS + VOLC_OPTIONS）
      motions       [(key, label), ...] 动作清单
    返回：
      None                      未命中任何指令意图 -> 调用方照常走对话大脑
      {"intent": "switch_voice", "voice_id", "voice_label", "matched"}
      {"intent": "play_motion",  "motion_key", "motion_label", "matched"}
      {"intent": "stop_motion",  "matched"}
      {"intent": "ask_voice",    "ambiguous"?: [label,...], "matched"}   # 缺槽位/歧义 -> 反问
      {"intent": "ask_motion",   "matched"}                              # 缺槽位 -> 反问
    """
    text = (text or "").strip()
    if not text or len(text) > 40:      # 指令都是短句，长句一律当对话
        return None

    if _STOP_RE.search(text):
        return {"intent": "stop_motion", "matched": _STOP_RE.search(text).group(0)}

    m, v = _MOTION_RE.search(text), _VOICE_RE.search(text)
    # 组合分支：动词 + 直接点名动作（"跳小蛋糕"）。没点中槽位就当没这意图。
    if not m and _MOTION_VERB_RE.search(text):
        if _resolve_motion(text, motions):
            m = _MOTION_VERB_RE.search(text)
    # 组合分支（音色侧对称）："换成清冷御姐"没有"声音"名词，但有换/切动词
    # 且点中了音色名/别名 —— 槽位是闸门，点不中就不会误伤"换成现金"这类话。
    if not v and re.search(r"换成?|切成?|变成?|调成?|换个?|切个?", text):
        opt_probe, amb_probe, _w = _resolve_voice(text, voice_options)
        if opt_probe or amb_probe:
            v = re.search(r"换成?|切成?|变成?|调成?|换个?|切个?", text)
    # 组合分支（人物模型侧）："换成甘雨/想看佩儿"，同样动词+槽位双闸门
    mm = _MODEL_RE.search(text)
    num_hit = None
    if _MODEL_VERB_RE.search(text):
        # 编号槽位（"变成5号/十二号"）：数字是 ASR 最稳的词
        num_hit = _MODEL_NUM_RE.search(text)
        if not mm and (num_hit or _resolve_model(text, models or [])):
            mm = _MODEL_VERB_RE.search(text)
    if not m and not v and not mm:
        return None

    # 一句话同时含多个意图时，取说得靠前的那半句（"跳个舞…再换个声音"）；
    # Phase 1 单轮只执行一个指令，剩下的自然落在她确认后的下一轮。
    if m and (not v or m.start() <= v.start()) and (not mm or m.start() <= mm.start()):
        hit = _resolve_motion(text, motions)
        if hit:
            return {"intent": "play_motion", "motion_key": hit[0],
                    "motion_label": hit[1], "matched": hit[2]}
        # 缺槽位：疑问/闲聊语境（"你会跳舞吗"）交还对话；祈使（"跳个舞呗"）才反问
        if _QUESTION_RE.search(text):
            return None
        return {"intent": "ask_motion", "matched": m.group(0)}

    if v and (not mm or v.start() <= mm.start()):
        opt, amb, word = _resolve_voice(text, voice_options)
        if opt:
            return {"intent": "switch_voice", "voice_id": opt.get("id"),
                    "voice_label": opt.get("label"), "matched": word}
        if amb:
            return {"intent": "ask_voice",
                    "ambiguous": [o.get("label") for o in amb], "matched": word}
        if _QUESTION_RE.search(text):
            return None
        return {"intent": "ask_voice", "matched": v.group(0)}

    if mm:
        if num_hit:
            # 编号分支：models 顺序即服务端展示顺序（1 起）；越界当没说，交还对话
            n = int(num_hit.group(1)) if num_hit.group(1) else _cn_to_int(num_hit.group(2))
            lst = models or []
            if not (1 <= n <= len(lst)):
                return None
            k, lb = lst[n - 1]
            return {"intent": "switch_model", "model_key": k,
                    "model_label": lb, "matched": num_hit.group(0)}
        hit = _resolve_model(text, models or [])
        if hit and "key" in hit:
            return {"intent": "switch_model", "model_key": hit["key"],
                    "model_label": hit["label"], "matched": hit["matched"]}
        if hit and "amb" in hit:
            return {"intent": "ask_model", "ambiguous": hit["amb"],
                    "matched": mm.group(0)}
        if _QUESTION_RE.search(text):
            return None
        return {"intent": "ask_model", "matched": mm.group(0)}
    return None
