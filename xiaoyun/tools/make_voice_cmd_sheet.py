# -*- coding: utf-8 -*-
"""从真实数据生成《语音指令速查表》——不再手写、不会跟代码飘。

为什么做成生成器：
  这份表原来是手抄的，一改音色/形象/动作就过期（"15 个音色"实际 21 个、
  "18 个形象"实际 31 个、名字还是加后缀之前的）。表是给人查"我该怎么说"的，
  说错一个字她就不动，所以表必须和 _VOICE_ALIAS / _MODEL_ALIAS 这些**同一份**数据同源。

数据来源（全部只读，不改任何东西）：
  音色   xiaoyun_server.py 的 VOICE_OPTIONS / GSV_OPTIONS / VOLC_OPTIONS
  动作   assets/motions/*.json（label 即菜单名，group 决定分组）
  形象   assets/models/*/model.json + assets/peier/model.json
  性格   人设/*.json（含 preset 字段的才是人设）
  别名   voice_command_router.py 的 _VOICE_ALIAS / _MOTION_ALIAS / _MODEL_ALIAS / _PRESET_ALIAS
  编号   与服务端同一顺序：_models() 排序后 1 起

用法：
    python tools/make_voice_cmd_sheet.py            # 写 docs/语音指令速查表.md
    python tools/make_voice_cmd_sheet.py --stdout    # 只打印
"""
import glob
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import voice_command_router as R       # noqa: E402  （纯函数模块，可安全 import）

OUT = os.path.join(ROOT, "docs", "语音指令速查表.md")


# ---------------------------------------------------------------- 取数
def load_source_tables():
    """从 xiaoyun_server.py 里**静态**抠出三张音色表（不 import 服务端，避免副作用）。"""
    src = io.open(os.path.join(ROOT, "xiaoyun_server.py"), encoding="utf-8").read()
    out = {}
    for name in ("VOICE_OPTIONS", "GSV_OPTIONS", "VOLC_OPTIONS"):
        m = re.search(name + r"\s*=\s*\[(.*?)\n\]", src, re.S)
        if not m:
            out[name] = []
            continue
        rows = []
        for blk in re.findall(r"\{(.*?)\}", m.group(1), re.S):
            d = {}
            for km in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', blk):
                d[km.group(1)] = km.group(2)
            if d.get("id"):
                rows.append(d)
        out[name] = rows
    return out


def gsv_voices():
    """GSV_OPTIONS 是字典推导式，抠不出来 -> 直接读 GSV_VOICES 每项的 label。"""
    src = io.open(os.path.join(ROOT, "xiaoyun_server.py"), encoding="utf-8").read()
    m = re.search(r"GSV_VOICES\s*=\s*\{(.*?)\n\}", src, re.S)
    if not m:
        return []
    out = []
    for k, lb in re.findall(r'"(\w+)"\s*:\s*\{(.*?)\}', m.group(1), re.S):
        lm = re.search(r'"label"\s*:\s*"([^"]+)"', lb)
        if lm:
            out.append({"id": "gsv:" + k, "engine": "gptsovits", "label": lm.group(1)})
    return out


def voices():
    t = load_source_tables()
    return t["VOLC_OPTIONS"] + gsv_voices() + t["VOICE_OPTIONS"]


def read_jsons(pattern):
    out = []
    for p in sorted(glob.glob(pattern)):
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def motions():
    rows = read_jsons(os.path.join(ROOT, "assets", "motions", "*.json"))
    rows = [r for r in rows if r.get("label")]
    rows.sort(key=lambda r: (r.get("order", 900), r.get("label")))
    return rows


def models():
    rows = read_jsons(os.path.join(ROOT, "assets", "models", "*", "model.json"))
    rows += read_jsons(os.path.join(ROOT, "assets", "peier", "model.json"))
    rows = [r for r in rows if r.get("label")]
    # 与服务端 _discover_models 同序：order 升序，同 order 按 label
    rows.sort(key=lambda r: (r.get("order") if isinstance(r.get("order"), int) else 999,
                             r.get("label") or ""))
    return rows


def presets():
    rows = read_jsons(os.path.join(ROOT, "人设", "*.json"))
    rows = [r for r in rows if r.get("preset")]
    for r in rows:
        r.setdefault("key", "")
    return rows


# ---------------------------------------------------------------- 渲染
def in_alias(table, key):
    """反查：某个 key 有哪些口语别名指向它。"""
    out = []
    for word, val in table.items():
        vals = val if isinstance(val, list) else [val]
        if key in vals:
            out.append(word)
    return out


def short_motion(label):
    """给"跳个 ___"挑一个说得出口的短名。

    展示名可能带「· 时长」和口语助词（"小蛋糕在哪呢"），硬切前四个字会切出
    "跳个小蛋糕在"这种半截话。规则：去时长 → 去尾部助词（呢/在/呀/吧/了）→ 太长就取前 5 字。
    """
    base = R.clean_label(label)
    # 先砍"在哪呢/在吗"这类追问式尾巴，再砍单个语气助词 —— 顺序反了会剩个"在"
    base = re.sub(r"在哪.{0,2}$|在吗$", "", base).strip()
    base = re.sub(r"(呢|在|呀|吧|了|啊|嘛)+$", "", base).strip()
    return base if len(base) <= 6 else base[:6].strip()


