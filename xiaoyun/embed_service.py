#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
念念 · 本地 Embedding 服务（长期记忆的语义检索）

端口 8092。只做一件事：文本 -> 512 维 L2 归一化向量（bge-small-zh-v1.5）。
与 or_proxy（对话）/ ASR / TTS 三条链路完全解耦 —— 本服务不参与对话、
识别、合成，只给记忆库提供"语义命中"能力。它没起来时，后端自动降级为
纯 FTS5 关键词检索（memory_store 的既有行为），她不会因此变笨或答不上话。

为什么值得单独一个服务：
  · 后端运行时是 Miniconda3 的 3.10（没有 torch），而模型要跑在 5050 上；
    torch cu128 在 hunyuan3d/venv 里已经就绪，直接复用，不另装环境。
  · 与 ASR 服务（8091）同构：独立进程、独立端口、可单独重启。

接口：
  GET  /health          模型与设备状态
  POST /embed           {"texts": [...], "mode": "doc"|"query"}
                        -> {"vectors": [[...]], "dim": 512, "count": N}

  mode=doc    存入记忆事实用（无指令前缀）
  mode=query  检索用（BGE 推荐的不对称检索指令前缀，提升召回）
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "127.0.0.1", int(os.environ.get("XIAOYUN_EMBED_PORT", "8092"))
MODEL_DIR = os.environ.get(
    "XIAOYUN_EMBED_MODEL", os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models', 'bge-small-zh-v1.5')))
DEVICE = os.environ.get("XIAOYUN_EMBED_DEVICE", "cuda")   # 失败自动降级 cpu
MAX_BATCH = 64            # 单次请求最多这么多条（桌宠场景一次 1~8 条）
MAX_CHARS = 512           # 截断长度（BERT 类模型 512 token 上限，中文 1 字≈1 token）

# BGE v1.5 不对称检索的查询侧指令（短查询 -> 长文档）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

MODEL = None
DEVICE_USED = None
_err = None
_lock = threading.Lock()   # encode 串行化：批量小，锁的代价可忽略


def load_model():
    """加载模型。GPU 初始化失败（没 torch/没卡/显存被占）自动降级 CPU。"""
    global MODEL, DEVICE_USED, _err
    from sentence_transformers import SentenceTransformer
    try:
        MODEL = SentenceTransformer(MODEL_DIR, device=DEVICE)
        DEVICE_USED = DEVICE
    except Exception as e:
        print("[embed] %s 加载失败，降级 CPU: %r" % (DEVICE, e))
        MODEL = SentenceTransformer(MODEL_DIR, device="cpu")
        DEVICE_USED = "cpu"
    # 预热一次，把首请求的延迟挪到启动时
    MODEL.encode(["预热"], normalize_embeddings=True)
    print("[embed] 模型就绪 device=%s dim=%d" % (DEVICE_USED, MODEL.get_sentence_embedding_dimension()))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):     # 静默默认访问日志，保持控制台干净
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json({
                "ok": MODEL is not None,
                "model": os.path.basename(MODEL_DIR),
                "device": DEVICE_USED,
                "dim": MODEL.get_sentence_embedding_dimension() if MODEL else 0,
                "error": _err,
            })
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/embed":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        texts = req.get("texts") or []
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            return self._json({"error": "texts must be a list of str"}, 400)
        if not texts:
            return self._json({"vectors": [], "dim": 0, "count": 0})
        if len(texts) > MAX_BATCH:
            return self._json({"error": "too many texts (max %d)" % MAX_BATCH}, 400)
        if MODEL is None:
            return self._json({"error": "model not ready"}, 503)

        mode = req.get("mode") or "doc"
        clean = [t[:MAX_CHARS] for t in texts]
        if mode == "query":
            clean = [QUERY_INSTRUCTION + t for t in clean]

        try:
            with _lock:
                vec = MODEL.encode(clean, normalize_embeddings=True,
                                   batch_size=min(32, len(clean)))
            return self._json({
                "vectors": [[round(float(x), 6) for x in row] for row in vec],
                "dim": int(vec.shape[1]),
                "count": int(vec.shape[0]),
            })
        except Exception as e:
            return self._json({"error": repr(e)}, 500)


def main():
    print("念念 Embedding 服务启动: http://127.0.0.1:%d（模型 %s，device %s）"
          % (PORT, MODEL_DIR, DEVICE))
    load_model()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("[embed] 监听 %s:%d" % (HOST, PORT))
    srv.serve_forever()


if __name__ == "__main__":
    main()
