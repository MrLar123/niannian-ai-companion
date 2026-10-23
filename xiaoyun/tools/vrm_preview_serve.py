# -*- coding: utf-8 -*-
"""One-shot VRM preview: temp backend (8096) + node headless shot, exits when done.

Same contract as gaze_serve_test.py: agent-spawned processes die with the
command, so server + test must live inside ONE command. /api/3dprobe lines
(vrmraw/vrmnorm/vrmloaded/vrmfail) are printed here via Flask stdout.
ASCII-only source (PS 5.1 GBK rule); the Chinese model filename is
percent-encoded in the URL below.
"""
import os, sys, threading, subprocess, time, socket, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import xiaoyun_server as S

PORT = int(os.environ.get("VRM_TEST_PORT") or 8096)
NODE = r"C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
# /assets/小芸.vrm  ->  percent-encoded, keeps this file pure ASCII
MODEL_ENC = "%E5%B0%8F%E8%8A%B8.vrm"
PAGE = "http://127.0.0.1:%d/bjd3dvr?model=/assets/%s" % (PORT, MODEL_ENC)
OUT = os.path.join(ROOT, "tools", "shots", "vrm_xiaoyun_preview.png")


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
    print("FATAL backend not up")
    sys.exit(2)
print("serving http://127.0.0.1:%d/  page=%s" % (PORT, PAGE))

rc = subprocess.call([NODE, "tools/vrm_preview.mjs", PAGE, OUT])
print("\n[serve_test] node exit=%d" % rc)
sys.exit(rc)
