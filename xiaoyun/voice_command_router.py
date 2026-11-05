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
# 明确的换/切动词：只有它出现且不是问句，才允许 ASR 近似与"报候选反问"
_CHANGE_VERB = re.compile(r"换成?|切成?|变成?|调到|调成?|换个?|切个?|改成?|换到|切到")

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
# ---------------- 性格（人设） ----------------
# 与音色/形象的区别靠**名词锚点**：说"性格/人设/人格/脾气/性子"才是换性格。
# 只靠形容词（"换个温柔点的"）会跟音色别名"温柔->温暖御姐"打架，所以：
#   有名词锚点 -> 性格；没名词锚点但只命中性格别名 -> 性格；两者都像 -> 音色（既有行为不变）。
_PRESET_RE = re.compile(
    r"(?:性格|人设|人格|脾气|性子|性格设定|说话的样子|调调|口吻|态度)")
_PRESET_VERB_RE = re.compile(r"换成?|切成?|变成?|调成?|换个?|切个?|来个?|要个?|改成?|切到|换到")
# 口语词 -> 人设 key。value 用列表表示"该说法也能理解成另一套"（歧义 -> 她反问）。
# 别名只收**ASR 能稳定转出、且不含歧义**的词；"高冷"这类同时是音色别名的
# 故意不收，交给名词锚点（"高冷的性格"）走展示名匹配。
_PRESET_ALIAS = {
    "傲娇": "aojiao",
    "毒舌": "dushe",
    "腹黑": "fuhei",
    "高攻低防": "gaogongdifang",
    "静态美人": "jingtai",
    "邻家姐姐": "linjiajiejie",
    "邻家": "linjiajiejie",
    "闷骚": "mensao",
    "黏人": "nianren",
    "粘人": "nianren",          # ASR 常把"黏"转成"粘"
    "醋精": "nianren",
    "女王": "nvwang",
    "三无": "sanwu",
    "调皮": "tiaopi",
    "小恶魔": "xiaoemo",
    "小天使": "xiaotianshi",
    "学姐": "xuejie",
    "医生": "yisheng",
    "慵懒": "yonglan",
    "元气": "yuanqi",
    "温柔": "wenrou",
}

# 与音色别名**不重叠**的性格词：这些词不会让人听成"在说声音"，所以没有名词锚点
# 也能直接认（"换成傲娇"）。"温柔/高冷"这种两边都像的故意排除在集合外，
# 必须说出"性格/人设"才按性格处理 —— 否则上一版"换个温柔的声音"会突然改人格。
_PRESET_SAFE = tuple(w for w in _PRESET_ALIAS if w not in _VOICE_ALIAS)

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
    """把展示名变成「说出来顺口」的样子。

    去掉两类注记：
      "爱你（49 秒）"              -> "爱你"        （括号里的旧式时长）
      "弹跳扣扣舞 · 8 秒"          -> "弹跳扣扣舞"  （本轮改名后的 ★ 分隔时长）
      "灰姑娘 · 2 分 20 秒"        -> "灰姑娘"
    但**保留**有语义的后缀：
      "卡芙卡 · 泳装"              -> "卡芙卡 · 泳装"（"泳装"是这套衣服的名字，删了就分不出人）
    判据是"最后一段是不是以数字开头"——时长一定以数字开头，服装名不会是。
    """
    s = re.sub(r"[（(][^（）()]*[)）]", "", label or "").strip()
    parts = [p.strip() for p in re.split(r"[·・]", s) if p.strip()]
    if len(parts) > 1 and re.match(r"^\d", parts[-1]):
        parts.pop()
    return " · ".join(parts)


def _norm(s):
    """归一化后比较：去掉间隔号、空白与常见标点。

    为什么必须这么做：改名后展示名变成「卡芙卡 · 泳装」，而人说话时不会念那个点
    （"换成卡芙卡泳装"）。不归一化就只能匹配到「卡芙卡」这个更短的串，
    于是"换成卡芙卡泳装"会被判成基础版卡芙卡 —— 换了个错的形象还不报错。
    """
    return re.sub(r"[\s·・,，。、~～\-_/]+", "", s or "").lower()


