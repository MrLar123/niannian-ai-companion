# 扫描 VMD 动作里的"眼部资产"：眼骨轨道 + 眼部表情(morph)
# 决定眼神追随与动作素材的冲突面：动作自带眼骨轨道 → 我们的覆盖会改写动作表演；
# 动作自带眨眼/闭眼 morph → 眼球在动但被眼皮挡住（观感上"没生效"）。
import struct, sys, os, glob

EYE_BONES = ('目', '両目', 'eye', 'Eye')
EYE_MORPHS = ('まばたき', '笑い', 'ウィンク', 'じと目', '瞳', '目')

def scan(path):
    with open(path, 'rb') as f:
        d = f.read()
    if not d.startswith(b'Vocaloid Motion Data'):
        return None
    off = 30 + 20
    n, = struct.unpack_from('<I', d, off); off += 4
    bones = {}
    for _ in range(n):
        name = d[off:off + 15].split(b'\x00')[0].decode('shift_jis', 'replace')
        off += 15 + 4 + 12 + 16 + 64
        bones[name] = bones.get(name, 0) + 1
    mn, = struct.unpack_from('<I', d, off); off += 4
    morphs = {}
    for _ in range(mn):
        name = d[off:off + 15].split(b'\x00')[0].decode('shift_jis', 'replace')
        off += 15 + 4 + 4
        morphs[name] = morphs.get(name, 0) + 1
    eye_b = {k: v for k, v in bones.items() if any(e in k for e in EYE_BONES)}
    eye_m = {k: v for k, v in morphs.items() if any(e in k for e in EYE_MORPHS)}
    return bones, morphs, eye_b, eye_m

for p in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else 'assets/motions/*.vmd')):
    r = scan(p)
    if not r:
        print('SKIP', os.path.basename(p)); continue
    bones, morphs, eb, em = r
    print('%-24s 骨轨道=%-4d 眼骨=%-22s | morph=%-3d 眼部=%s' % (
        os.path.basename(p), len(bones),
        (','.join('%s×%d' % (k, v) for k, v in eb.items()) or '无'),
        len(morphs),
        (','.join('%s×%d' % (k, v) for k, v in em.items()) or '无')))
