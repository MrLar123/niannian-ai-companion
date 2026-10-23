# -*- coding: utf-8 -*-
"""
生成中文口型字典：汉字 -> 两个口型码（声母口型 + 韵母口型）

口型码（与 lipsync.js 保持一致）：
  0 = 闭唇  (b p m 等爆破/鼻音成阻)
  1 = 大开口 (a / ai / an / ang ...)
  2 = 扁张口 (i / in / ing / 舌面音声母)
  3 = 圆撮口 (u / ü / ong ...)
  4 = 半开中口 (e / ei / en / eng / er)
  5 = 圆唇 o  (o / uo / ou / iou ...)

输出：assets/lipsync/zh-visemes.json
    {"v":1,"note":"...","map":{"啊":"11","我":"35",...}}

用法（本项目自带 venv 里没有 pypinyin，用独立环境跑一次即可，产物是静态 JSON）：
    python tools/gen_viseme_dict.py
"""
import json
import os
import sys

from pypinyin import lazy_pinyin, Style

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "lipsync", "zh-visemes.json")

INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
            "g", "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"]

# 声母口型
INITIAL_VISEME = {
    "b": 0, "p": 0, "m": 0,
    "f": 2,
    "d": 2, "t": 2, "n": 2, "l": 2,
    "g": 2, "k": 2, "h": 2,
    "j": 2, "q": 2, "x": 2,
    "zh": 2, "ch": 2, "sh": 2, "r": 2,
    "z": 2, "c": 2, "s": 2,
    "y": 2,
    "w": 3,
}

# 韵母口型（按最长匹配）
FINAL_VISEME = {
    "iang": 1, "iong": 3, "uang": 1, "ueng": 3, "uai": 1, "uan": 1, "ian": 1,
    "iao": 1, "iou": 5, "ang": 1, "eng": 4, "ing": 2, "ong": 3,
    "ai": 1, "ei": 4, "ao": 1, "ou": 5, "an": 1, "en": 4, "er": 4,
    "ia": 1, "ie": 4, "iu": 5, "in": 2, "un": 4, "uo": 5, "ua": 1,
    "ui": 4, "ue": 4, "ve": 4, "vn": 3, "van": 1,
    "a": 1, "o": 5, "e": 4, "i": 2, "u": 3, "v": 3,
    "n": 4, "ng": 4, "m": 0,
}


def split_syllable(py: str):
    """把无声调拼音切成 (声母, 韵母)"""
    py = py.strip().lower()
    for ini in INITIALS:
        if py.startswith(ini):
            # 注意：zhi/chi/shi/ri/zi/ci/si 的 i 是舌尖元音，仍归入 2 号口型
            return ini, py[len(ini):]
    return "", py


def final_viseme(fin: str) -> int:
    if not fin:
        return 0
    if fin in FINAL_VISEME:
        return FINAL_VISEME[fin]
    # 最长前缀匹配，兜底再逐字符
    for n in (4, 3, 2):
        if fin[:n] in FINAL_VISEME:
            return FINAL_VISEME[fin[:n]]
    for ch in fin:
        if ch in FINAL_VISEME:
            return FINAL_VISEME[ch]
    return 2


def syllable_code(ch: str):
    py = lazy_pinyin(ch, style=Style.NORMAL, errors="ignore")
    if not py or py[0] == ch:
        return None
    py = py[0]
    ini, fin = split_syllable(py)
    vf = final_viseme(fin)
    # 零声母（啊/我/一）没有成阻动作，起口型直接跟随韵母
    vi = INITIAL_VISEME[ini] if ini else vf
    return "%d%d" % (vi, vf)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    table = {}
    for cp in range(0x4E00, 0xA000):          # CJK 基本区
        ch = chr(cp)
        code = syllable_code(ch)
        if code:
            table[ch] = code
    data = {
        "v": 1,
        "note": "汉字->声母口型+韵母口型；0闭唇 1开口 2扁口 3撮口 4中口 5圆唇",
        "map": table,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT)
    print("wrote %s  chars=%d  size=%.1f KB" % (OUT, len(table), size / 1024.0))
    for s in "啊啊我你说话嘴巴吃饭喜欢知道":
        print(s, table.get(s))


if __name__ == "__main__":
    sys.exit(main())
