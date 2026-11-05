#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 assets/emo_slots.json 的实测结果整理成体检报告（Markdown）。

用法：
    python tools/make_emo_report.py                 # 写 docs/PMX表情体检报告.md
    python tools/make_emo_report.py --print         # 只在终端打表

口径：vis = 该表情在脸部小框里造成的**可见度** = 改动像素占比（%） + 峰值分块差 × 0.04 + 平均差 × 1.0。
三项各管一类变化：面积管"大面积强变化"（闭眼笑、睁大眼）；峰值管"细小但局部很强"
（眼睛一眯、眉毛一压）；平均差管"整片均匀的低幅度变化"（脸红染色）。
实测标尺：全闭眼 vis ≈ 1.5~2；>= 0.5 正常可见；0.2~0.5 轻微；< 0.2 基本看不见。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "assets", "emo_slots.json")
OUT = os.path.join(ROOT, "docs", "PMX表情体检报告.md")

LABELS = [("happy", "开心"), ("soft", "温柔"), ("annoyed", "不耐烦"),
          ("jealous", "吃醋"), ("shy", "害羞"), ("sad", "难过"),
          ("tired", "疲惫"), ("curious", "好奇")]
SLOTS = [("smile", "微笑"), ("angry", "生气"), ("sad", "难过"), ("calm", "平和"),
         ("shock", "惊讶"), ("half", "半眯"), ("blush", "脸红")]
GOOD, OK_, BAD = 0.5, 0.2, 0.0
TINT_NAME = "__tint"


def part_name(p):
    """把伪表情名换成给人看的说法。"""
    n = p.get("name", "?")
    if n == TINT_NAME:
        return "兜底染色"
    return n


def mark(v):
    if v is None:
        return "—"
    if v >= GOOD:
        return "OK"
    if v >= OK_:
        return "轻"
    return "无"