def _label_cands(label):
    """一个展示名的全部可匹配形态（已归一化）：[(候选, 是否整名)]。

    "卡芙卡 · 泳装" -> [("卡芙卡泳装", True), ("卡芙卡", False), ("泳装", False)]
    拆段是为了让"换成泳装"这种只说衣服的说法也能命中；整名打标记是为了让它优先。
    """
    base = clean_label(label)
    out, seen = [], set()
    full = _norm(base)
    if full:
        out.append((full, True))
        seen.add(full)
    for part in re.split(r"[·・]", base):
        n = _norm(part)
        if n and n not in seen:
            seen.add(n)
            out.append((n, False))
    return out


FULL_BONUS = 40      # 完整名命中的加分：必须大于任何名字的长度，否则"雷电将军"会被"雷电将军·素体"里的同名段抢走


# ---------------- ASR 近似匹配（调研报告风险表第一条） ----------------
# 相邻两个字被听错（"甘雨"->"甘兩"、"卡芙卡"->"卡芙咔"）在 ASR 里非常常见。
# 精确子串匹配对此完全无感：于是用户说"换成甘雨"她一动不动，比切错还让人困惑。
# 这里补一层"整句对名字"的近似比对，只在**动词明确 + 近似度够高 + 第二名明显落后**时
# 才认，否则一律反问 —— 宁可多问一句，不可静默乱切。
FUZZ_MIN = 0.72      # 低于它不认（两字名错一个字就掉到 0.5，所以门槛按名字长度自适应）
FUZZ_GAP = 0.06      # 第一名必须比第二名高这么多，否则视为歧义


def _fuzz_ratio(a, b):
    """两个串的相似度（0~1），用 difflib 的比值。

    ★ 短名字吃亏：两字名错一个字，比值只有 0.5。所以门槛随名字长度放宽：
      两字名允许错一个字（阈值 0.5），三字以上要更像（阈值 0.72）。
    """
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _fuzz_threshold(name):
    """相似度门槛：名字越短越危险，所以两字名干脆不做近似。

    ★ 两字名的坑（实测踩到）：两字名错一个字相似度就是 0.5，而**两个毫不相干的
      两字名只要有一个字相同也是 0.5**（"甘雨" vs "晓雨"）。于是"换成甘雨"会被
      近似成音色"晓雨"，把她的话听成了完全另一件事。这种精度不值得为它冒误伤风险，
      两字名一律不做近似 —— 走精确匹配 + 别名表（别名表本来就是为 ASR 误听准备的）。
    """
    n = len(name or "")
    if n <= 2:
        return 1.01          # >1 = 永不可能达到 -> 两字名不做近似
    if n == 3:
        return 0.62
    return FUZZ_MIN


def _fuzz_slot(text_n, items, cands_of):
    """在归一化文本里找"最像"的那一项，返回 (item, matched_name, ratio) 或 None。

    ★ 只对文本里出现的**片段**做比对，不是整句对整名：说"换成甘兩吧"要能对上"甘雨"，
      整句比会把这个"吧"也算进相似度。做法是按候选名长度滑窗取片段再比。
    """
    best = None
    for it in items:
        for name in cands_of(it):
            # _label_cands 返回 (候选, 是否整名)；这里只关心串本身
            if isinstance(name, tuple):
                name = name[0]
            if len(name) < 2:
                continue
            th = _fuzz_threshold(name)
            L = len(name)
            # 允许用户多说/少说一个字，窗口取 L-1 ~ L+1
            for w in range(max(2, L - 1), L + 2):
                if w > len(text_n):
                    continue
                for i in range(0, len(text_n) - w + 1):
                    seg = text_n[i:i + w]
                    r = _fuzz_ratio(seg, name)
                    if r < th:
                        continue
                    if best is None or r > best[2]:
                        best = (it, name, r)
    return best


def _fuzz_rivals(text_n, items, cands_of, winner, win_ratio, gap=None):
    """近似匹配里"紧咬第一名"的其他候选（空列表 = 可以放心认）。

    ★ 为什么要有这个：把"甘雨"听成"甘兩"时，模型里可能同时存在"甘雨"和"甘雨泳装"
      这种互相很像的名字。不查第二名就替用户挑一个，等于用猜测改了她的装扮。
    """
    gap = FUZZ_GAP if gap is None else gap
    out = []
    for it in items:
        if it is winner:
            continue
        best = 0.0
        for name in cands_of(it):
            if isinstance(name, tuple):
                name = name[0]
            if len(name) < 2:
                continue
            L = len(name)
            for w in range(max(2, L - 1), L + 2):
                if w > len(text_n):
                    continue
                for i in range(0, len(text_n) - w + 1):
                    r = _fuzz_ratio(text_n[i:i + w], name)
                    if r > best:
                        best = r
        if best >= win_ratio - gap:
            out.append(it)
    return out


