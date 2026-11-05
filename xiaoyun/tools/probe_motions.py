# -*- coding: utf-8 -*-
"""探测动作库的真实情况：时长 / 骨骼覆盖 / 是否带位移 / 是否只有手部。

用途：右键菜单里「动作」的名字要写成人能看懂的话，就得先知道每个 VMD 到底是什么。
纯静态解析 VMD，不加载 three.js、不启浏览器。

VMD 布局（little-endian）：
  30 字节签名 "Vocaloid Motion Data 0002\0..."  + 20 字节模型名
  uint32 骨骼帧数 n  ->  n × 111 字节（名字 15 + 帧号 4 + 位置 12 + 四元数 16 + 插值 64）
  uint32 表情帧数 m  ->  m × 23 字节（名字 15 + 帧号 4 + 权重 4）
  uint32 相机帧数 k  ->  k × 61 字节（可选，很多文件到表情就结束了）
  uint32 灯光帧数 ...（再往后是灯光/阴影/IK，一路往后还有，这里不关心）

用法：
  python tools/probe_motions.py                 # 全部动作
  python tools/probe_motions.py aini yao        # 指定动作
  MOTION_JSON=1 python tools/probe_motions.py   # 输出 JSON（写配置用）
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOTION_DIR = os.path.join(ROOT, "assets", "motions")

# 躯干/手臂/腿——用来判断"这个动作到底动了哪里"
GROUPS = {
    "手臂": ("腕", "ひじ", "肩", "arm", "Arm", "elbow"),
    "腿": ("足", "ひざ", "脚", "leg", "Leg", "knee"),
    "头": ("頭", "首", "head", "Head", "neck"),
    "表情": ("目", "まぶた", "口", "眉", "eye", "Eye", "mouth"),
}
ROOT_BONES = ("センター", "全ての親", "center", "Center", "root", "Root")


def read_vmd(path):
    with open(path, "rb") as f:
        buf = f.read()
    if not buf.startswith(b"Vocaloid Motion Data"):
        return {"error": "不是 VMD 文件"}
    off = 30 + 20
    (nbone,) = struct.unpack_from("<I", buf, off)
    off += 4
    bones = {}
    maxf_bone = 0
    root_moved = 0.0
    for _ in range(nbone):
        raw = buf[off:off + 15]
        name = raw.split(b"\x00")[0].decode("shift_jis", "replace")
        (fno,) = struct.unpack_from("<I", buf, off + 15)
        px, py, pz = struct.unpack_from("<3f", buf, off + 19)
        off += 111
        bones[name] = bones.get(name, 0) + 1
        if fno > maxf_bone:
            maxf_bone = fno
        if name in ROOT_BONES:
            root_moved = max(root_moved, abs(px) + abs(pz))
    morph = 0
    if off + 4 <= len(buf):
        (morph,) = struct.unpack_from("<I", buf, off)
    return {"bones": bones, "maxframe": maxf_bone, "morph": morph,
            "rootmoved": round(root_moved, 2), "nbone": nbone}


def summarize(key, path):
    d = read_vmd(path)
    if d.get("error"):
        return {"key": key, "error": d["error"]}
    bones = d["bones"]
    secs = d["maxframe"] / 30.0
    hit = {g: sum(c for n, c in bones.items() if any(k.lower() in n.lower() for k in ks))
           for g, ks in GROUPS.items()}
    total = sum(hit.values()) or 1
    only_hand = hit["手臂"] / total >= 0.9 and hit["腿"] == 0
    biggest = sorted(((c, n) for n, c in bones.items()), reverse=True)[:6]
    return {
        "key": key,
        "seconds": round(secs, 1),
        "bones": len(bones),
        "frames": d["nbone"],
        "morph": d["morph"],
        "root_moved": d["rootmoved"],
        "arms": hit["手臂"], "legs": hit["腿"], "head": hit["头"],
        "only_hand": only_hand,
        "top": [n for _, n in biggest],
    }


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")]
    files = sorted(f for f in os.listdir(MOTION_DIR) if f.lower().endswith(".vmd"))
    if want:
        files = [f for f in files if os.path.splitext(f)[0] in want]
    rows = [summarize(os.path.splitext(f)[0], os.path.join(MOTION_DIR, f)) for f in files]
    if os.environ.get("MOTION_JSON"):
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print("%-18s %6s %5s %5s %8s %5s %5s %5s  %s"
          % ("动作", "时长s", "骨骼", "帧数", "根位移", "臂", "腿", "头", "主要骨骼"))
    for r in rows:
        if r.get("error"):
            print("%-18s  %s" % (r["key"], r["error"]))
            continue
        print("%-18s %6.1f %5d %5d %8.2f %5d %5d %5d  %s%s"
              % (r["key"], r["seconds"], r["bones"], r["frames"], r["root_moved"],
                 r["arms"], r["legs"], r["head"], ",".join(r["top"][:4]),
                 "  [仅手部]" if r["only_hand"] else ""))


if __name__ == "__main__":
    main()
