# -*- coding: utf-8 -*-
"""右键菜单「人物形象 / 动作」自检：不弹窗、不点鼠标，纯数据校验。

为什么要有这个：菜单是 Win32 手绘的，人手点开才能看见；但真正会出错的环节是
popup_menu_layout() 拿到的数据 —— 项数、分组、名字、勾选、命令 ID 与清单下标对不对得上。
这里喂真实后端数据跑一遍，把它当回归测试用。

断言六件事：
  1) 「人物形象」是两级（服装类型 -> 角色），各组合计 = 后端模型数，没有掉进「其它」的漏网项；
  2) 每个名字都不是技术术语（无 MDJSN / edit / 模之屋 / 作者 / loop 之类）；
  3) 勾选恰好一个，且指向后端 current；
  4) 每个菜单项的命令 ID 反算出来的下标，对应的正是它的 key（错位会让点 A 变成 B）；
  5) 动作同理（内置待机 + 19 个 VMD，分「待机 · 小动作」「舞蹈」两组）；
  6) 每个 model.json / 动作 json 都真的有 label + group（数据源本身不缺）。

用法：
    cd D:\\LunaAI\\project-nn\\xiaoyun
    .venv\\Scripts\\python.exe tools\\test_menu_labels.py
"""
import glob
import json
import os
import sys
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = "http://127.0.0.1:" + os.environ.get("XIAOYUN_PORT", "8090")

# 名字里不该再出现的东西（都是上一版菜单里真实出现过的）
BANNED = ("MDJSN", "edit", "模之屋", "作者", "来源", "待确认", "loop", "Lora", "LORA")


def get(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def flatten(kids):
    """把两级子菜单摊平 -> [(label, cmd, checked, 组标题)]"""
    out = []
    for ent in kids:
        if len(ent) >= 5 and ent[0] == "sub":
            for c in ent[4]:
                out.append((c[0], c[1], c[2], ent[1]))
        else:
            out.append((ent[0], ent[1], ent[2], "（未分组）"))
    return out


def build_app(D):
    models = get("/api/models")
    motions = get("/api/motions")
    chat = get("/api/chat-model")
    voice = get("/api/voice-options")
    preset = get("/api/preset")
    proact = get("/api/proactive")
    idle = {"key": "idle", "group": "idle",
            "label": motions.get("idle_label") or "内置待机"}
    return types.SimpleNamespace(
        models=models.get("models") or [],
        model_groups=models.get("groups") or [],
        model_key=models.get("current") or "",
        motions=[idle] + (motions.get("motions") or []),
        motion_groups=motions.get("groups") or [],
        motion_key=motions.get("current") or "idle",
        chat_models=chat.get("models") or [],
        chat_model=chat.get("current") or "",
        chat_label=chat.get("label") or "",
        chat_best=chat.get("best") or "",
        voices=voice.get("options") or [],
        voice_id=voice.get("current") or "",
        voice_label=voice.get("label") or "",
        presets=preset.get("presets") or [],
        preset_key=preset.get("current") or "",
        preset_label=preset.get("label") or "",
        proactive_levels=proact.get("levels") or [],
        proactive_key=proact.get("current") or "",
        proactive_label=proact.get("label") or "",
        gaze_mode="always", poke_style="soft",
    )


def check_group_row(row, items, base, key_of, cur_key, label, fail):
    """通用：两级子菜单 vs 扁平清单的一致性检查。"""
    kids, cur_label = row[4], None
    flat = flatten(kids)
    if not any(len(e) >= 5 and e[0] == "sub" for e in kids):
        fail.append("%s：没有分组（应折成两级）" % label)
    sub_counts = [(e[1], len(e[4])) for e in kids if len(e) >= 5 and e[0] == "sub"]
    if len(flat) != len(items):
        fail.append("%s：菜单项 %d != 清单 %d（有项被漏掉/重复）"
                    % (label, len(flat), len(items)))
    if any(g == "其它" or g.startswith("其它（") for _l, _c, _x, g in flat):
        fail.append("%s：有项落进「其它」—— 说明某个 json 的 group 值不在分组表里" % label)
    checked = [f for f in flat if f[2]]
    if len(checked) != 1:
        fail.append("%s：勾选应恰好 1 个，实际 %d 个" % (label, len(checked)))
    elif key_of(items[checked[0][1] - base]) != cur_key:
        fail.append("%s：勾的不是当前的（%s）" % (label, checked[0][0]))
    for lab, cmd, _c, _g in flat:
        idx = cmd - base
        if not (0 <= idx < len(items)):
            fail.append("%s：「%s」命令 ID 越界" % (label, lab))
            continue
        if lab.rstrip("（VRM）") != (items[idx].get("label") or "").rstrip("（VRM）"):
            if lab.replace("（VRM）", "") != (items[idx].get("label") or ""):
                fail.append("%s：「%s」与下标 %d 的条目对不上（%s）"
                            % (label, lab, idx, items[idx].get("label")))
        for b in BANNED:
            if b in lab:
                fail.append("%s：「%s」还带着 %s" % (label, lab, b))
        if len(lab) > 20:
            fail.append("%s：「%s」太长（%d 字）" % (label, lab, len(lab)))
    print("%s 子菜单标题: %s" % (label, row[1]))
    print("  分组: " + "  ".join("%s=%d" % (t, n) for t, n in sub_counts)
          + "  合计 %d（清单 %d）" % (len(flat), len(items)))
    for lab, _cmd, chk, grp in flat:
        print("   %s %-8s %s" % ("√" if chk else " ", grp, lab))
    return flat


def main():
    import desktop_app as D
    app = build_app(D)
    rows = D.popup_menu_layout(app)
    mrow = next((r for r in rows if r[0] == "submenu" and r[1].startswith("人物形象")), None)
    arow = next((r for r in rows if r[0] == "submenu" and r[1].startswith("动作")), None)
    fail = []
    if not mrow:
        print("失败：右键菜单里没有「人物形象」子菜单")
        return 1
    if not arow:
        print("失败：右键菜单里没有「动作」子菜单")
        return 1
    check_group_row(mrow, app.models, D.MENU_MODEL_BASE,
                    lambda m: m.get("key"), app.model_key, "人物形象", fail)
    print()
    check_group_row(arow, app.motions, D.MENU_MOTION_BASE,
                    lambda m: m.get("key"), app.motion_key, "动作", fail)

    # 数据源自身：每个文件都得有 label + group，否则菜单只能退回 key（英文/拼音）
    print()
    nbad = 0
    for p in glob.glob(os.path.join(ROOT, "assets", "models", "*", "model.json")) \
            + [os.path.join(ROOT, "assets", "peier", "model.json")]:
        d = json.load(open(p, encoding="utf-8"))
        if not d.get("label") or not d.get("group"):
            print("  数据缺口: %s" % p)
            nbad += 1
    for p in glob.glob(os.path.join(ROOT, "assets", "motions", "*.json")):
        d = json.load(open(p, encoding="utf-8"))
        if not d.get("label") or not d.get("group"):
            print("  数据缺口: %s" % p)
            nbad += 1
    if nbad:
        fail.append("有 %d 个 json 缺 label/group" % nbad)

    if fail:
        print("\n失败项：")
        for f in fail:
            print("  -", f)
        return 1
    print("\n全部通过：两级分组、名字无术语、勾选与后端一致、命令 ID 与清单下标一一对应")
    return 0


if __name__ == "__main__":
    sys.exit(main())
