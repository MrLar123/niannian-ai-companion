# -*- coding: utf-8 -*-
"""EmbedClient 降级逻辑回归测试（mock Ollama，不依赖真实服务/显卡）。

覆盖三条分支：
  1. GPU 请求失败（显存被占）-> 用 num_gpu=0 重试成功 -> 记 cpu_fallback
  2. GPU/CPU 都失败 -> 返回 None，且 available() 变 False（调用方退化 FTS5）
  3. 回落满 gpu_retry_secs 后 -> 自动重新试 GPU 并复归（不会永久留在慢路径）

用法：python tools/test_embed_fallback.py
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "D:/LunaAI/xiaoyun")
import xiaoyun_server as S          # noqa: E402

CALLS = []          # 记录每次请求里的 num_gpu，用于断言实际走了 GPU 还是 CPU
FAIL_FIRST_GPU = [True]   # 是否让"第一个 GPU 请求"失败

ok = True
def check(name, cond):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        ok = False


class MockOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        ng = (body.get("options") or {}).get("num_gpu")
        CALLS.append(ng)
        gpu_calls = [c for c in CALLS if c != 0]
        if ng != 0 and FAIL_FIRST_GPU[0] and len(gpu_calls) == 1:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"memory allocation failed"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"embeddings": [[0.1] * 8]}).encode())


class AlwaysFail(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.send_response(500)
        self.end_headers()


def serve(handler, port):
    srv = HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


serve(MockOllama, 18099)
serve(AlwaysFail, 18098)

print("[1] GPU 失败 -> CPU 回落")
c = S.EmbedClient("http://127.0.0.1:18099", backend="ollama", num_gpu=99, keep_alive="30m")
check("初始 gpu_state=gpu", c.gpu_state == "gpu")
v = c.embed(["测试"])
check("CPU 重试成功返回向量", bool(v) and len(v[0]) == 8)
check("请求序列 num_gpu=[99, 0]", CALLS == [99, 0])
check("gpu_state 记为 cpu_fallback", c.gpu_state == "cpu_fallback")
check("dim 记录正确", c.dim == 8)
check("available()=True", c.available())

print("[2] 两端全失败 -> 安静降级")
c2 = S.EmbedClient("http://127.0.0.1:18098", backend="ollama", num_gpu=99)
check("返回 None", c2.embed(["x"]) is None)
check("available()=False", c2.available() is False)

print("[3] 回落到期 -> 自动复归 GPU")
CALLS.clear()
c3 = S.EmbedClient("http://127.0.0.1:18099", backend="ollama", num_gpu=99, gpu_retry_secs=0)
c3.embed(["第一次"])        # GPU 500 -> CPU 成功
check("首次回落 cpu_fallback", c3.gpu_state == "cpu_fallback")
FAIL_FIRST_GPU[0] = False   # 显存已释放：之后 GPU 应能成功（模拟 3D 跑完）
c3.embed(["第二次"])        # 窗口=0，立刻重试 GPU
check("到期后复归 gpu", c3.gpu_state == "gpu")
check("复归后请求走 num_gpu=99", CALLS[-1] == 99)

print("结果:", "全部通过" if ok else "有失败")
sys.exit(0 if ok else 1)