# ---------------- 反问候选：宽松近似（只报名字，绝不执行） ----------------
# 与执行层近似是两件事：执行层两字名一律不近似（"换成甘雨"曾被听成音色"晓雨"），
# 但若因此让"换成甘兩"直接落闲聊，用户会以为换装成功了 —— 比切错更让人困惑。
# 于是分开：这里允许两字名以 0.5 的低门槛进候选，**只用来反问**；
# 一个候选都找不到就说明用户说的压根不是名字（"换成现金"），安静交还对话。
LOOSE_MIN = 0.5
_LOOSE_STRIP = re.compile(
    r"给我|帮我|一下|想看|换到|切到|换成?|切成?|变成?|调成?|换个?|切个?|改成?|来个?|要个?|"
    r"声音|音色|嗓音|嗓子|声线|性格|人设|人格|脾气|形象|动作|"
    r"舞|吧|呀|啊|嘛|呗|的|了")


def _labels_of(items):
    """把三类清单（dict / (key,label) 元组 / 字符串）统一成展示名列表。"""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            out.append(it.get("label") or it.get("id") or "")
        elif isinstance(it, (tuple, list)):
            out.append(it[1] if len(it) > 1 else str(it[0]))
        else:
            out.append(str(it))
    return [x for x in out if x]


def _clarify_kind(text):
    """没命中时该去哪张表里找候选：按用户话里的名词/动词归类。"""
    if re.search(r"声音|音色|嗓音|嗓子|声线", text):
        return "voice"
    if re.search(r"性格|人设|人格|脾气|性子|调调|口吻|态度", text):
        return "preset"
    if re.search(r"跳|舞|动作|表演", text):
        return "motion"
    return "model"


def loose_candidates(text, labels, n=2):
    """只用于反问的宽松近似：返回最像的 n 个展示名（可能为空）。

    与 _fuzz_slot 的差别：允许两字名、不要求甩开第二名、只取前 n 个。
    错了的代价只是多问一句，不会切错任何状态 —— 这是它敢放宽的全部理由。
    """
    rest = _LOOSE_STRIP.sub("", _norm(text))
    if len(rest) < 2:
        return []
    scored = []
    for lb in _labels_of(labels):
        best = 0.0
        for cand, _full in _label_cands(lb):
            if len(cand) < 2:
                continue
            for w in (len(cand) - 1, len(cand), len(cand) + 1):
                if w < 2 or w > len(rest):
                    continue
                for i in range(0, len(rest) - w + 1):
                    r = _fuzz_ratio(rest[i:i + w], cand)
                    if r > best:
                        best = r
        if best >= LOOSE_MIN:
            scored.append((best, lb))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _r, lb in scored:
        if lb not in out:
            out.append(lb)
        if len(out) >= n:
            break
    return out


def _ask(intent, kind, text, pool, matched, amb=None, n=3, allow_guess=False):
    """反问统一出口：真歧义报原候选；没候选时拿宽松近似补几个（**只展示，不执行**）。

    guess=True 表示这几个是"我猜的"而非"确实有两个同名的"，服务端据此换措辞。
    """
    if amb:
        return {"intent": intent, "kind": kind, "ambiguous": list(amb)[:n],
                "guess": False, "matched": matched}
    d = {"intent": intent, "kind": kind, "matched": matched}
    if allow_guess:
        guess = loose_candidates(text, pool, n=n)
        if guess:
            d["ambiguous"] = guess
            d["guess"] = True
    return d


def _voice_by_norm_name(text_n, voice_options):
    """精确整名/短名命中（先走这条，命中就不必近似了）。"""
    best = None
    for o in voice_options:
        for c, full in _voice_cands(o):
            sc = _hit_score(c, text_n, full)
            if sc and (best is None or sc > best[2]):
                best = (o, c, sc)
    return best


def _voice_cands(option):
    """一个音色的全部可匹配候选（已归一化）：完整名 + 按「·」拆出的短名。
    "豆包 · 高冷御姐" -> ["豆包高冷御姐", "豆包", "高冷御姐"]"""
    return _label_cands(option.get("label") or "")


