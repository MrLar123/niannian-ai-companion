# -*- coding: utf-8 -*-
"""预览/调色用的本地静态服务（127.0.0.1:8765）。

用途：桌面上「看XX.bat」这类单模型预览窗的后端 —— 只读、且极轻，不需要
启动整个小芸（8090）。它管三件事：

1. 静态文件：把 xiaoyun 目录当根，/assets/** 直接给 PMX 模型与 three.js 库。
2. 假接口：宠物页启动要的 /api/models、/api/motions、/api/voice/* 等，
   这里给"够用"的只读实现，**绝不写 assets/motions 里的任何东西**。
3. 调色：/api/grade 与正式后端同协议（页面两边通用），保存时只往
   models/<key>/model.json 的 params 里合并四个颜色键。

⚠ 与正式后端的差别（故意的）：
   - /api/model 不写 current.txt，预览窗切形象不影响桌面上的她；
   - /api/motion 只回 ok，不写动作 current.txt —— 预览里点舞蹈不会改变桌宠当前动作；
   - 不写事件日志，纯静态服务，进程退出不留痕迹。
"""
import os
import json
import http.server
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
MODEL_DIR = os.path.join(ASSETS, "models")
# 不在 models/ 下的历史模型（佩儿等）。与正式后端 MODEL_EXTRA_DIRS 保持一致，
# 否则预览窗会少一个形象、桌面「看XX.bat」也会漏生成。
MODEL_EXTRA_DIRS = [os.path.join(ASSETS, "peier")]
MOTION_DIR = os.path.join(ASSETS, "motions")
PORT = 8765

# 没有 model.json 的老模型靠这张表拿显示名与排序（照抄正式后端）
BUILTIN_MODEL_META = {
    "peier":     {"label": "姵儿（模之屋官方）", "params": {}, "order": 30},
    "ganyu":     {"label": "甘雨（原神）", "params": {}, "order": 30},
    "huangquan": {"label": "黄泉（星穹铁道）", "params": {}, "order": 30},
    "raiden":    {"label": "雷电将军（原神）", "params": {}, "order": 30},
    "kafka":     {"label": "卡芙卡（星穹铁道）", "params": {}, "order": 30},
    "qianxi":    {"label": "千咲（鸣潮 1.03）", "params": {}, "order": 30},
}

RES_SKIP_DIRS = {"textures", "texture", "tex", "toon", "spa", "spa2", "sub"}
PMX_SKIP_WORDS = ("weapon", "wepon", "sword", "spear", "bow", "staff", "prop", "武器", "刀", "剑")
GRADE_KEYS = ("contrast", "saturate", "gamma", "bright")
GRADE_DEF = {"contrast": 1.20, "saturate": 1.18, "gamma": 0.85, "bright": 1.00}
GRADE_LIM = {"contrast": (0.30, 3.00), "saturate": (0.30, 3.00),
             "gamma": (0.40, 1.60), "bright": (0.60, 1.60)}
GRADE_LIVE = {}
GRADE_SEQ = [0]
CURRENT = [""]          # 预览窗内的临时选择（不落盘）


# ---------------- 模型 / 动作清单（只读） ----------------
def _pick_pmx(model_dir):
    best, best_sz = None, -1
    for root, dirs, files in os.walk(model_dir):
        dirs[:] = [d for d in dirs if d.lower() not in RES_SKIP_DIRS]
        for fn in files:
            if not fn.lower().endswith(".pmx"):
                continue
            if any(w in fn.lower() for w in PMX_SKIP_WORDS):
                continue
            fp = os.path.join(root, fn)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz > best_sz:
                best, best_sz = fp, sz
    return best


def _models():
    out = []
    if os.path.isdir(MODEL_DIR):
        for key in sorted(os.listdir(MODEL_DIR)):
            d = os.path.join(MODEL_DIR, key)
            if not os.path.isdir(d):
                continue
            meta = {}
            mp = os.path.join(d, "model.json")
            if os.path.isfile(mp):
                try:
                    with open(mp, encoding="utf-8") as f:
                        meta = json.load(f) or {}
                except Exception:
                    meta = {}
            pmx = meta.get("pmx")
            if pmx:
                rel = os.path.join("models", key, *pmx.replace("\\", "/").split("/"))
                if not os.path.isfile(os.path.join(ASSETS, rel)):
                    pmx = None
            if not pmx:
                fp = _pick_pmx(d)
                if not fp:
                    continue
                rel = os.path.relpath(fp, ASSETS)
            base = BUILTIN_MODEL_META.get(key) or {}
            out.append({
                "key": key,
                "label": meta.get("label") or base.get("label") or key,
                "pmx": rel.replace("\\", "/"),
                "params": meta.get("params") or base.get("params") or {},
                "order": meta.get("order") or base.get("order") or 100,
            })
    # 散落在 assets/ 下的老目录（如 assets/peier/）—— 与正式后端一致
    for d in MODEL_EXTRA_DIRS:
        if not os.path.isdir(d):
            continue
        key = os.path.basename(d)
        fp = _pick_pmx(d)
        if not fp:
            continue
        base = BUILTIN_MODEL_META.get(key) or {}
        out.append({
            "key": key,
            "label": base.get("label") or key,
            "pmx": os.path.relpath(fp, ASSETS).replace("\\", "/"),
            "params": base.get("params") or {},
            "order": base.get("order") or 30,
        })
    out.sort(key=lambda m: (m.get("order", 100), m["label"]))
    return out


