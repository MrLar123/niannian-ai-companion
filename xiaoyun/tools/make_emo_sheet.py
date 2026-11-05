#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 tools/pmx_emo_lab.mjs audit all --shots 的截图拼成**看得清表情**的验收图。

为什么不用整张截图：audit 的截图是 620×940 全身像，脸只占十几像素，
拼版出来"每格都面无表情"—— 那不是表情没生效，是根本看不清。
这里按每个模型 labels.json 里记录的头部屏幕坐标裁出脸，再放大。

用法：
    python tools/make_emo_sheet.py <截图根目录> <输出目录>
    python tools/make_emo_sheet.py "$TEMP" docs/验收截图

产出：
    <输出>/表情验收_<模型>.png        单模型 3×3（中性 + 8 情绪），带情绪名
    <输出>/表情验收_<情绪>_全模型.png  同一种情绪在全部模型上的对比，带模型名

依赖 ffmpeg（PATH 里）。截图根目录下每个子目录是一个模型，内含
00_base.png / 01_happy.png ... 08_curious.png / labels.json。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

TAGS = [("00_base", "中性"), ("01_happy", "开心"), ("02_soft", "温柔"),
        ("03_annoyed", "不耐烦"), ("04_jealous", "吃醋"), ("05_shy", "害羞"),
        ("06_sad", "难过"), ("07_tired", "疲惫"), ("08_curious", "好奇")]
# 单情绪全模型对比：取最能体现"看得见/看不见"的四个
SHEETS = [("01_happy", "开心"), ("05_shy", "害羞"), ("03_annoyed", "不耐烦"), ("07_tired", "疲惫")]
CW, CH = 320, 260          # 拼版单格尺寸
FONT = "C\\:/Windows/Fonts/msyh.ttc"


def head_box(model_dir, w, h):
    """按 labels.json 的头部坐标算裁切框；没有就当脸在画面上部居中。"""
    x, y = w * 0.5, h * 0.26
    try:
        d = json.load(open(os.path.join(model_dir, "labels.json"), encoding="utf-8"))
        hx, hy = d.get("head") or (x, y)
        x, y = float(hx), float(hy)
    except Exception:
        pass
    bw, bh = 200, 210
    left = max(0, int(x - bw / 2))
    # 头部坐标通常落在"下巴/嘴"附近，往上留 0.45 倍框高才装得下整个头和头发；
    # 留少了会切掉头顶（yowane / kafka_yoga 实测就是被切了）。
    top = max(0, int(y - bh * 0.45))
    return left, top, bw, bh


def ffmpeg(args):
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args)
    return r.returncode == 0


def tile_model(model_dir, key, out_path):
    """单模型 3×3：九格都裁到脸，左上角写情绪名。"""
    left, top, bw, bh = head_box(model_dir, 620, 940)
    tmp = tempfile.mkdtemp(prefix="emo_sheet_")
    try:
        parts = []
        for i, (tag, cn) in enumerate(TAGS):
            src = os.path.join(model_dir, tag + ".png")
            if not os.path.exists(src):
                continue
            dst = os.path.join(tmp, "p%02d.png" % i)
            vf = ("crop=%d:%d:%d:%d,scale=%d:%d:flags=lanczos,"
                  "drawtext=fontfile='%s':text='%s':x=6:y=6:fontsize=22:"
                  "fontcolor=black:box=1:boxcolor=white@0.55:boxborderw=4"
                  % (bw, bh, left, top, CW, CH, FONT, cn))
            if ffmpeg(["-i", src, "-vf", vf, dst]):
                parts.append(dst)
        if not parts:
            return False
        # 不足九格用最后一格补满，保证 tile 3x3 不崩
        while len(parts) < 9:
            shutil.copy(parts[-1], os.path.join(tmp, "p%02d.png" % len(parts)))
        ok = ffmpeg(["-framerate", "1", "-i", os.path.join(tmp, "p%02d.png"),
                     "-vf", "tile=3x3:padding=6:margin=6:color=white",
                     "-frames:v", "1", out_path])
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tile_emotion(root, models, tag, cn, out_path):
    """同一种情绪 × 全部模型：一行 8 个，带模型名。"""
    tmp = tempfile.mkdtemp(prefix="emo_emo_")
    try:
        n = 0
        for m in models:
            d = os.path.join(root, "xy_emo_audit_" + m)
            src = os.path.join(d, tag + ".png")
            if not os.path.exists(src):
                continue
            left, top, bw, bh = head_box(d, 620, 940)
            dst = os.path.join(tmp, "s%02d.png" % n)
            vf = ("crop=%d:%d:%d:%d,scale=240:196:flags=lanczos,"
                  "drawtext=fontfile='%s':text='%s':x=4:y=4:fontsize=17:"
                  "fontcolor=black:box=1:boxcolor=white@0.55:boxborderw=3"
                  % (bw, bh, left, top, FONT, m))
            if ffmpeg(["-i", src, "-vf", vf, dst]):
                n += 1
        if not n:
            return False
        cols = 8
        return ffmpeg(["-framerate", "1", "-i", os.path.join(tmp, "s%02d.png"),
                       "-vf", "tile=%dx%d:padding=5:margin=5:color=white"
                              % (cols, (n + cols - 1) // cols),
                       "-frames:v", "1", out_path])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    root, out = sys.argv[1], sys.argv[2]
    models = sorted(x[len("xy_emo_audit_"):] for x in os.listdir(root)
                    if x.startswith("xy_emo_audit_"))
    if not models:
        print("没找到 xy_emo_audit_* 截图目录：%s" % root)
        return 1
    os.makedirs(out, exist_ok=True)

    done = 0
    for m in models:
        if tile_model(os.path.join(root, "xy_emo_audit_" + m), m,
                      os.path.join(out, "表情验收_%s.png" % m)):
            done += 1
    print("单模型九宫格：%d/%d" % (done, len(models)))

    for tag, cn in SHEETS:
        if tile_emotion(root, models, tag, cn,
                        os.path.join(out, "表情验收_%s_全模型.png" % cn)):
            print("全模型对比：%s" % cn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
