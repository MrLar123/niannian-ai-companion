# -*- coding: utf-8 -*-
"""把 face_shots.mjs 出的 8 张表情图拼成一张对比图（人工目视验收用）。

- 按 meta.json 里的头骨屏幕坐标自动裁头肩特写，再 2 倍放大；
- 透明背景垫浅灰底（页面是透明窗，直接看 PNG 会是一片"白"）；
- 中文标签用微软雅黑，缺字体时退回默认（只是字丑，不报错）。

用法：
  python tools/make_contact_sheet.py [shotsDir] [outPng]
"""
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

SHOTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("TEMP", "/tmp"), "xy_face_shots")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SHOTS, "contact_sheet.png")

CN = {
    "happy": "开心 happy", "soft": "温柔 soft", "annoyed": "不耐烦 annoyed",
    "jealous": "吃醋 jealous", "shy": "害羞 shy", "sad": "低落 sad",
    "tired": "疲惫 tired", "curious": "好奇 curious",
}
# 每格贴图尺寸（放大后的特写）
CELL_W, CELL_H = 300, 340
BAR_H = 46
PAD = 8
COLS = 3


def load_font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def crop_head(im, meta):
    W, H = im.size
    hx, hy = meta["head"]
    cw = int(W * 0.34)
    ch = int(H * 0.26)
    left = int(hx - cw / 2)
    # 头骨（head bone）在颅底/脖子顶，脸在它上方：框要整体上提。
    # 上提量按「头骨落在框内 62% 处」算，而不是按画布比例算 ——
    # 不同模型（框高 972 / 1215 都出现过）脸在画面里的绝对位置差很多，
    # 用固定 0.14H 会在高的画布上把头顶切掉。
    top = int(hy - ch * 0.62)
    top = max(0, min(top, H - ch))
    box = (max(0, left), top, min(W, left + cw), min(H, top + ch))
    return im.crop(box).resize((CELL_W, CELL_H), Image.LANCZOS)


def fmt_expr(raw):
    """图注里的表情权重：只留权重最大的两项，否则一行塞不下会糊成一片。"""
    pairs = re.findall(r"([^\s=]+)=([\d.]+)", raw or "")
    if not pairs:
        s = (raw or "").strip()
        if not s or s == "—":
            return ""
        return s if len(s) <= 30 else s[:29] + "…"
    pairs.sort(key=lambda p: -float(p[1]))
    top = pairs[:2]
    out = " · ".join("%s %.2f" % (n, float(v)) for n, v in top)
    if len(pairs) > len(top):
        out += "  +%d" % (len(pairs) - len(top))
    return out


def flatten(im, bg=(246, 244, 247)):
    """透明背景垫底色：桌宠页 alpha 通道是真透明，直接拼图会糊成白板。"""
    base = Image.new("RGB", im.size, bg)
    base.paste(im, (0, 0), im if im.mode == "RGBA" else None)
    return base


def main():
    meta = json.load(open(os.path.join(SHOTS, "meta.json"), encoding="utf-8"))
    items = []
    base_p = os.path.join(SHOTS, "00_base.png")
    if os.path.isfile(base_p):
        im = Image.open(base_p)
        items.append((crop_head(im, meta), "平静 none（基线）", ""))
    for it in meta["labels"]:
        p = os.path.join(SHOTS, it["label"] + ".png")
        if not os.path.isfile(p):
            alt = [f for f in os.listdir(SHOTS) if f.endswith(it["label"] + ".png")]
            if not alt:
                continue
            p = os.path.join(SHOTS, alt[0])
        im = Image.open(p)
        items.append((crop_head(im, meta), CN.get(it["label"], it["label"]),
                      fmt_expr(it.get("expr"))))

    rows = (len(items) + COLS - 1) // COLS
    font_t = load_font(18)
    font_s = load_font(12)
    sheet = Image.new("RGB", (COLS * (CELL_W + PAD) + PAD,
                              rows * (CELL_H + BAR_H + PAD) + PAD), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, (img, title, sub) in enumerate(items):
        r, c = divmod(i, COLS)
        x = PAD + c * (CELL_W + PAD)
        y = PAD + r * (CELL_H + BAR_H + PAD)
        sheet.paste(flatten(img), (x, y))
        d.rectangle([x, y, x + CELL_W - 1, y + CELL_H - 1], outline=(226, 222, 230))
        d.text((x + 3, y + CELL_H + 3), title, font=font_t, fill=(32, 30, 36))
        if sub:
            d.text((x + 3, y + CELL_H + 23), sub, font=font_s, fill=(122, 116, 130))
    sheet.save(OUT)
    print(OUT, sheet.size, len(items), "格")


if __name__ == "__main__":
    main()
