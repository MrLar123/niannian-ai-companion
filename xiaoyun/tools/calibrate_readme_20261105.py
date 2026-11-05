# -*- coding: utf-8 -*-
"""主 README 事实校验（2026-11-05 重写版）。

背景：README.md 已于 2026-11-05 重写为产品向精简版（原 598 行技术手册拆到 docs/），
第一版的 43 条定点替换规则随之失效。本脚本职责改为**事实校验**：
  1) 新 README 必须包含的关键实况数字/链接都在；
  2) 已被推翻的旧数字不得回流（防止手工编辑时从旧文档复制粘贴回来）。

用法：
  python calibrate_readme_20261105.py          # 校验，发现问题退出码 1
  python calibrate_readme_20261105.py --check  # 同上（保留兼容，历史命令引用）
"""
import io
import os
import sys

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "README.md")

# (说明, 必须存在的字符串)
MUST_HAVE = [
    ("产品定位", "桌面 AI 伴侣"),
    ("形象数", "31 套"),
    ("音色数", "21 种音色"),
    ("人设数", "19 套人格"),
    ("舞蹈数", "19 套 MMD 舞蹈"),
    ("表情标定", "248 格"),
    ("语音首响", "0.45"),
    ("反问闭环", "你说的是甘雨吗"),
    ("宁问不猜", "绝不直接改状态"),
    ("零明文凭证", "零明文"),
    ("实况索引链接", "docs/00-文档索引与当前实况.md"),
    ("变更纪要链接", "docs/18-2026-11 变更纪要.md"),
    ("速查表链接", "docs/语音指令速查表.md"),
    ("快速开始脚本", "启动小芸桌面.bat"),
    ("诊断脚本", "诊断小芸桌面.bat"),
    ("or_proxy 说明", "or_proxy/README_OR_PROXY.md"),
    ("隐私承诺", "无遥测"),
]

# 旧快照的错误数字/表述，出现即失败（空格归一化后匹配）
STALE = [
    "15 PMX", "13 VRM", "66 个端点", "4174", "2488",
    "0.0010", "K=1.3", "内置标准 Key", "内置兜底 Key",
    "58 项", "32 项单元", "test_nn_pet.py    # 32",
]


def norm(s):
    return " ".join(s.split())


def main():
    if not os.path.isfile(TARGET):
        print("!! 找不到 %s" % TARGET)
        return 1
    text = norm(io.open(TARGET, encoding="utf-8").read())

    missing = [name for name, needle in MUST_HAVE if norm(needle) not in text]
    stale = [pat for pat in STALE if norm(pat) in text]

    if missing:
        print("README 缺少关键内容：")
        for m in missing:
            print("  - " + m)
    if stale:
        print("README 出现已推翻的旧数字/旧表述：")
        for s in stale:
            print("  - " + s)
    if not missing and not stale:
        print("README 事实校验通过：%d 项关键内容齐全，无旧数字回流"
              % len(MUST_HAVE))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