def _hit_score(cand, text_n, full=False):
    """候选串在归一化文本里的命中分：整名命中给大加分，拆出来的短名只按长度。0 = 未命中。

    为什么必须区分「整名」与「拆段」：
      · 不加分 -> "换成雷电将军素体"只命中短名"雷电将军"，静默换成基础版；
      · 只按长度加分 -> "换成卡芙卡"会同时命中"卡芙卡"（整名）、
        "卡芙卡·泳装"里的"卡芙卡"（拆段）……变成没必要的三选一反问。
    所以整名命中 +FULL_BONUS，拆段命中只算长度：名字本身就叫这个 = 用户说的就是它。
    """
    if len(cand) < 2 or cand not in text_n:
        return 0
    return len(cand) + (FULL_BONUS if full else 0)


def _resolve_voice(text, voice_options, allow_fuzz=False):
    """在文本里找音色槽位。
    返回 (option, ambiguous_options, matched)：三选一非空。

    allow_fuzz=True（只有动词明确的祈使句才为真）时补一层 ASR 近似匹配：
    精确命中不到就找最像的那个，够像才认，含糊就返回歧义让服务端反问。"""
    t = _norm(text)
    best = _voice_by_norm_name(t, voice_options)
    if best:
        return best[0], None, best[1]
    if allow_fuzz:
        # _voice_cands 给的是 (候选, 是否整名)，近似比对只关心串
        hit = _fuzz_slot(t, voice_options,
                         lambda o: [_norm(c) for c, _f in _voice_cands(o)])
        if hit:
            o, name, r = hit
            # 第二名只差一点点 -> 她反问，不猜（"ニア ミス"级别的读音差异本来就不该替用户决定）
            rivals = _fuzz_rivals(t, voice_options,
                                 lambda it: [_norm(c) for c, _f in _voice_cands(it)],
                                 o, r)
            if rivals:
                return None, [o] + rivals, name
            return o, None, name
    for word, parts in _VOICE_ALIAS.items():
        if word in text:
            # _voice_cands 现在是 [(候选, 是否整名)]，别名比对只要候选串
            hits = [o for o in voice_options
                    if any(_norm(part) in [c for c, _f in _voice_cands(o)]
                           for part in parts)]
            if len(hits) == 1:
                return hits[0], None, word
            if len(hits) > 1:
                return None, hits, word
    return None, None, None


def _resolve_motion(text, motions):
    """在文本里找动作槽位，motions = [(key, label), ...]。
    返回 (key, label, matched) 或 None。
    展示名常带注记（"小蛋糕在哪呢 · 10 秒"），口语只说开头（"跳小蛋糕"）——
    精确子串打不中时退到最长公共子串（>=3 字才算命中，两字名必须精确命中）。"""
    t = _norm(text)
    best = None
    for key, label in motions:
        for c in [key, clean_label(label)]:
            if not c:
                continue
            cn = _norm(c)
            score = 0
            if len(cn) >= 2 and cn in t:
                score = len(cn) + (FULL_BONUS if len(cn) >= 4 else 0)
            else:
                sm = difflib.SequenceMatcher(None, t, cn)
                score = sm.find_longest_match(0, len(t), 0, len(cn)).size
                if score < min(3, len(cn)):
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


def _resolve_model(text, models, allow_fuzz=False):
    """在文本里找人物形象槽位，models = [(key, label), ...]（服务端已按展示序排列）。
    返回 {"key","label","matched"} 或 {"amb":[label,...]}（同分多候选歧义）或 None。
    匹配顺序：展示名（含「·」服装名，整名优先）→ 别名表（ASR 友好词，值可为列表表歧义）
              → allow_fuzz 时再做一次 ASR 近似（名字被听错一两个字）。"""
    t = _norm(text)
    byk = {}      # key -> (score, label, matched)
    for key, label in models:
        best = None
        for c, full in [(key, False)] + _label_cands(label):
            sc = _hit_score(_norm(c), t, full)
            if sc and (best is None or sc > best[0]):
                best = (sc, c)
        if best:
            byk[key] = (best[0], label, best[1])
    if not byk and allow_fuzz:
        # 近似匹配只在"一个都没精确命中"时兜底：精确命中永远优先，不会被"更像"抢走
        row_items = [{"key": k, "label": lb} for k, lb in models]
        hit = _fuzz_slot(t, row_items, lambda it: _label_cands(it.get("label") or ""))
        if hit:
            it, name, r = hit
            rivals = _fuzz_rivals(t, row_items,
                                  lambda x: _label_cands(x.get("label") or ""), it, r)
            if rivals:
                return {"amb": [x["label"] for x in [it] + rivals]}
            return {"key": it["key"], "label": it["label"], "matched": name}
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


