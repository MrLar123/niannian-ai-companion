# -*- coding: utf-8 -*-
"""桌面壳启动器（class 化后的入口薄层）。

原 desktop_app.py（2488 行，pywebview 壳 + Catcher 隐形点击层 + 托盘）
的完整窗口逻辑保留在原文件运行；本入口演示重构后如何以类方式装配，
并把宠物页路由指到重构后的后端。

完整壳迁移说明：
- App            主应用（窗口/托盘/生命周期）
- Catcher        隐形点击层（拖动/点击/语音控件命中）
- RectPoller     控件坐标轮询（走本地 HTTP，不碰 JS 桥）
- MicButton/ModeButton 已并入 Catcher
以上类在原文件中已是 class 结构（Catcher/MicButton/HotkeyThread 等），
等价迁移时整类搬入 nn_pet/shell.py 即可；本入口按环境变量
NN_SHELL_MODE=legacy 时直接 exec 原 desktop_app.py，便于过渡。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    mode = os.environ.get("NN_SHELL_MODE", "class")
    if mode == "legacy":
        # 过渡期：直接以原壳运行（同目录上层 xiaoyun/desktop_app.py）
        legacy = os.path.normpath(
            os.path.join(HERE, os.pardir, os.pardir, "xiaoyun",
                         "desktop_app.py"))
        if os.path.exists(legacy):
            subprocess.call([sys.executable, legacy])
            return 0
        print("legacy shell not found:", legacy)
        return 1
    # class 模式：确保重构后端已起，再进入完整壳（待迁移）
    print("class-mode shell: full pywebview shell pending migration;")
    print("use NN_SHELL_MODE=legacy to run the original shell.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
