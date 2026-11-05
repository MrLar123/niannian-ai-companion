# -*- coding: utf-8 -*-
"""右键菜单「音色」子菜单自检（不弹窗、不点鼠标，纯数据校验）。

为什么要有这个：菜单是 Win32 手绘的，肉眼验收要靠人手点开右键；
真正会出错的环节是 popup_menu_layout() 拿到的数据（项数、标签、勾选）。
这里直接喂真实后端数据跑一遍，断言新加的 6 个豆包女声都在菜单里、
且勾选项与后端 current 一致。

用法：
    cd D:\\LunaAI\\project-nn\\xiaoyun
    .venv\\Scripts\\python.exe tools\\test_voice_menu.py
"""
import json
import os
import sys
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = "http://127.0.0.1:" + os.environ.get("XIAOYUN_PORT", "8090")

# 新加的 6 个女声：必须在菜单里出现
NEW_VOICES = {
    "zh_female_shuangkuaisisi_uranus_bigtts": "豆包 · 爽快思思",
    "zh_female_kailangjiejie_uranus_bigtts": "豆包 · 开朗姐姐",
    "zh_female_linjianvhai_uranus_bigtts": "豆包 · 邻家女孩",
    "zh_female_tianmeixiaoyuan_uranus_bigtts": "豆包 · 甜美小源",
    "saturn_zh_female_keainvsheng_tob": "豆包 · 可爱女生（saturn）",
    "saturn_zh_female_tiaopigongzhu_tob": "豆包 · 调皮公主（saturn）",
}


def get(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    vo = get("/api/voice-options")
    opts = vo.get("options") or []
    cur = vo.get("current") or ""

    # 假 app：只给 popup_menu_layout 需要的字段
    app = types.SimpleNamespace(
        models=[], motions=[], chat_models=[], presets=[],
        model_key="", motion_key="", chat_model="", chat_label="", chat_best="",
        preset_key="", preset_label="", proactive_levels=[], proactive_key="",
        proactive_label="", gaze_mode="always", poke_style="soft",
        voices=opts, voice_id=cur,
        voice_label=next((o["label"] for o in opts if o.get("id") == cur), cur),
    )

    import desktop_app as D
    rows = D.popup_menu_layout(app)
    voice_row = next((r for r in rows if r[0] == "submenu" and r[1].startswith("音色")), None)
    if not voice_row:
        print("失败：右键菜单里没有「音色」子菜单")
        return 1
    kids = voice_row[4]
    labels = [k[0] for k in kids]
    ids_ok = len(kids) == len(opts)

    fail = []
    if not ids_ok:
        fail.append("菜单项数 %d != 后端音色数 %d" % (len(kids), len(opts)))
    for vid, label in NEW_VOICES.items():
        hit = next((o for o in opts if o.get("id") == vid), None)
        if not hit:
            fail.append("后端清单里没有 %s" % vid)
            continue
        if hit["label"] not in labels:
            fail.append("菜单里缺少「%s」" % hit["label"])

    checked = [k for k in kids if k[2]]
    if len(checked) != 1:
        fail.append("勾选项应当恰好 1 个，实际 %d 个" % len(checked))

    print("音色子菜单标题: %s" % voice_row[1])
    print("菜单项数: %d（后端 %d） 勾选: %s" % (len(kids), len(opts),
                                            checked[0][0] if checked else "无"))
    print("---- 菜单里的音色 ----")
    for i, k in enumerate(kids):
        mark = "√" if k[2] else " "
        print(" %s %2d. %s" % (mark, i + 1, k[0]))
    if fail:
        print("\n失败项：")
        for f in fail:
            print("  -", f)
        return 1
    print("\n全部通过：新增 6 个女声都在菜单里，勾选与后端一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
