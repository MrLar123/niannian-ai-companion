# -*- coding: utf-8 -*-
"""把「人物形象 / 动作」的菜单名整理成人话（一次跑完，可重复跑）。

为什么单独做一个脚本：
  菜单名的唯一数据源是 assets/models/<key>/model.json 与 assets/motions/<key>.json。
  原来这些 label 混着作品名、作者名、技术后缀（"MDJSN edit"、"素体"、"头发物理优化"、
  "（模之屋·来源待确认）"），右键菜单里 31 个模型 + 19 个动作根本没法一眼看明白。
  改名不该满仓库手改 json（容易漏、容易把 params/credit 冲掉），所以集中在这一张表里。

三件事：
  1) label 换成「角色 · 服装」「动作名 · 时长」，去掉作者 / 作品 / 技术后缀；
  2) 打上 group（人物=日常/泳装/礼服居家/特殊形态，动作=待机/舞蹈），
     菜单据此折叠成两级，31 项不会一次糊在脸上；
  3) order 由「组序 × 100 + 组内序」算出来，菜单顺序 = 下面这张表的书写顺序。

动作的时长与「只动手 / 只动上半身」来自 tools/probe_motions.py 的实测（解析 VMD 帧），
不是手抄的估计值；改了动作文件就重跑一次 probe_motions.py 再核对这里。

用法：
  python tools/normalize_menu_labels.py --dry     # 只打印将要改成什么
  python tools/normalize_menu_labels.py           # 落盘
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")

# 组顺序 = 菜单里的先后。label 是子菜单标题上的字。
MODEL_GROUPS = [
    ("daily",   "日常"),
    ("swim",    "泳装"),
    ("dress",   "礼服 · 旗袍 · 居家"),
    ("special", "特殊形态"),
]
MOTION_GROUPS = [
    ("idle",  "待机 · 小动作"),
    ("dance", "舞蹈"),
]

# (组, key, 新名字)　书写顺序就是组内顺序
MODEL_MENU = [
    ("daily", "kafka",        "卡芙卡"),
    ("daily", "rover",        "女漂泊者"),
    ("daily", "gengchen",     "庚辰 · 海上私语"),
    ("daily", "wuluo",        "武罗 · 锻玉"),
    ("daily", "qianxi",       "千咲"),
    ("daily", "ganyu",        "甘雨"),
    ("daily", "raiden",       "雷电将军"),
    ("daily", "raiden_mdjsn", "雷电将军 · 素体"),
    ("daily", "huangquan",    "黄泉"),
    ("daily", "xiangnaimei",  "香奈美"),
    ("daily", "nicole_zzz",   "妮可"),
    ("daily", "yoohee_skin",  "幼熙"),
    ("daily", "furina_mdjsn", "芙宁娜"),
    ("daily", "ruanmei_mdjsn", "阮梅"),
    ("daily", "wajite_cool",  "瓦吉特 · 清凉"),
    ("daily", "peier",        "姵儿"),

    ("swim", "elysia",        "爱莉希雅 · 泳装"),
    ("swim", "kafka_vest",    "卡芙卡 · 泳装"),
    ("swim", "clukay_summer", "可露凯 · 泳装"),
    ("swim", "nier42_swim",   "42 · 泳装"),
    ("swim", "yingzhao_swim", "英招 · 泳装"),

    ("dress", "yowane",       "弱音 · 旗袍"),
    ("dress", "luotianyi",    "洛天依 · 旗袍"),
    ("dress", "helen_dress",  "海伦 · 礼服"),
    ("dress", "xiaoyu",       "涂山小玉 · 睡裙"),
    ("dress", "kafka_yoga",   "卡芙卡 · 瑜伽裤"),
    ("dress", "jiuyuan_yoga", "九原 · 瑜伽服"),

    ("special", "feixue",        "绯雪 · 一形态"),
    ("special", "feixue2",       "绯雪 · 二形态"),
    ("special", "hades_dengying", "哈迪斯 · 灯影"),
    ("special", "ainingjing",    "无限之视 · 凝晶"),
]

# 时长来自 probe_motions.py（VMD 最大帧 / 30）。括号里只说"动了哪里"这种选之前想知道的事。
MOTION_MENU = [
    ("idle", "phut_hon",     "Phut hon · 5 秒"),
    ("idle", "idle_daiji",   "摆手呼吸 · 8 秒"),
    ("idle", "hucao_qiuqiu", "丘丘摇 · 11 秒"),
    ("idle", "niko",         "妮可妮可妮 · 12 秒"),

    ("dance", "tankouwu",     "弹跳扣扣舞 · 8 秒"),
    ("dance", "yao",          "摇 · 9 秒"),
    ("dance", "lxjwwc",       "舞蹈 · 10 秒"),
    ("dance", "xiangxiang",   "想想念念 · 10 秒"),
    ("dance", "xiaodangao",   "小蛋糕在哪呢 · 10 秒"),
    ("dance", "changchangyao", "常常摇（只动上半身）· 18 秒"),
    ("dance", "aligaduo",     "喵！阿里嘎多 · 19 秒"),
    ("dance", "yeyeye",       "yeyeye · 23 秒"),
    ("dance", "phut_hon_long", "Phut hon 慢版 · 24 秒"),
    ("dance", "aini",         "爱你 · 49 秒"),
    ("dance", "dance_myst57", "舞蹈 · 57 秒"),
    ("dance", "wangcheng",    "忘城 · 67 秒"),
    ("dance", "bingmingweiai", "病名为爱 · 78 秒"),
    ("dance", "nya_fingers",  "手指律动（只动手）· 82 秒"),
    ("dance", "cinderella",   "灰姑娘 · 2 分 20 秒"),
]

# 字段顺序固定，写回去的 json 才好看且 diff 稳定
HEAD = ("key", "label", "group", "order")


def reorder(d):
    out = {k: d[k] for k in HEAD if k in d}
    for k, v in d.items():
        if k not in out:
            out[k] = v
    return out


def model_json_path(key):
    for p in (os.path.join(ASSETS, "models", key, "model.json"),
              os.path.join(ASSETS, key, "model.json")):
        if os.path.isfile(p):
            return p
    return None


def apply_models(dry):
    gidx = {k: i for i, (k, _) in enumerate(MODEL_GROUPS)}
    seen, missing = set(), []
    for group, key, label in MODEL_MENU:
        idx_in_group = [r[1] for r in MODEL_MENU if r[0] == group].index(key)
        order = gidx[group] * 100 + idx_in_group * 5
        p = model_json_path(key)
        if not p:
            missing.append(key)
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f) or {}
        seen.add(key)
        d["key"] = d.get("key") or key
        d["label"] = label
        d["group"] = group
        d["order"] = order
        d = reorder(d)
        print("  %-14s %-16s -> %s" % (key, group, label))
        if not dry:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
                f.write("\n")
    print("人物：写成 %d 个，缺目录 %d 个 %s" % (len(seen), len(missing), missing or ""))
    return missing


def apply_motions(dry):
    gidx = {k: i for i, (k, _) in enumerate(MOTION_GROUPS)}
    seen, missing = set(), []
    for group, key, label in MOTION_MENU:
        p = os.path.join(ASSETS, "motions", key + ".json")
        if not os.path.isfile(p):
            missing.append(key)
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f) or {}
        seen.add(key)
        d["key"] = d.get("key") or key
        d["label"] = label
        d["group"] = group
        d["order"] = gidx[group] * 100 + [r[1] for r in MOTION_MENU
                                          if r[0] == group].index(key) * 5
        d = reorder(d)
        print("  %-14s %-6s -> %s" % (key, group, label))
        if not dry:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
                f.write("\n")
    print("动作：写成 %d 个，缺文件 %d 个 %s" % (len(seen), len(missing), missing or ""))
    return missing


def main():
    dry = "--dry" in sys.argv
    print("== 人物形象 ==")
    miss1 = apply_models(dry)
    print("== 动作 ==")
    miss2 = apply_motions(dry)
    if miss1 or miss2:
        print("有 key 找不到文件，检查上面的名单")
        return 1
    print("完成%s" % ("（dry-run，未落盘）" if dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