def _resolve_preset(text, presets, allow_fuzz=False):
    """在文本里找人设槽位，presets = [(key, label), ...]（服务端按展示序）。"""
    t = _norm(text)
    byk = {}
    for key, label in presets:
        best = None
        for c, full in [(key, False)] + _label_cands(label):
            sc = _hit_score(_norm(c), t, full)
            if sc and (best is None or sc > best[0]):
                best = (sc, c)
        if best:
            byk[key] = (best[0], label, best[1])
    if not byk and allow_fuzz:
        row_items = [{"key": k, "label": lb} for k, lb in presets]
        hit = _fuzz_slot(t, row_items, lambda it: _label_cands(it.get("label") or ""))
        if hit:
            it, name, r = hit
            rivals = _fuzz_rivals(t, row_items,
                                  lambda x: _label_cands(x.get("label") or ""), it, r)
            if rivals:
                return {"amb": [x["label"] for x in [it] + rivals]}
            return {"key": it["key"], "label": it["label"], "matched": name}
    for word, keys in _PRESET_ALIAS.items():
        if word not in text:
            continue
        for key in (keys if isinstance(keys, list) else [keys]):
            if key not in byk:
                lb = next((lb for k, lb in presets if k == key), key)
                byk[key] = (len(word) + 2, lb, word)   # +2 让别名压过同长度的展示名片段
    if not byk:
        return None
    top = max(v[0] for v in byk.values())
    winners = [(k, v[1], v[2]) for k, v in byk.items() if v[0] == top]
    if len(winners) > 1:
        return {"amb": [lb for _, lb, _ in winners]}
    k, lb, w = winners[0]
    return {"key": k, "label": lb, "matched": w}