def esc(s):
    return (s or "").replace("|", "／")


def build():
    V, M, D, P = voices(), motions(), models(), presets()
    L = []
    A = L.append

    A("# 语音指令速查表")
    A("")
    A("> **本文件由 `tools/make_voice_cmd_sheet.py` 自动生成，不要手改。**")
    A("> 改音色 / 形象 / 动作 / 性格 / 别名后重跑一次，表和代码就不会飘。")
    A("> 全部指令走 L1 规则层（正则 + 子串，<1ms），**没命中就照常聊天**，不影响她正常说话。")
    A("")
    A("## 〇、先读这张表")
    A("")
    A("| 规则 | 说明 |")
    A("|---|---|")
    A("| 句子长度 | **40 字以内**才算指令；长句一律当闲聊 |")
    A("| 动词 + 名字双闸门 | 只有\"换 / 切 / 变成 / 跳 / 来\"**并且**点中名字或编号才执行（\"换成现金\"不会误触） |")
    A("| 优先级 | 停动作 > 性格 > 动作 > 音色 > 形象 |")
    A("| 生僻名说编号 | 形象名读不准就报编号，**数字是语音识别最稳的词** |")
    A("| 一词多解她会问 | 同前缀（绯雪一形态/二形态）她报候选让你点，绝不瞎切 |")
    A("| 听岔了她也会问 | 名字被识别错时她报最像的候选，**不会静默不理、更不会猜着切** |")
    A("| 反问怎么接 | 说「对」/「第一个」/「第二个」，或直接把名字重说一遍 |")
    A("| 她记得你常点的 | 反问报清单时常用的排前面（记在 `data/cmd_prefs.json`，只影响推荐顺序） |")
    A("| 冷却 | 同一音色 6s、同一动作 10s、同一性格 8s、同一形象 15s 内重复说，她只回应不重做 |")
    A("")
    A("### 听岔了 / 说岔了，会怎样")
    A("")
    A("| 你说的 | 她的反应 |")
    A("|---|---|")
    A("| 「换成甘兩」（甘雨听错一个字） | 刚没听清～你说的是「甘雨」吗？说声「对」就行 |")
    A("| 接一句「对」/「嗯」/「是」 | 直接换成甘雨 |")
    A("| 接「第一个」/「第二个」 | 按她报的候选序号选（最多 3 个） |")
    A("| 直接重说「甘雨」 | 也能接上，不必非说「对」 |")
    A("| 接着改主意说「变成雷神」 | 这次反问作废，正常执行新指令 |")
    A("| 「换成现金」/ 闲聊 | 安静当聊天，不多嘴 |")
    A("")
    A("> 反问只挂 45 秒；超时或你说了别的，这次确认自动作废。"
      "候选是**宽松近似**算出来的，只用来报名字给你确认，绝不拿它直接改状态。")
    A("")

    # ---- 性格 ----
    A("## 一、换性格（%d 套人设）" % len(P))
    A("")
    A("> 说法：**「换个 ___ 的性格」/「人设切成 ___」/「脾气调成 ___」**；")
    A("> 与音色不冲突的说法（傲娇/毒舌/腹黑/女王/三无/元气/慵懒/小恶魔/小天使/学姐/医生/邻家/闷骚/黏人/高攻低防/静态美人）**可以省掉\"性格\"两个字**：直接说\"换成傲娇\"。")
    A("> 「温柔」「高冷」这类两边都像的词，**必须带\"性格/人设\"**才按性格处理 —— 否则\"换个温柔的声音\"会变成换人格。")
    A("")
    A("| 性格（菜单名） | 可以这样说 | 自带声线 |")
    A("|---|---|---|")
    for p in P:
        k = p.get("key") or ""
        aliases = in_alias(R._PRESET_ALIAS, k)
        say = "、".join('"%s"' % a for a in aliases[:4]) or "（说展示名）"
        spk = p.get("volc_speaker") or ""
        spk_cn = next((v.get("label") for v in V if v.get("id") == spk), spk or "跟随当前音色")
        A("| %s | %s | %s |" % (esc(p.get("label")), esc(say), esc(spk_cn)))
    A("")
    A("| 其它说法 | 效果 |")
    A("|---|---|")
    A("| \"换个性格呗\" / \"换个人设\" | 她反问前几套让你挑 |")
    A("| 已经是这套性格再点一次 | \"我现在就是「___」呀\"（不重复加载） |")
    A("| \"你什么性格呀\" / \"我最近脾气不太好\" | 不触发指令，正常聊天 |")
    A("")

    # ---- 音色 ----
    A("## 二、换音色（%d 个）" % len(V))
    A("")
    A("> 说法：**「把声音换成 ___」/「用 ___ 的声音」/「音色切成 ___」**，点中名字就生效。")
    A("")
    A("| 音色（菜单名） | 引擎 | 可以这样说 |")
    A("|---|---|---|")
    # VOICE_OPTIONS 里没有 engine 字段（它就是 edge 那张表），默认按 edge 标
    eng_cn = {"": "edge-tts", "edge": "edge-tts", "volc": "豆包 2.0",
              "gptsovits": "本地 GSV"}
    for v in V:
        vid, lb = v.get("id"), v.get("label") or v.get("id")
        alias = in_alias(R._VOICE_ALIAS, lb) or []
        # 别名表里存的是短名片段（如"高冷御姐"），这里反查 label 里的片段
        alias = [w for w in R._VOICE_ALIAS if any(a in lb for a in R._VOICE_ALIAS[w])] or alias
        say = "、".join('"%s"' % a for a in alias[:3]) or "（说名字）"
        A("| %s | %s | %s |" % (esc(lb), eng_cn.get(v.get("engine"), v.get("engine") or "-"), esc(say)))
    A("")
    A("> 本地 GSV 音色第一次切换要拉起本地模型，约 30~60s，她会先提示。")
    A("> 说过一次\"台湾 / 女友 / 少女\"这种多候选词，她会反问二选一。")
    A("")

    # ---- 动作 ----
    A("## 三、做动作（%d 个）" % len(M))
    A("")
    A("> 说法：**「跳/来/做/演 + 动作名」**，例如\"跳个小蛋糕\"\"来段爱你\"。")
    A("> 名字对不上时她会反问\"想看哪个呀\"。停：\"停下来休息\"\"别跳了\"\"回到待机\"。")
    A("")
    A("| 动作（菜单名） | 分组 | 可以这样说 |")
    A("|---|---|---|")
    g_cn = {"idle": "待机 · 小动作", "dance": "舞蹈"}
    for m in M:
        k, lb = m.get("key"), m.get("label") or m.get("key")
        alias = in_alias(R._MOTION_ALIAS, k)
        # 有口语别名就优先给别名（"扑通"比"跳个Phut"像人话）；没有别名才给"跳个 X"模板
        parts = ['"%s"' % a for a in alias[:3]]
        if not parts:
            parts.append('"跳个%s"' % short_motion(lb))
        say = "、".join(parts)
        A("| %s | %s | %s |" % (esc(lb), esc(g_cn.get(m.get("group"), m.get("group") or "-")),
                                esc(say)))
    A("")

    # ---- 形象 ----
    A("## 四、换形象（%d 套）" % len(D))
    A("")
    A("> 说法：**「换成 ___」/「变成 ___」/「想看 ___」**。切换后页面自动重载模型（几秒）。")
    A("> 名字里带「· 服装」的，**说全名直接命中**（\"换成卡芙卡泳装\"）；只说服装（\"换成瑜伽裤\"）也行。")
    A("> 生僻名（庚辰 / 千咲 / 武罗 / 姵儿）建议按编号说。")
    A("")
    A("| 编号 | 形象（菜单名） | 分组 | 可以这样说 |")
    A("|---|---|---|---|")
    g_cn2 = {"daily": "日常", "swim": "泳装", "dress": "礼服·旗袍·居家", "special": "特殊形态"}
    for i, d in enumerate(D, 1):
        k, lb = d.get("key"), d.get("label") or d.get("key")
        alias = in_alias(R._MODEL_ALIAS, k)
        say = "、".join('"%s"' % a for a in alias[:3]) or "—"
        A("| %d号 | %s | %s | %s |" % (i, esc(lb), esc(g_cn2.get(d.get("group"), "-")), esc(say)))
    A("")
    A("> **编号以 `GET /api/models` 的实时顺序为准**（上表就是当前顺序）；模型目录增删后自动顺延，")
    A("> 她要你报编号时的念法与这张表一致。")
    A("")
    A("| 其它说法 | 效果 |")
    A("|---|---|")
    A("| \"换个模型\" / \"换个角色\" | 她反问\"想看我变成谁？\"并报前几个编号 |")
    A("| 已经是这套形象再说一次 | \"我现在就是「___」呀\"（不重载模型） |")
    A("| \"变成三十号\"（越界） | 不触发指令，正常聊天 |")
    A("")

    # ---- 边界 ----
    A("## 五、边界（为什么有些话她不动）")
    A("")
    A("| 设计 | 说明 |")
    A("|---|---|")
    A("| 40 字上限 | 超过就整句落回对话大脑 |")
    A("| 整数名命中优先 | 名字本身就叫这个 = 你说的是它；带后缀的（\"雷电将军 · 素体\"）必须说全 |")
    A("| 间隔号不发音 | \"卡芙卡 · 泳装\"说成\"卡芙卡泳装\"照样命中（归一化时去掉间隔号与标点） |")
    A("| 单字名必须带动词 | \"摇\"单说不动；\"来摇一个\"才播 |")
    A("| 疑问句不动手 | \"你会跳舞吗\"\"你什么性格\"都是聊天 |")
    A("| 同前缀歧义必反问 | 绯雪一形态/二形态 这种不猜 |")
    A("")
    return "\n".join(L) + "\n"


def main():
    txt = build()
    if "--stdout" in sys.argv:
        print(txt)
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    print("已生成 %s（%d 行）" % (OUT, txt.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
