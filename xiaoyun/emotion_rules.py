"""emotion_rules.py — 情绪判定的纯函数层（无 IO、无第三方依赖、可单测）。

设计依据见 docs/情绪旁路一期实施方案.md §4/§5。三条不能退回去的结论：

  ① 判定输入是「他说的话」，不是她的台词。她的台词常常只有 2~4 字（"哦。""嗯。"），
     文本里没有情绪信号，谁判都只能判 none（实测 18%）。
  ② 标签集 8 个、**没有 neutral**。用"判 none"表达平淡，且 none 的适用范围必须收窄到
     "纯事实陈述/工具性请求"——否则模型会拿 none 当垃圾桶（实测 v2 判据 36% 塌缩）。
  ③ 强度不靠模型输出，靠标点与长度推。让模型多输出一个数字只会重新引入格式问题。

本模块只有纯函数，任何一处改动都应先跑 tools/test_emotion_rules.py。
"""

LABELS = ("happy", "soft", "annoyed", "jealous", "shy", "sad", "tired", "curious")

# 判定 prompt（v3，44 条有期望样本实测 80% 命中 / 9% 塌缩）
# ★ 改这里必须同步 tools/emo_probe.py 的 JUDGE_SYS_V3，否则回归工具的基线与生产不一致。
JUDGE_SYS = (
    "判断她对这句话的情绪反应，只回一个词：" + " ".join(LABELS) + " none。"
    "被夸/被表白/被想念→shy；提到别的异性→jealous；抱怨/被催/被嫌→annoyed；"
    "关心/撒娇/道谢/道晚安/留门→soft；低落/告别/要离开→sad；累/困/熬夜→tired；"
    "好消息/炫耀/分享喜悦→happy；提问/追问/让人猜→curious。"
    "只有当这句话是纯事实陈述或工具性请求（天气、报错、写代码、算术、日程确认）"
    "才回 none；其余情况一律选最接近的一个词，不要因为'不确定'就回 none。"
)

# 判定模型的固定约束：max_tokens 只够一个词，带思维链的模型会把预算全花在
# reasoning_content 上 -> content 为空 -> 解析成 None -> 全判 none，且 HTTP 200 不报错。
JUDGE_MAX_TOKENS = 8
JUDGE_TEMPERATURE = 0.1
JUDGE_THINKING = {"type": "disabled"}

# 表情比她开口早到这个量级最自然（真人听懂了也要 300~800ms 才开口）。
# 双流式 TTS 首包 607ms 会把提前量压到 ~250ms，"脸和嘴同时动"反而假。
EMO_LEAD_MS = 400

# ---------------- 规则层：LLM 超时/失败时的降级 ----------------
# 顺序即优先级：越靠前的越"具体"，同分时先命中者胜（吃醋不能被"关心"抢走）。
# 词表只收高置信、无歧义的口语，宁可判 none 也别乱判——规则层的作用是兜底，不是主判。
_RULES = (
    ("jealous", ("别的女生", "其他女生", "前女友", "女同事", "相亲", "她是谁",
                 "哪个女生", "女生朋友", "跟谁", "谁的微信")),
    ("annoyed", ("烦死", "好烦", "气死", "受够", "讨厌", "闭嘴", "别烦", "啰嗦",
                 "烦不烦", "别念", "又来了")),
    ("tired", ("好累", "累死", "困死", "困了", "熬夜", "通宵", "加班",
               "没睡", "撑不住", "睁不开眼")),
    ("sad", ("难过", "想哭", "不开心", "失落", "孤独", "没人理", "分手",
             "算了", "不说了", "走了", "再见")),
    ("happy", ("升职", "加薪", "中奖", "太好了", "真棒", "好耶", "哈哈哈哈",
               "开心", "成功了", "搞定了", "通过了", "过了")),
    ("shy", ("喜欢你", "爱你", "想你", "好看", "漂亮", "可爱", "抱抱", "亲一个")),
    ("soft", ("晚安", "早点睡", "谢谢", "辛苦", "注意身体", "吃点", "别熬",
              "心疼", "抱抱你", "乖")),
    ("curious", ("为什么", "怎么办", "是什么", "怎么回事", "猜猜", "知不知道",
                 "你觉得")),
)

# 疑问句给 curious / jealous / annoyed 类加一点表演幅度（§4.3）
_QUESTION_LABELS = ("curious", "jealous", "annoyed")

# 语速/音量映射（§5）。两个维度都实测生效：
#   speech_rate  点值 = 语速百分比增量（+60 → 1.60 倍速，1:1）
#   loudness_rate 点值 = 音量百分比增量（-40/+0/+40 → RMS 1457/2354/3337）
# 音量这一维专门用来分开 shy / sad / tired —— 三者表情余弦 > 0.85、语速也都是"变慢"，
# 但"不怎么慢但明显小声"(shy)、"慢而闷"(sad)、"又慢又轻"(tired) 在听感上是三回事。
TTS_TABLE = {
    "happy":   (8, 4),
    "soft":    (-4, -10),
    "annoyed": (5, 6),
    "jealous": (6, 2),
    "shy":     (-6, -14),
    "sad":     (-10, -8),
    "tired":   (-16, -18),
    "curious": (4, 2),
}

_RATE_RANGE = (-50, 100)      # 豆包 speech_rate 合法区间，越界直接报错
_LOUD_RANGE = (-50, 100)


