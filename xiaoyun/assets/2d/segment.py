# -*- coding: utf-8 -*-
"""对 2D 立绘做"轮廓分割"，生成蒙皮查表 mask.json。

为什么要它：2D 立绘是一整张图，要让四肢动起来就必须知道"哪些像素属于手臂/腿/躯干"。
手工填阈值很容易错（这版立绘的手臂紧贴身体，按固定 |x| 阈值会把肩膀也切进去）。
这里改成按行扫描 alpha 的连通段：
  · 含身体中线的那一段 = 躯干
  · 完全在它左侧/右侧的独立段 = 左臂/右臂（只有手臂与躯干之间真的有空隙时才会出现）
  · 裙摆以下若有两段 = 左腿/右腿
把每行的分段区间（换算成"脚底 y=0、头顶 y=1、身体中线 x=0"的归一化坐标）导出，
页面按行查表即可精确决定每个顶点的骨骼归属。
"""
import json
import os
from PIL import Image

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "char.png")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mask.json")
BINS = 256          # 纵向分 256 段

im = Image.open(SRC).convert("RGBA")
W, H = im.size
alpha = im.getchannel("A").load()

bb = im.getchannel("A").getbbox()
bl, bt, br, bb_bottom = bb
body_h = bb_bottom - bt                      # 人物像素身高
cx_px = (bl + br) / 2.0                      # 身体中线（像素）


def runs_of_row(y):
    """返回该行 alpha 连续段 [(x0, x1), ...]（x1 为开区间）"""
    out = []
    x = 0
    while x < W:
        if alpha[x, y] > 128:
            x0 = x
            while x < W and alpha[x, y] > 128:
                x += 1
            if x - x0 >= 3:                  # 忽略 3 像素以下的噪点
                out.append((x0, x - 1))
        else:
            x += 1
    return out


def to_body(x, y):
    """像素坐标 → 归一化身体坐标（脚底 y=0、头顶 y=1、中线 x=0）"""
    return ((x - cx_px) / body_h, (bb_bottom - y) / body_h)


rows = []
for i in range(BINS):
    # 每段取其中间行作为代表
    y = int(round(bb_bottom - (i + 0.5) * body_h / BINS))
    y = max(0, min(H - 1, y))
    body_y = (bb_bottom - y) / body_h
    segs = runs_of_row(y)
    if not segs:
        rows.append({"y": round(body_y, 4), "segs": []})
        continue

    # 找出含中线的段 = 躯干；否则取最宽的一段
    torso_i = None
    for k, (a, b) in enumerate(segs):
        if a <= cx_px <= b:
            torso_i = k
            break
    if torso_i is None:
        torso_i = max(range(len(segs)), key=lambda k: segs[k][1] - segs[k][0])

    # 裙摆以下的两段 = 两条腿
    is_leg_zone = body_y < 0.45 and len(segs) == 2 and torso_i is not None

    labelled = []
    if is_leg_zone:
        # 两段分别是左右腿：按 x 排序
        segs_sorted = sorted(segs)
        labelled.append(["legR" if (segs_sorted[0][0] + segs_sorted[0][1]) / 2 > cx_px else "legL",
                         to_body(segs_sorted[0][0], y)[0], to_body(segs_sorted[0][1], y)[0]])
        labelled.append(["legR" if (segs_sorted[1][0] + segs_sorted[1][1]) / 2 > cx_px else "legL",
                         to_body(segs_sorted[1][0], y)[0], to_body(segs_sorted[1][1], y)[0]])
    else:
        for k, (a, b) in enumerate(segs):
            x0, _ = to_body(a, y)
            x1, _ = to_body(b, y)
            if k == torso_i:
                tag = "torso"
            elif (a + b) / 2 < cx_px:
                tag = "armL"
            else:
                tag = "armR"
            labelled.append([tag, round(x0, 4), round(x1, 4)])

    rows.append({"y": round(body_y, 4), "segs": labelled})

json.dump({"image": os.path.basename(SRC), "w": W, "h": H,
           "bbox": [bl, bt, br, bb_bottom], "body_h": body_h,
           "cx": cx_px, "rows": rows},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

# —— 报告：能看到手臂/腿在哪些高度被分离出来 ——
stat = {}
for r in rows:
    tags = tuple(sorted({s[0] for s in r["segs"]}))
    stat[tags] = stat.get(tags, 0) + 1
print("分段类型统计（标签组合 → 行数）:")
for k, v in sorted(stat.items(), key=lambda kv: -kv[1]):
    print("  ", k, v)
print("人物像素身高 = %d，中线 x = %.1f" % (body_h, cx_px))
print("已写出:", OUT)
