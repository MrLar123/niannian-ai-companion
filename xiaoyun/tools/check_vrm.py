# -*- coding: utf-8 -*-
"""VRM 入库自检（部署前必跑）

用法:
    python check_vrm.py <模型.vrm> [更多.vrm ...]
    python check_vrm.py 小芸.vrm --json

检查项（FAIL 会阻断部署，WARN 只提示）:
    1. 容器格式 / VRM 版本
    2. 预设表情完整性（含口型五元音，缺失会让 driveMouth 静默失效）
    3. 自定义表情数量（0 个 = 无法表达 shy/annoyed/sleepy/pout）
    4. humanoid 必需骨骼（缺 leftEye/rightEye 会让目光追随静默失效）
    5. 弹簧骨数量
    6. 节点缩放（缩放模型是铁律禁区）
    7. 绑定姿态包围盒高度（用于取景参考）
    8. 网格/节点规模（性能参考）

只依赖标准库，不发网络请求，不修改模型文件。
"""
import json
import struct
import sys
import os

# 本项目 lipsync.js 依赖五元音，缺任一则口型失效
REQUIRED_PRESETS = ["happy", "angry", "sad", "relaxed", "surprised",
                    "aa", "ih", "ou", "ee", "oh", "blink"]
# 目光追随 / 头部朝向依赖的眼骨
REQUIRED_BONES = ["hips", "spine", "neck", "head", "leftEye", "rightEye",
                  "leftUpperArm", "leftLowerArm", "leftHand",
                  "rightUpperArm", "rightLowerArm", "rightHand",
                  "leftUpperLeg", "leftLowerLeg", "leftFoot",
                  "rightUpperLeg", "rightLowerLeg", "rightFoot"]
HEIGHT_RANGE = (0.80, 2.30)  # 米，超出基本可判定比例异常


def _fail(msg):
    print(msg)
    sys.exit(2)


