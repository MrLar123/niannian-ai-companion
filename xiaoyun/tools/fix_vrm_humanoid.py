# -*- coding: utf-8 -*-
"""修正 avaturn_nn.vrm 的 humanoid 映射表（导出器写错：头/颈错位、左臂错一级、手指串位）。
按骨架真实节点名重建 VRMC_vrm.humanoid.humanBones，原地改写并备份原文件。
Usage: python tools/fix_vrm_humanoid.py <path-to-vrm>
"""
import json, struct, sys, os, shutil

VRM_BONES = ['hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
             'leftEye', 'rightEye', 'jaw',
             'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'leftToes',
             'rightUpperLeg', 'rightLowerLeg', 'rightFoot', 'rightToes',
             'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
             'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
             'leftThumbMetacarpal', 'leftThumbProximal', 'leftThumbDistal',
             'leftIndexProximal', 'leftIndexIntermediate', 'leftIndexDistal',
             'leftMiddleProximal', 'leftMiddleIntermediate', 'leftMiddleDistal',
             'leftRingProximal', 'leftRingIntermediate', 'leftRingDistal',
             'leftLittleProximal', 'leftLittleIntermediate', 'leftLittleDistal',
             'rightThumbMetacarpal', 'rightThumbProximal', 'rightThumbDistal',
             'rightIndexProximal', 'rightIndexIntermediate', 'rightIndexDistal',
             'rightMiddleProximal', 'rightMiddleIntermediate', 'rightMiddleDistal',
             'rightRingProximal', 'rightRingIntermediate', 'rightRingDistal',
             'rightLittleProximal', 'rightLittleIntermediate', 'rightLittleDistal']

# VRM 骨骼名 -> glTF 节点名（按 avaturn_nn.vrm 实际骨架）
MAP = {
    'hips': 'Hips', 'spine': 'Spine', 'chest': 'chest', 'upperChest': 'upperChest',
    'neck': 'Neck', 'head': 'Head',
    'leftEye': 'leftEye', 'rightEye': 'rightEye',
    'leftUpperLeg': 'leftUpperLeg', 'leftLowerLeg': 'leftLowerLeg',
    'leftFoot': 'LeftFoot', 'leftToes': 'leftToes',
    'rightUpperLeg': 'rightUpperLeg', 'rightLowerLeg': 'rightLowerLeg',
    'rightFoot': 'RightFoot', 'rightToes': 'rightToes',
    'leftShoulder': 'LeftShoulder', 'leftUpperArm': 'leftUpperArm',
    'leftLowerArm': 'leftLowerArm', 'leftHand': 'LeftHand',
    'rightShoulder': 'RightShoulder', 'rightUpperArm': 'rightUpperArm',
    'rightLowerArm': 'rightLowerArm', 'rightHand': 'RightHand',
    # 手指（骨架有 Proximal/Intermediate/Distal；VRM1 拇指只认 Metacarpal/Proximal/Distal）
    'leftThumbProximal': 'leftThumbProximal', 'leftThumbDistal': 'leftThumbDistal',
    'leftIndexProximal': 'leftIndexProximal', 'leftIndexIntermediate': 'leftIndexIntermediate',
    'leftIndexDistal': 'leftIndexDistal',
    'leftMiddleProximal': 'leftMiddleProximal', 'leftMiddleIntermediate': 'leftMiddleIntermediate',
    'leftMiddleDistal': 'leftMiddleDistal',
    'leftRingProximal': 'leftRingProximal', 'leftRingIntermediate': 'leftRingIntermediate',
    'leftRingDistal': 'leftRingDistal',
    'leftLittleProximal': 'leftLittleProximal', 'leftLittleIntermediate': 'leftLittleIntermediate',
    'leftLittleDistal': 'leftLittleDistal',
    'rightThumbProximal': 'rightThumbProximal', 'rightThumbDistal': 'rightThumbDistal',
    'rightIndexProximal': 'rightIndexProximal', 'rightIndexIntermediate': 'rightIndexIntermediate',
    'rightIndexDistal': 'rightIndexDistal',
    'rightMiddleProximal': 'rightMiddleProximal', 'rightMiddleIntermediate': 'rightMiddleIntermediate',
    'rightMiddleDistal': 'rightMiddleDistal',
    'rightRingProximal': 'rightRingProximal', 'rightRingIntermediate': 'rightRingIntermediate',
    'rightRingDistal': 'rightRingDistal',
    'rightLittleProximal': 'rightLittleProximal', 'rightLittleIntermediate': 'rightLittleIntermediate',
    'rightLittleDistal': 'rightLittleDistal',
}


def read_glb(path):
    data = open(path, 'rb').read()
    magic, ver, total = struct.unpack('<III', data[:12])
    assert magic == 0x46546C67, 'not glb'
    off = 12; chunks = []
    while off < total:
        ln, ty = struct.unpack('<II', data[off:off + 8])
        chunks.append((ty, data[off + 8:off + 8 + ln]))
        off += 8 + ln + ((4 - ln % 4) % 4)
    return chunks


def write_glb(path, chunks):
    out = bytearray()
    body = bytearray()
    for ty, buf in chunks:
        pad = (4 - len(buf) % 4) % 4
        body += struct.pack('<II', len(buf) + pad, ty)
        body += buf + (b' ' if ty == 0x4E4F534A else b'\x00') * pad
    total = 12 + len(body)
    out += struct.pack('<III', 0x46546C67, 2, total) + body
    open(path, 'wb').write(bytes(out))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r'D:/LunaAI/xiaoyun/assets/avaturn_nn.vrm'
    chunks = read_glb(path)
    js = json.loads([c for t, c in chunks if t == 0x4E4F534A][0].decode('utf-8'))
    names = [n.get('name', '') for n in js['nodes']]
    idx = {n: i for i, n in enumerate(names)}
    ext = js.get('extensions', {}).get('VRMC_vrm')
    if not ext:
        ext = js.get('extensions', {}).get('VRM')
    assert ext, 'no vrm extension'
    old = ext['humanoid']['humanBones']
    new = {}
    for b in VRM_BONES:
        node = MAP.get(b)
        if node and node in idx:
            new[b] = {'node': idx[node]}
    ext['humanoid']['humanBones'] = new
    # 对照打印
    print('--- before ---')
    for k in sorted(old):
        print('  %-22s' % k, names[old[k]['node']])
    print('--- after (%d bones) ---' % len(new))
    for k in sorted(new):
        print('  %-22s' % k, names[new[k]['node']])
    bak = path + '.bak_humanoid'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print('backup ->', bak)
    newchunks = []
    for ty, buf in chunks:
        if ty == 0x4E4F534A:
            newchunks.append((ty, json.dumps(js, ensure_ascii=False, separators=(',', ':')).encode('utf-8')))
        else:
            newchunks.append((ty, buf))
    write_glb(path, newchunks)
    print('written ->', path)


if __name__ == '__main__':
    main()
