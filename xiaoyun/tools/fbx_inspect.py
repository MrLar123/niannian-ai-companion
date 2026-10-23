# -*- coding: utf-8 -*-
"""批量体检 FBX：骨骼、网格、材质、动画统计。输出纯 ASCII 报告。"""
import sys, os, json

import bpy

def clean_ascii(s):
    try:
        s.encode('ascii')
        return s
    except UnicodeEncodeError:
        return "<non-ascii>"

def inspect(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(path)[1].lower()
    if ext == '.fbx':
        try:
            bpy.ops.import_scene.fbx(filepath=path)
        except Exception as e:
            return {"error": "import_failed: %s" % clean_ascii(str(e))[:100]}
    elif ext in ('.glb', '.gltf'):
        try:
            bpy.ops.import_scene.gltf(filepath=path)
        except Exception as e:
            return {"error": "import_failed: %s" % clean_ascii(str(e))[:100]}
    else:
        return {"error": "unsupported"}

    armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    anims = [a for a in bpy.data.actions]

    # 采样 armature 骨骼名
    bone_names = []
    if armatures:
        for b in armatures[0].data.bones:
            bone_names.append(b.name)

    # humanoid 命中估计（常见 Mixamo/VRM/Unity 命名）
    low = [n.lower() for n in bone_names]
    hits = [k for k in ('hips','spine','head','lefthand','righthand','leftupleg','rightupleg',
                        'upperarm','forearm','leftfoot','rightfoot','neck','shoulder')
            if any(k in n for n in low)]

    # 材质/贴图统计
    mats = []
    for m in bpy.data.materials:
        tex_count = 0
        if m.use_nodes:
            for n in m.node_tree.nodes:
                if n.type == 'TEX_IMAGE':
                    tex_count += 1
        mats.append({"name": clean_ascii(m.name)[:40], "image_nodes": tex_count})

    # 顶点数统计
    total_verts = sum(len(o.data.vertices) for o in meshes if o.data)

    return {
        "armatures": len(armatures),
        "bones": len(bone_names),
        "humanoid_hits": hits,
        "meshes": len(meshes),
        "materials": mats,
        "actions": len(anims),
        "verts": total_verts,
    }

out = {}
for p in sys.argv[sys.argv.index('--') + 1:]:
    key = os.path.basename(p)
    try:
        out[key] = inspect(p)
    except Exception as e:
        out[key] = {"error": clean_ascii(str(e))[:120]}

with open(r"D:\LunaAI\xiaoyun\素材\_fbx_report.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("REPORT_DONE")