def rule_label(msg):
    """关键词 + 标点兜底判定。返回 (label|None, confidence)。

    confidence 是"规则有多确信"，不是情绪强度；它只用来和 LLM 结果做取舍日志，
    不参与表演幅度（幅度见 intensity_from）。
    """
    text = (msg or "").strip()
    if not text:
        return None, 0.0
    best, best_hits = None, 0
    for label, words in _RULES:
        hits = sum(1 for w in words if w in text)
        if hits > best_hits:
            best, best_hits = label, hits
    if not best:
        return None, 0.0
    # 一个词命中 = 0.45（勉强可用），两个词 0.57 …… 上限 0.75：
    # 规则层永远不该表现得比 LLM 更有把握，否则日志会误导排查。
    return best, min(0.75, 0.45 + 0.12 * (best_hits - 1))


def parse_label(text):
    """从模型输出里抠标签。只认白名单，其他一律 None。

    要兼容的畸形输出（都有单测）：
      "happy" / "happy。" / "情绪：happy" / "她应该会 shy" / "HAPPY"
      "happy 或 shy"（两个标签 = 歧义，丢）/ "hyppy"（拼错，丢）/ "" / None
    """
    if not text:
        return None
    if not isinstance(text, str):
        text = str(text)
    low = text.strip().lower()
    if not low:
        return None
    # 只按 ASCII 字母边界匹配：中文紧贴着也不影响（"她shy了" 要能认出来），
    # 但 "shadow" 里的 "sad" 不能算（后面跟字母）。
    import re
    found = []
    for label in LABELS:
        if re.search(r"(?<![a-z])" + label + r"(?![a-z])", low):
            found.append(label)
    if len(found) == 1:
        return found[0]
    return None        # 0 个 = 无标签/拼错；多个 = 歧义，都不猜


# 兼容方案里的旧名字（文档写的是 parse_judge）
parse_judge = parse_label


def intensity_from(msg, label):
    """按标点与长度推表演幅度（§4.3）。返回 [0.15, 0.9]。

    强度只要求"别每句一个幅度"，不要求准——所以规则简单、可解释、零延迟。
    """
    if not label:
        return 0.0
    text = (msg or "").strip()
    if not text:
        return 0.5
    inten = 0.5
    # 感叹号：每 1 个 +0.15；中文感叹号与半角都算
    bangs = text.count("！") + text.count("!")
    inten += min(0.3, 0.15 * bangs)
    # 连续重复标点（"。。"、"!!"）再补一点，表示"话里有话"
    for seq in ("。。", "！！", "!!", "？？", "??", "……"):
        if seq in text:
            inten += 0.1
            break
    # 句末疑问：只给"会因追问而兴奋/发紧"的标签加，平淡提问不加
    if text.rstrip().endswith(("？", "?")) and label in _QUESTION_LABELS:
        inten += 0.1
    # 话越短情绪越内敛（她的人设），长句则回到基准
    if len(text) < 6:
        inten -= 0.15
    return round(max(0.15, min(0.9, inten)), 3)


def tts_params(label, intensity=0.5):
    """返回 (speech_rate, loudness_rate)。intensity 0.5 = 表值原样。

    k = 0.6 + 0.8 * intensity：0 → ×0.6（不归零，归零就听不出情绪了），1 → ×1.4。
    """
    if not label or label not in TTS_TABLE:
        return 0, 0
    try:
        k = 0.6 + 0.8 * float(intensity)
    except (TypeError, ValueError):
        k = 1.0
    r, l = TTS_TABLE[label]
    rate = int(round(r * k))
    loud = int(round(l * k))
    return (max(_RATE_RANGE[0], min(_RATE_RANGE[1], rate)),
            max(_LOUD_RANGE[0], min(_LOUD_RANGE[1], loud)))


def split_sentences(text):
    """按标点切句，标点跟在前一句尾巴上。返回非空句子列表。

    她的回复常常只有一句（"哦。"），调用方必须能安全地拿到长度 1 的列表。
    """
    text = (text or "").strip()
    if not text:
        return []
    out, buf = [], []
    for ch in text:
        buf.append(ch)
        if ch in "。！？!?…\n":
            seg = "".join(buf).strip()
            if seg:
                out.append(seg)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def sentence_rate(speech_rate, idx, total):
    """逐句韵律：首句满强度，末句收一点尾（-3），中间保持。

    "一句话说完情绪就散了"是最像机器的细节；但单句回复必须原样返回，
    否则会把"哦。"也切出情绪尾巴。
    """
    try:
        speech_rate = int(speech_rate or 0)
        idx, total = int(idx), int(total)
    except (TypeError, ValueError):
        return speech_rate
    if total <= 1 or idx < total - 1:
        return speech_rate
    return max(_RATE_RANGE[0], min(_RATE_RANGE[1], speech_rate - 3))


def judge_messages(msg, context=None):
    """判定请求的 messages。上下文最多一句，且必须带反向指令。

    不要塞她的台词/记忆块/历史：台词是噪声源，记忆块会引入"深夜→tired"这类偏置。
    """
    sysmsg = JUDGE_SYS
    if context:
        sysmsg = sysmsg + "\n情境：" + str(context) + "只参考，不要因此默认 tired。"
    return [{"role": "system", "content": sysmsg},
            {"role": "user", "content": "他：" + (msg or "")}]
