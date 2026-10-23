# -*- coding: utf-8 -*-
"""把舞蹈截图转成 ASCII 轮廓图，用于在无图像通道时定量/定性判断姿态：
   - 每行宽度剖面（能看出头/肩/腰/裙的宽度变化）
   - 连通域数量（>1 = 身体断裂，例如腰断/肢体分离）
   - 左右对称性（左右半边非透明像素比）
Usage: python tools/ascii_pose.py <png> [cols] [rows]
"""
import sys
from PIL import Image


def main():
    path = sys.argv[1]
    CW = int(sys.argv[2]) if len(sys.argv) > 2 else 46
    CH = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    im = Image.open(path).convert('RGB')
    w, h = im.size
    px = im.load()
    # 背景色 = 四角像素均值（截图背景不透明，不能用 alpha 判前景）
    corners = [px[2, 2], px[w - 3, 2], px[2, h - 3], px[w - 3, h - 3]]
    bg = tuple(sum(c[i] for c in corners) / 4 for i in range(3))

    def isfg(x, y):
        p = px[x, y]
        return (abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2])) > 42
    # 降采样：每格内非透明像素占比
    grid = []
    for r in range(CH):
        row = []
        for c in range(CW):
            x0, x1 = int(c * w / CW), max(int(c * w / CW) + 1, int((c + 1) * w / CW))
            y0, y1 = int(r * h / CH), max(int(r * h / CH) + 1, int((r + 1) * h / CH))
            n = tot = 0
            for y in range(y0, y1, 2):
                for x in range(x0, x1, 2):
                    tot += 1
                    if isfg(x, y):
                        n += 1
            row.append(n / max(tot, 1))
        grid.append(row)
    chars = ' .:-=+*#%@'
    print('== ' + path)
    for row in grid:
        print(''.join(chars[min(9, int(v * 9.99))] for v in row))
    # 连通域（4 邻域，阈值 0.35）
    thr = 0.35
    seen = [[False] * CW for _ in range(CH)]
    comps = []

    def dfs(r, c):
        stack = [(r, c)]; n = 0
        while stack:
            rr, cc = stack.pop()
            if rr < 0 or rr >= CH or cc < 0 or cc >= CW: continue
            if seen[rr][cc] or grid[rr][cc] < thr: continue
            seen[rr][cc] = True; n += 1
            stack += [(rr + 1, cc), (rr - 1, cc), (rr, cc + 1), (rr, cc - 1)]
        return n
    for r in range(CH):
        for c in range(CW):
            if grid[r][c] >= thr and not seen[r][c]:
                comps.append(dfs(r, c))
    comps = sorted(comps, reverse=True)
    print('components=%d sizes=%s' % (len(comps), comps[:5]))
    left = sum(grid[r][c] for r in range(CH) for c in range(CW // 2))
    right = sum(grid[r][c] for r in range(CH) for c in range(CW // 2, CW))
    print('L/R mass %.2f / %.2f  ratio=%.2f' % (left, right, left / max(right, 0.01)))


if __name__ == '__main__':
    main()
