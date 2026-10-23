# -*- coding: utf-8 -*-
"""临时起一个后端（8095）跑前端眼神测试，命令结束即退。

为什么不用现成的 8090：桌面壳不一定在跑；而 agent 拉起的进程活不过回合，
所以必须在**同一条命令**里完成"起服务 -> 跑 node 测试 -> 退出"。
"""
import os, sys, threading, subprocess, time, socket, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import xiaoyun_server as S

PORT = int(os.environ.get("GAZE_TEST_PORT") or 8095)
NODE = r"C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"


def alive(port, timeout=0.4):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def serve():
    S.app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False, debug=False)


th = threading.Thread(target=serve, daemon=True)
th.start()

for _ in range(80):
    if alive(PORT):
        break
    time.sleep(0.25)
else:
    print("FATAL 后端没起来")
    sys.exit(2)
print("服务就绪 http://127.0.0.1:%d/" % PORT)

# 可选指定测哪个脚本（默认眼神；poke_front_test.mjs 复用同一套临时服务）
SCRIPT = os.environ.get("TEST_SCRIPT") or (sys.argv[1] if len(sys.argv) > 1
                                           else "tools/gaze_front_test.mjs")
rc = subprocess.call([NODE, SCRIPT, "http://127.0.0.1:%d/desktoppmx" % PORT])
print("\n[serve_test] node exit=%d" % rc)
sys.exit(rc)
