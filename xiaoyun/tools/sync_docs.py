# -*- coding: utf-8 -*-
"""把散落三处的文档同步进 GitHub 仓库内的唯一发布副本。

    python tools/sync_docs.py            # 同步（只从真源 -> 发布副本，单向）
    python tools/sync_docs.py --check   # 只报告差异，不写文件

真源归属（**不要反向覆盖**）：
  D:\\LunaAI\\docs\\*.md                       根目录调研报告（工作副本，不在 git 仓库内）
  project-nn/xiaoyun/docs/语音指令速查表.md     由 tools/make_voice_cmd_sheet.py 生成，禁手改
  project-nn/xiaoyun/docs/语音指令路由调研报告.md
  project-nn/xiaoyun/docs/PMX表情体检报告.md   由 tools/make_emo_report.py 生成，禁手改
  project-nn/xiaoyun/docs/pmx_morphs.json

发布副本（会被本脚本覆盖，改文档改真源，别改这里）：
  project-nn/docs/                     编号文档 00~18 + 专题（唯一真源，手工维护）
  project-nn/docs/调研报告/             根目录 5 份大报告的仓库镜像

编号文档同时镜像回 xiaoyun/docs 中已存在的 5 份同名副本（单向，杜绝两边分叉）。

不同步：xiaoyun/docs/验收截图/（31 张 PNG 共 29MB，本地验收材料，进仓只增体积）。
"""
import io
import os
import shutil
import sys

XY = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.abspath(os.path.join(XY, ".."))
WORKSPACE = os.path.abspath(os.path.join(REPO, ".."))
PUB = os.path.join(REPO, "docs")
PUB_RESEARCH = os.path.join(PUB, "调研报告")
ROOT_DOCS = os.path.join(WORKSPACE, "docs")
XY_DOCS = os.path.join(XY, "docs")

# 真源 -> 发布副本
JOBS = [
    (os.path.join(ROOT_DOCS, f), os.path.join(PUB_RESEARCH, f))
    for f in sorted(os.listdir(ROOT_DOCS))
    if f.lower().endswith(".md")
]
JOBS += [
    (os.path.join(XY_DOCS, f), os.path.join(PUB, f))
    for f in ("语音指令路由调研报告.md", "语音指令速查表.md",
              "PMX表情体检报告.md", "pmx_morphs.json")
]

# 反向镜像：编号文档的真源在 project-nn/docs，xiaoyun/docs 里早年留了 5 份复制品。
# 只对「工作区已存在同名文件」做覆盖，避免凭空往工作区塞新文件，也杜绝两边内容分叉。
MIRROR_NAMES = ("16-人物调色盘设置.md", "17-3D建模历程与素材收集.md",
                "whalepal对比与优化清单.md", "口型同步-实现说明.md",
                "记忆语义检索-实现说明.md")
JOBS += [
    (os.path.join(PUB, f), os.path.join(XY_DOCS, f))
    for f in MIRROR_NAMES
    if os.path.isfile(os.path.join(XY_DOCS, f))
]

# 仓库外还散着一份早期版 README_OR_PROXY.md（旧版 99% 内容已在仓库版中），
# 一并镜像，避免根目录留一份过期文档误导人。
JOBS.append((os.path.join(REPO, "or_proxy", "README_OR_PROXY.md"),
             os.path.join(WORKSPACE, "README_OR_PROXY.md")))


def same(a, b):
    if not os.path.isfile(a) or not os.path.isfile(b):
        return False
    return open(a, "rb").read() == open(b, "rb").read()


def main():
    check = "--check" in sys.argv
    changed, missing, skipped = [], [], []
    for src, dst in JOBS:
        label = os.path.relpath(dst, REPO).replace("\\", "/")
        if not os.path.isfile(src):
            missing.append(label)
            continue
        if same(src, dst):
            skipped.append(label)
            continue
        changed.append(label)
        if not check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    print("同步方向: 各处真源 -> project-nn/docs；编号文档再镜像回 xiaoyun/docs（均单向）")
    print("更新 %d / 已一致 %d / 源缺失 %d" % (len(changed), len(skipped), len(missing)))
    for x in changed:
        print("  %s %s" % ("[待同步]" if check else "[已同步]", x))
    for x in missing:
        print("  [源缺失] %s" % x)
    if check and changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