def parse_command(text, voice_options, motions, models=None, presets=None):
    """指令解析主入口（纯函数）。

    参数：
      text          ASR 转写文本（原样，未清洗）
      voice_options 服务端音色注册表（VOICE_OPTIONS + GSV_OPTIONS + VOLC_OPTIONS）
      motions       [(key, label), ...] 动作清单
      models        [(key, label), ...] 人物形象清单（服务端展示序，编号方案用它）
      presets       [(key, label), ...] 人设清单；不传 = 不支持换性格（旧调用方向后兼容）
    返回：
      None                      未命中任何指令意图 -> 调用方照常走对话大脑
      {"intent": "switch_voice", "voice_id", "voice_label", "matched"}
      {"intent": "play_motion",  "motion_key", "motion_label", "matched"}
      {"intent": "stop_motion",  "matched"}
      {"intent": "ask_voice",    "ambiguous"?: [label,...], "matched"}   # 缺槽位/歧义 -> 反问
      {"intent": "ask_motion",   "matched"}                              # 缺槽位 -> 反问
      {"intent": "switch_preset", "preset_key", "preset_label", "matched"}
      {"intent": "ask_preset",   "ambiguous"?: [label,...], "matched"}
      {"intent": "ask_model",    "ambiguous"?: [label,...], "matched"}
      {"intent": "ask_clarify",  "kind", "ambiguous": [label,...], "matched"}  # ASR 听岔了 -> 报候选反问
    """
    text = (text or "").strip()
    if not text or len(text) > 40:      # 指令都是短句，长句一律当对话
        return None
    # 近似匹配/报候选反问的总闸门（调研报告风险表第一条）：
    #   只有"动词明确 + 不像在闲聊/发问"时才允许。闲聊照样走对话 ——
    #   "我今天心情很差"里的"很差"不该被比成某个名字。
    fuzz_ok = bool(_CHANGE_VERB.search(text)) and not _QUESTION_RE.search(text)

    if _STOP_RE.search(text):
        return {"intent": "stop_motion", "matched": _STOP_RE.search(text).group(0)}

    # ---- 性格（人设）----
    # 闸门：嘴里有"性格/人设/人格/脾气"这类名词锚点，或者用的是与音色不重叠的性格词
    # 且带着换/切动词。两个闸门都不满足就完全不碰这一支，聊天与换音色的既有行为不变。
    pp = _PRESET_RE.search(text)
    pv = _PRESET_VERB_RE.search(text)
    if presets and (pp or (pv and any(w in text for w in _PRESET_SAFE))):
        ph = _resolve_preset(text, presets, allow_fuzz=bool(pv) and not _QUESTION_RE.search(text))
        if ph and "key" in ph:
            return {"intent": "switch_preset", "preset_key": ph["key"],
                    "preset_label": ph["label"], "matched": ph["matched"]}
        if ph and "amb" in ph:
            return _ask("ask_preset", "preset", text, presets,
                        pp.group(0) if pp else pv.group(0), amb=ph["amb"])
        # 说了"换个性格"但没点名：有名词锚点才反问（"你什么性格"是聊天，不能反问）
        if pp and pv and not _QUESTION_RE.search(text.replace("什么性格", "")):
            return _ask("ask_preset", "preset", text, presets, pp.group(0),
                        allow_guess=fuzz_ok)

    m, v = _MOTION_RE.search(text), _VOICE_RE.search(text)
    # 组合分支：动词 + 直接点名动作（"跳小蛋糕"）。没点中槽位就当没这意图。
    if not m and _MOTION_VERB_RE.search(text):
        if _resolve_motion(text, motions):
            m = _MOTION_VERB_RE.search(text)
    # 组合分支（音色侧对称）："换成清冷御姐"没有"声音"名词，但有换/切动词
    # 且点中了音色名/别名 —— 槽位是闸门，点不中就不会误伤"换成现金"这类话。
    if not v and _CHANGE_VERB.search(text):
        opt_probe, amb_probe, _w = _resolve_voice(text, voice_options, allow_fuzz=fuzz_ok)
        if opt_probe or amb_probe:
            v = _CHANGE_VERB.search(text)
    # 组合分支（人物模型侧）："换成甘雨/想看佩儿"，同样动词+槽位双闸门
    mm = _MODEL_RE.search(text)
    num_hit = None
    if _MODEL_VERB_RE.search(text):
        # 编号槽位（"变成5号/十二号"）：数字是 ASR 最稳的词
        num_hit = _MODEL_NUM_RE.search(text)
        if not mm and (num_hit or _resolve_model(text, models or [], allow_fuzz=fuzz_ok)):
            mm = _MODEL_VERB_RE.search(text)
    if not m and not v and not mm:
        # 动词明确、非问句，但三张表一个都没对上：名字多半被 ASR 听岔了。
        # 静默落闲聊最糟（用户以为切了），猜着执行更糟 —— 只拿最像的名字反问一句。
        # 连一个像的都没有 = 用户说的根本不是名字（"换成现金"），安静交还对话。
        if fuzz_ok:
            kind = _clarify_kind(text)
            pool = {"voice": voice_options, "model": models or [],
                    "motion": motions, "preset": presets or []}[kind]
            sugg = loose_candidates(text, pool, n=3)
            if sugg:
                return {"intent": "ask_clarify", "kind": kind, "ambiguous": sugg,
                        "guess": True, "matched": _CHANGE_VERB.search(text).group(0)}
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
        return _ask("ask_motion", "motion", text, motions, m.group(0),
                    allow_guess=fuzz_ok)

    if v and (not mm or v.start() <= mm.start()):
        opt, amb, word = _resolve_voice(text, voice_options)
        if opt:
            return {"intent": "switch_voice", "voice_id": opt.get("id"),
                    "voice_label": opt.get("label"), "matched": word}
        if amb:
            return _ask("ask_voice", "voice", text, voice_options, word,
                        amb=[o.get("label") for o in amb])
        if _QUESTION_RE.search(text):
            return None
        return _ask("ask_voice", "voice", text, voice_options, v.group(0),
                    allow_guess=fuzz_ok)

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
            return _ask("ask_model", "model", text, models or [], mm.group(0),
                        amb=hit["amb"])
        if _QUESTION_RE.search(text):
            return None
        return _ask("ask_model", "model", text, models or [], mm.group(0),
                    allow_guess=fuzz_ok)
    return None