def main():
    if not os.path.exists(CFG):
        print("没有 %s，先跑 tools/pmx_emo_lab.mjs calib" % CFG)
        return 1
    d = json.load(open(CFG, encoding="utf-8"))
    models = [k for k in d if not k.startswith("_")]
    rows = []
    for k in models:
        m = d[k]
        diag = m.get("_diag") or {}
        labels = diag.get("labels") or {}
        slots = m.get("slots") or {}
        idle = m.get("idle") or {}
        rows.append(dict(key=k, labels=labels, slots=slots, idle=idle,
                         meshes=diag.get("meshes"), morphs=diag.get("morphs"),
                         area=diag.get("labelsArea") or {},
                         peak=diag.get("labelsPeak") or {},
                         scores=diag.get("scores") or {}))

    # ---- 概览 ----
    print("%-16s %s" % ("模型", " ".join("%-6s" % cn for _, cn in LABELS)))
    for r in rows:
        line = " ".join("%-6s" % mark(r["labels"].get(lb)) for lb, _ in LABELS)
        print("%-16s %s" % (r["key"], line))

    tally = {lb: {"OK": 0, "轻": 0, "无": 0} for lb, _ in LABELS}
    for r in rows:
        for lb, _ in LABELS:
            tally[lb][mark(r["labels"].get(lb))] += 1
    print()
    for lb, cn in LABELS:
        t = tally[lb]
        print("%-8s %-6s OK=%d 轻=%d 无=%d" % (lb, cn, t["OK"], t["轻"], t["无"]))

    if "--print" in sys.argv:
        return 0

    # ---- 文档 ----
    L = []
    L.append("# PMX 形象表情体检报告\n")
    L.append("> 由 `tools/pmx_emo_lab.mjs calib` 实测生成，重标定后本文件可重新生成。\n")
    L.append("## 一、口径\n")
    L.append("桌宠的表情有没有「看得见」，用一个量：**可见度 vis**。")
    L.append("做法是把该表情单独摆到模型脸上，跟中性脸逐像素比，再合成三项：")
    L.append("")
    L.append("```")
    L.append("vis = 改动像素占比(%)  +  峰值16×16分块平均差 × 0.04  +  平均差 × 1.0")
    L.append("```")
    L.append("")
    L.append("| 分数区间 | 观感 |\n|---|---|")
    L.append("| ≥ 2.0 | 很明显 |")
    L.append("| 1.0 ~ 2.0 | 明显（参考：全闭眼 ≈ 1.5~2） |")
    L.append("| 0.5 ~ 1.0 | 清楚可见 |")
    L.append("| 0.2 ~ 0.5 | 轻微，凑近能看出来 |")
    L.append("| < 0.2 | 基本看不见 |\n")
    L.append("三项各管一类变化，缺一不可：面积管「大面积强变化」（闭眼笑、睁大眼）；"
             "峰值管「细小但局部很强」（眼睛一眯、眉毛一压）；"
             "平均差管「整片均匀的低幅度变化」——脸红染色就是这一类，"
             "面积可能为 0 但整张脸都轻微偏红，只看面积会误判成没效果。")
    L.append("不掺姿态（歪头/低头）——姿态在任何模型上都明显，会把「表情几乎没动」掩盖掉。")

    L.append("## 二、总览（%d 个模型 × 8 个情绪）\n" % len(rows))
    L.append("| 模型 | " + " | ".join(cn for _, cn in LABELS) + " | 常驻微笑 |")
    L.append("|" + "---|" * (len(LABELS) + 2))
    for r in rows:
        cells = []
        for lb, _ in LABELS:
            v = r["labels"].get(lb)
            cells.append("—" if v is None else "%.2f" % v)
        idle = r["idle"] or {}
        it = "%s@%.2f" % (idle.get("name", "—"), idle.get("w", 0)) if idle else "—"
        L.append("| `%s` | %s | %s |" % (r["key"], " | ".join(cells), it))
    L.append("")
    L.append("统计：\n")
    L.append("| 标签 | 可见(≥0.5) | 轻微 | 看不见 |\n|---|---|---|---|")
    for lb, cn in LABELS:
        t = tally[lb]
        L.append("| %s | %d | %d | %d |" % (cn, t["OK"], t["轻"], t["无"]))
    L.append("")

    # 面积小但局部很强：这类靠"面积"会误判成没表情，必须单独列出来
    narrow = []
    for r in rows:
        for lb, cn in LABELS:
            a = (r["area"] or {}).get(lb)
            v = r["labels"].get(lb)
            pk = (r["peak"] or {}).get(lb)
            if a is None or v is None:
                continue
            if a < 0.5 and v >= 0.5:
                narrow.append((r["key"], cn, a, pk, v))
    if narrow:
        L.append("### 面积小但局部清晰的表情\n")
        L.append("这些表情只改很小一块（面积不足 0.5%），但局部对比度足够强，肉眼是看得见的 ——")
        L.append("早期只用面积口径时它们会被误判成「没效果」，是这一轮修掉的口径缺陷。\n")
        L.append("| 模型 | 情绪 | 改动面积 | 峰值分块差 | 可见度 |\n|---|---|---|---|---|")
        for k, cn, a, pk, v in narrow:
            L.append("| `%s` | %s | %.2f%% | %s | %.2f |" % (k, cn, a, pk, v))
        L.append("")

    L.append("## 三、每个模型选中的表情\n")
    L.append("槽位格式 `表情名@放大系数`：系数 >1 表示该表情本身幅度偏小，配方权重会按比例顶上去。\n")
    L.append("| 模型 | " + " | ".join(cn for _, cn in SLOTS) + " |")
    L.append("|" + "---|" * (len(SLOTS) + 1))
    for r in rows:
        cells = []
        for s, _ in SLOTS:
            parts = r["slots"].get(s) or []
            if not parts:
                cells.append("缺")
            else:
                cells.append("+".join("%s@%s" % (part_name(p), p.get("w", 1)) for p in parts))
        L.append("| `%s` | %s |" % (r["key"], " | ".join(cells)))
    L.append("")

    # ---- 缺口 ----
    miss = {s: [r["key"] for r in rows if not (r["slots"].get(s) or [])] for s, _ in SLOTS}
    tint_used = [r["key"] for r in rows
                 if any((p.get("name") == TINT_NAME) for p in (r["slots"].get("blush") or []))]
    L.append("## 四、模型本身的缺口\n")
    L.append("以下是**模型没给这类表情**（或给了但幅度小到测不出来），代码无法凭空造：\n")
    for s, cn in SLOTS:
        lst = miss[s]
        L.append("- **%s**：%s" % (cn, "、".join("`%s`" % k for k in lst) if lst else "无缺口"))
    L.append("")
    L.append("「脸红」不再有无缺口之外的空白：模型自带的照れ测不出来时，自动改用"
             "**兜底染色**（把脸材质往粉红方向压），本轮有 %d 个模型走这条路：%s。"
             % (len(tint_used), "、".join("`%s`" % k for k in tint_used) if tint_used else "无"))
    L.append("")
    L.append("缺「平和」已自动用微笑 0.6 倍顶替。\n")

    L.append("## 五、这一轮修掉的东西\n")
    L.append("| 缺陷 | 现象 | 根因 | 修法 |\n|---|---|---|---|")
    L.append("| 表情只写一个网格 | 脸/眼/口是独立网格的模型上「算了但脸不动」 "
             "| PMX 按材质拆网格，各带一份 morph 字典 | 按**名字**跨全部网格写入 |")
    L.append("| 闭眼笑被当成微笑 | happy 时角色闭着眼，像在睡觉 "
             "| 选表情只按「最明显」，而闭眼笑改动面积最大 | smile 槽位加**睁眼优先**硬约束："
             "睁眼笑够亮才用它；睁眼笑几乎为零（< 0.5）时退回闭眼笑并把权重压到 0.7，"
             "眼睛半闭读作眯眼笑，也好过「睁着眼但毫无表情」|")
    L.append("| 标定读数与真机不一致 | 报表写「害羞 2.09」，真机脸上不红 "
             "| 探测时按满权重 1.0 量，实际配方只给 0.48 —— 幅度不够的表情在低权重下归零 "
             "| 候选一律按**实际生效权重**（配方权重 × 0.8）探测，选出后再按生效权重复测一遍 |")
    L.append("| 强表情被弱表情挤掉 | feixue「好奇」只有 0.38，明明有更亮的「はちゅ」 "
             "| morph → 像素不单调：位移要跨过栅格化阈值才出现，0.336 权重下「はちゅ」是 0、"
             "0.672 下是 1.70。只按基础权重量一轮，会把「放大后才发光」的表情全部漏掉 "
             "| 最高分不足 1.0 时再做一轮**放大权重**探测，谁在真机权重下更亮就用谁 |")
    L.append("| 睁眼笑/闭眼笑判定漂浮 | 同一个 peier，两次标定给出相反的结论 "
             "| 界线卡在 0.5，而「喜び」算出来正好 0.50（真闭眼笑都在 0.76 以上，中间是空档）"
             "| 界线挪到 0.6，落进空档里 |")
    L.append("| 语义不贴的弱表情占了槽位 | peier 的「温柔」挂在「にやり」上，只有 0.16 "
             "| 兜底只在「完全没有平和表情」时才触发，不看选中的那个能不能看见 "
             "| 分数不到可见阈值也走兜底，并且取**画面上最亮的笑**压到 0.6 |")
    L.append("| 闭眼笑只有眼睛在动 | 眼睛闭上了，嘴还是平的，读起来像在打瞌睡 "
             "| 很多模型的「笑い」只驱动眼睑，嘴角不在同一个表情里 "
             "| 判定为闭眼笑时自动补一个「口角上げ」（嘴角上扬），让嘴也跟着笑 |")
    L.append("| 材质型表情不执行 | 「照れ」写多少权重画面都不变 "
             "| MMDLoader 对 PMX morph type 8 是 `// TODO: implement` | 拦加载器内部同一份 PMX 解析结果，"
             "自己按加算规则写回材质颜色 |")
    L.append("| 脸红贴图缺失 | 「照れ」测出 0.00%，同模型眨眼 1.9% "
             "| 不少模型的脸红是一块独立贴片，作者打包漏了那张 png（如 wuluo 的 `表情.png`），"
             "空贴图 + MMD 自定义混合 = 什么都不画 | 标定发现原生脸红看不见时，改用**脸材质染色**兜底（伪表情 `__tint`）|")
    L.append("")
    L.append("标定口径本身也换过一次：早期用「跨异步任务采样」，读数会整体漂移、"
             "同一表情两次能量出差一个数量级；现在把「清零 → 摆表情 → render → readPixels → 算差」"
             "压进**同一个同步任务**，噪声实测 0%。\n")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n写入 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