def _motions():
    out = []
    if not os.path.isdir(MOTION_DIR):
        return out
    for fn in sorted(os.listdir(MOTION_DIR)):
        if not fn.lower().endswith(".vmd"):
            continue
        key = os.path.splitext(fn)[0]
        meta = {}
        jp = os.path.join(MOTION_DIR, key + ".json")
        if os.path.isfile(jp):
            try:
                with open(jp, encoding="utf-8") as f:
                    meta = json.load(f) or {}
            except Exception:
                meta = {}
        out.append({"key": key, "label": meta.get("label") or key, "file": fn,
                    "url": "/assets/motions/" + fn, "note": meta.get("note") or ""})
    return out


# ---------------- 调色（与正式后端同协议） ----------------
def _meta_path(key):
    d = os.path.join(MODEL_DIR, key)
    if os.path.isdir(d):
        return os.path.join(d, "model.json")
    # 历史模型（assets/peier）：允许在它自己的目录里落一个 model.json 存颜色
    for ed in MODEL_EXTRA_DIRS:
        if os.path.basename(ed) == key and os.path.isdir(ed):
            return os.path.join(ed, "model.json")
    return os.path.join(MODEL_DIR, key, "model.json")


def _read_meta(key):
    p = _meta_path(key)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _grade_of(key):
    out = dict(GRADE_DEF)
    p = (_read_meta(key).get("params") or {})
    for k in GRADE_KEYS:
        v = p.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(float(v), 3)
    out.update({k: round(float(v), 3) for k, v in (GRADE_LIVE.get(key) or {}).items()
                if k in GRADE_KEYS})
    return out


def _clamp(raw):
    out = {}
    for k in GRADE_KEYS:
        if k not in raw:
            continue
        try:
            v = float(raw[k])
        except (TypeError, ValueError):
            continue
        lo, hi = GRADE_LIM[k]
        out[k] = round(min(hi, max(lo, v)), 3)
    return out


def _save_grade(key, params):
    path = _meta_path(key)
    if not os.path.isdir(os.path.dirname(path)):
        return False, "该模型不在 models/ 目录下，没有 model.json 可写"
    meta = _read_meta(key)
    p = dict(meta.get("params") or {})
    for k in GRADE_KEYS:
        if k in params:
            p[k] = params[k]
    meta.setdefault("key", key)
    meta["params"] = p
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return True, ""
    except Exception as e:
        return False, "写入失败: %r" % (e,)


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass

    # ---- 工具 ----
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        u = urlparse(self.path)
        return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

    def _cur_key(self):
        if CURRENT[0]:
            return CURRENT[0]
        lst = _models()
        return lst[0]["key"] if lst else ""

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---- GET ----
    def do_GET(self):
        path, q = self._query()
        if path in ("/desktoppmx", "/desktoppmx.html"):
            self.path = "/pages/desktoppmx.html"
        elif path in ("/pmxtune", "/pmxtune.html"):
            self.path = "/pages/pmxtune.html"
        elif path.startswith("/desktopvrm"):
            self.path = "/pages/desktopvrm.html"
        elif path == "/api/models":
            return self.send_json({"ok": True, "current": self._cur_key(), "auto": True,
                                   "preview": True, "models": _models()})
        elif path == "/api/motions":
            return self.send_json({"ok": True, "current": "idle",
                                   "idle_label": "内置待机（呼吸/歪头/挥手）",
                                   "motions": _motions()})
        elif path == "/api/grade":
            key = (q.get("model") or "").strip() or self._cur_key()
            return self.send_json({"ok": True, "model": key, "seq": GRADE_SEQ[0],
                                   "defaults": GRADE_DEF, "limits": GRADE_LIM,
                                   "params": _grade_of(key)})
        elif path == "/api/last":
            return self.send_json({"fresh": False, "seq": 0})
        elif path == "/api/history":
            return self.send_json({"seq": 0, "history": []})
        elif path == "/api/voice/state":
            return self.send_json({"ok": True, "asr_online": False, "shell_online": False,
                                   "state": "idle", "mode": "vad", "recording": False})
        elif path == "/api/3dprobe":
            print("PROBE:", self.path[14:200], flush=True)
            return self.send_json({"ok": True})
        return super().do_GET()

    # ---- POST ----
    def do_POST(self):
        path, _ = self._query()
        body = self._body()
        if path == "/api/grade":
            key = str(body.get("model") or "").strip() or self._cur_key()
            patch = _clamp(body.get("params") or {k: body.get(k) for k in GRADE_KEYS})
            if not patch:
                return self.send_json({"ok": False, "error": "没有可用的颜色参数"}, 400)
            cur = _grade_of(key)
            cur.update(patch)
            GRADE_LIVE[key] = cur
            GRADE_SEQ[0] += 1
            saved, err = False, ""
            if body.get("save"):
                saved, err = _save_grade(key, cur)
            return self.send_json({"ok": True, "model": key, "seq": GRADE_SEQ[0],
                                   "params": cur, "saved": saved, "error": err or None})
        if path == "/api/model":
            k = str(body.get("key") or "")
            if any(m["key"] == k for m in _models()):
                CURRENT[0] = k
                return self.send_json({"ok": True, "current": k, "preview": True})
            return self.send_json({"ok": False, "error": "没有这个模型"}, 404)
        if path == "/api/motion":
            # 预览窗内切动作不落盘：桌面上的她当前动作不受影响
            return self.send_json({"ok": True, "current": str(body.get("key") or "idle"),
                                   "preview": True})
        return self.send_json({"ok": True, "preview": True})


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