def load_gltf_json(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("不是 GLB 容器（可能是 .gltf + 外部 bin）")
    ver = struct.unpack("<I", data[4:8])[0]
    if ver != 2:
        raise ValueError("glTF 版本为 %d，仅支持 2" % ver)
    total = struct.unpack("<I", data[8:12])[0]
    if total != len(data):
        # 尾部冗余不致命，但提示文件可能被截断
        pass
    off = 12
    while off + 8 <= len(data):
        clen, ctype = struct.unpack("<II", data[off:off + 8])
        if ctype == 0x4E4F534A:  # JSON
            return json.loads(data[off + 8:off + 8 + clen].decode("utf-8"))
        off += 8 + clen + ((4 - clen % 4) % 4 if clen % 4 else 0)
    raise ValueError("GLB 内未找到 JSON 块")


def detect_version(j):
    exts = j.get("extensions", {}) or {}
    if "VRMC_vrm" in exts:
        return "VRM1.0", exts["VRMC_vrm"]
    if "VRM" in exts:
        return "VRM0.x", exts["VRM"]
    raise ValueError("不含 VRM 扩展（普通 glTF）")


def get_presets(vrm, ver):
    if ver == "VRM1.0":
        exp = (vrm.get("expressions") or {})
        preset = exp.get("preset") or {}
        custom = exp.get("custom") or {}
        return list(preset.keys()), list(custom.keys())
    # VRM 0.x: blendShapeMaster.blendShapeGroups[]
    groups = (vrm.get("blendShapeMaster") or {}).get("blendShapeGroups", [])
    preset, custom = [], []
    for g in groups:
        name = g.get("presetName") or ""
        if name and name != "unknown":
            preset.append(name)
        else:
            custom.append(g.get("name") or "unnamed")
    return preset, custom


def get_bones(vrm, ver):
    if ver == "VRM1.0":
        hb = (vrm.get("humanoid") or {}).get("humanBones") or {}
        return list(hb.keys())
    hb = (vrm.get("humanoid") or {}).get("humanBones") or []
    return [b.get("bone") for b in hb if b.get("bone")]


def get_spring_count(j, ver):
    exts = j.get("extensions", {}) or {}
    if ver == "VRM1.0":
        return len(((exts.get("VRMC_springBone") or {}).get("springs")) or [])
    return len(((exts.get("VRM") or {}).get("secondaryAnimation") or {}).get("boneGroups") or [])


def bind_bbox(j):
    """绑定姿态包围盒（未计节点变换，仅作量级参考）。"""
    lo = [1e9] * 3
    hi = [-1e9] * 3
    hit = False
    for m in j.get("meshes", []) or []:
        for p in m.get("primitives", []) or []:
            ai = (p.get("attributes") or {}).get("POSITION")
            if ai is None:
                continue
            acc = (j.get("accessors") or [])[ai] or {}
            mn, mx = acc.get("min"), acc.get("max")
            if not mn or not mx:
                continue
            hit = True
            for i in range(3):
                lo[i] = min(lo[i], mn[i])
                hi[i] = max(hi[i], mx[i])
    if not hit:
        return None
    return [round(hi[i] - lo[i], 3) for i in range(3)]


def scaled_nodes(j):
    bad = []
    for i, n in enumerate(j.get("nodes", []) or []):
        s = n.get("scale")
        if not s:
            continue
        if any(abs(v - 1.0) > 0.02 for v in s):
            bad.append((i, n.get("name") or "node%d" % i, [round(v, 3) for v in s]))
    return bad


def check_one(path, as_json=False):
    res = {"file": os.path.basename(path), "path": path,
           "checks": [], "fail": 0, "warn": 0}

    def add(level, item, detail):
        res["checks"].append({"level": level, "item": item, "detail": detail})
        if level == "FAIL":
            res["fail"] += 1
        elif level == "WARN":
            res["warn"] += 1

    try:
        size_kb = round(os.path.getsize(path) / 1024.0, 1)
        j = load_gltf_json(path)
        ver, vrm = detect_version(j)
    except Exception as e:
        add("FAIL", "解析", str(e))
        res["verdict"] = "不可用"
        return res

    res["version"] = ver
    res["size_kb"] = size_kb

    # 1 版本
    if ver == "VRM1.0":
        add("PASS", "版本", "VRM 1.0 / %.1f MB" % (size_kb / 1024.0))
    else:
        add("WARN", "版本", "VRM 0.x，three-vrm 需走 legacy 兼容路径，建议升级 1.0")

    # 2 预设表情
    preset, custom = get_presets(vrm, ver)
    missing = [p for p in REQUIRED_PRESETS if p not in preset]
    res["presets"] = preset
    if missing:
        add("FAIL", "预设表情", "%d/%d，缺 %s（口型或眨眼会静默失效）"
            % (len(preset), len(REQUIRED_PRESETS), ",".join(missing)))
    else:
        add("PASS", "预设表情", "%d 个，必需项齐全" % len(preset))

    # 3 自定义表情
    res["custom"] = custom
    if custom:
        add("PASS", "自定义表情", "%d 个：%s" % (len(custom), ",".join(custom[:8])))
    else:
        add("WARN", "自定义表情", "0 个 → 表达不了 shy/annoyed/sleepy/pout，"
                                  "细腻情绪只能靠预设加权组合")

    # 4 骨骼
    bones = get_bones(vrm, ver)
    res["bones"] = sorted(bones)
    bmiss = [b for b in REQUIRED_BONES if b not in bones]
    if bmiss:
        add("FAIL", "humanoid", "缺 %s" % ",".join(bmiss))
    else:
        add("PASS", "humanoid", "%d 根，目光/手臂/腿链齐全" % len(bones))

    # 5 弹簧骨
    sb = get_spring_count(j, ver)
    res["springs"] = sb
    if sb <= 0:
        add("WARN", "弹簧骨", "0 组 → 头发/裙摆不会摆动，物理感缺失")
    elif sb < 8:
        add("WARN", "弹簧骨", "%d 组，偏少（正常 VRoid 导出为 20+）" % sb)
    else:
        add("PASS", "弹簧骨", "%d 组" % sb)

    # 6 缩放
    bad = scaled_nodes(j)
    res["scaled"] = bad
    if bad:
        sample = "; ".join("%s=%s" % (n, s) for _, n, s in bad[:3])
        add("FAIL", "节点缩放", "%d 个节点非 1.0（%s）→ 弹簧刚度/碰撞半径会失真"
            % (len(bad), sample))
    else:
        add("PASS", "节点缩放", "全部为 1.0")

    # 7 包围盒
    bbox = bind_bbox(j)
    res["bbox"] = bbox
    if not bbox:
        add("WARN", "包围盒", "无法读取 POSITION 范围")
    else:
        h = bbox[1]
        if HEIGHT_RANGE[0] <= h <= HEIGHT_RANGE[1]:
            add("PASS", "包围盒", "%.3f × %.3f × %.3f m（绑定姿态，取景参考）"
                % tuple(bbox))
        else:
            add("WARN", "包围盒", "高度 %.3f m 超出常规 %.1f–%.1f"
                % (h, HEIGHT_RANGE[0], HEIGHT_RANGE[1]))

    # 8 规模
    nodes = len(j.get("nodes") or [])
    meshes = len(j.get("meshes") or [])
    prims = sum(len(m.get("primitives") or []) for m in (j.get("meshes") or []))
    res["nodes"], res["meshes"], res["prims"] = nodes, meshes, prims
    if nodes > 400 or prims > 40:
        add("WARN", "规模", "%d 节点 / %d 网格 / %d 图元，桌宠常驻可能偏高"
            % (nodes, meshes, prims))
    else:
        add("PASS", "规模", "%d 节点 / %d 网格 / %d 图元" % (nodes, meshes, prims))

    res["verdict"] = "可部署" if res["fail"] == 0 else "不可部署"
    return res


def print_human(res):
    print("")
    print("=" * 68)
    print("%s  (%s)" % (res["file"], res.get("version", "-")))
    print("=" * 68)
    for c in res["checks"]:
        print("[%-4s] %-10s %s" % (c["level"], c["item"], c["detail"]))
    print("-" * 68)
    print("结论: %s   (FAIL %d / WARN %d)"
          % (res.get("verdict"), res["fail"], res["warn"]))
    if res.get("custom") is not None and not res["custom"]:
        print("提示: 若需要细腻表情，用 Blender 加 ShapeKey 并注册为 custom expression")


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    as_json = "--json" in argv
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not args:
        _fail("用法: python check_vrm.py <模型.vrm> [更多.vrm ...] [--json]")

    results = []
    for p in args:
        if not os.path.isfile(p):
            _fail("文件不存在: %s" % p)
        results.append(check_one(p, as_json))

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print_human(r)

    return 0 if all(r["fail"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
