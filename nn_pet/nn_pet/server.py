# -*- coding: utf-8 -*-
"""PetServer — Flask 后端总装。

路由薄层：handler 只搬运参数，业务在组件类方法里。
端点与原 xiaoyun_server.py 对齐（子集）：/api/chat /api/last /api/history
/api/health /api/preset /api/pet_state /api/event /api/voice/*。
"""
import base64
import json
import os
import sys
import threading
import time

# 允许直接以脚本方式运行（python nn_pet/server.py）：先把包父目录挂进 sys.path
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if __package__ in (None, "") and _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)
    __package__ = "nn_pet"  # noqa: A001

from flask import Flask, jsonify, request
from flask_cors import CORS

from .brain import BrainClient
from .config import AppConfig
from .pipeline import ReplyPipeline
from .tts import TTSEngine
from .voice import HistoryStore, VoiceSession


class PetServer:
    """桌宠后端。组成：AppConfig → Brain/TTSEngine → Stores → Flask routes。"""

    def __init__(self, project_root):
        self.cfg = AppConfig(project_root)
        self.brain = BrainClient(self.cfg)
        self.tts = TTSEngine(self.cfg)
        self.history = HistoryStore(self.cfg.max_history)
        self.voice = VoiceSession()
        self.pipeline = ReplyPipeline(
            self.cfg, self.brain, self.tts, self.history, self.voice)
        self.pet_alive_ts = 0.0
        self.pet_visible = True
        self.started_at = time.time()

        self.app = Flask(
            self.__class__.__name__,
            static_folder=os.path.join(self.cfg.luna_root, "Live2D"),
            static_url_path="")
        CORS(self.app)
        self._register_routes()

    # ------------------------------------------------------------------
    def _register_routes(self):
        app = self.app
        app.route("/api/health")(self.api_health)
        app.route("/api/chat", methods=["POST"])(self.api_chat)
        app.route("/api/last")(self.api_last)
        app.route("/api/history")(self.api_history)
        app.route("/api/preset", methods=["GET", "POST"])(self.api_preset)
        app.route("/api/pet_state", methods=["POST"])(self.api_pet_state)
        app.route("/api/event", methods=["POST"])(self.api_event)
        app.route("/api/voice/state")(self.api_voice_state)
        app.route("/api/voice/start", methods=["POST"])(self.api_voice_start)
        app.route("/api/voice/playing", methods=["POST"])(self.api_voice_playing)
        app.route("/api/voice/resume", methods=["POST"])(self.api_voice_resume)
        app.route("/api/voice/tts", methods=["POST"])(self.api_voice_tts)

    # ---- 状态 ----
    def api_health(self):
        self.brain.health()
        return jsonify({
            "ok": True,
            "port": self.cfg.port,
            "uptime_s": round(time.time() - self.started_at, 1),
            "brain_online": self.brain.online,
            "model": self.cfg.model,
            "engine": self.cfg.tts_engine,
            "preset_key": self.cfg.preset.get("key"),
            "pet_visible": self.pet_visible,
        })

    # ---- 对话 ----
    def api_chat(self):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "empty text"}), 400
        result = self.pipeline.turn(text)
        if result.get("error"):
            return jsonify({"ok": False, "error": result["error"]}), 502
        audio = self.tts.synthesize(result["text"], result.get("emotion"))
        audio_b64 = base64.b64encode(audio).decode() if audio else ""
        return jsonify({
            "ok": True,
            "reply": result["text"],
            "seq": result["seq"],
            "emotion": result.get("emotion") or "",
            "audio": "data:audio/mp3;base64," + audio_b64 if audio_b64 else "",
        })

    def api_last(self):
        since = request.args.get("since", 0, type=int)
        item = self.history.last(since)
        self.pet_alive_ts = time.time()
        if not item:
            return jsonify({"ok": True, "fresh": False})
        claimed = self.history.claim_playback(item["seq"])
        return jsonify({"ok": True, "fresh": True,
                        "seq": item["seq"], "reply": item["text"],
                        "play_here": claimed})

    def api_history(self):
        return jsonify({"ok": True, "items": self.history.messages()})

    # ---- 人设 ----
    def api_preset(self):
        if request.method == "GET":
            presets = self.cfg.list_presets()
            return jsonify({"ok": True, "current": self.cfg.preset.get("key"),
                            "presets": [{"key": k, "label": v}
                                        for k, v in presets]})
        body = request.get_json(silent=True) or {}
        key = body.get("key", "")
        preset = self.cfg.load_preset(key)
        if preset.get("key") == key:
            self.cfg.preset = preset
            return jsonify({"ok": True, "current": key})
        return jsonify({"ok": False, "error": "preset not found"}), 404

    # ---- 壳/页面状态 ----
    def api_pet_state(self):
        body = request.get_json(silent=True) or {}
        if "visible" in body:
            self.pet_visible = bool(body["visible"])
        return jsonify({"ok": True})

    def api_event(self):
        body = request.get_json(silent=True) or {}
        # 前端事件日志：原版写 前端事件日志.txt，这里保留钩子（可接 evlog）
        return jsonify({"ok": True})

    # ---- 语音时序 ----
    def api_voice_state(self):
        snap = self.voice.snapshot()
        snap["ok"] = True
        snap["pet_online"] = (time.time() - self.pet_alive_ts) < 6
        return jsonify(snap)

    def api_voice_start(self):
        ok, reason = self.voice.start()
        if not ok:
            return jsonify({"ok": False, "reason": reason}), 425
        return jsonify({"ok": True, "mode": self.voice.mode})

    def api_voice_playing(self):
        body = request.get_json(silent=True) or {}
        self.voice.report_playing(body.get("seq", 0), body.get("dur", 0))
        return jsonify({"ok": True})

    def api_voice_resume(self):
        body = request.get_json(silent=True) or {}
        seq = body.get("seq", 0)
        if self.voice.busy:
            # 委派：收尾闸（start+dur+0.8）负责真正恢复（原 delegated 行为）
            return jsonify({"ok": True, "ignored": "busy", "delegated": True})
        if seq and seq != self.voice.last_seq and self.voice.state != "playing":
            return jsonify({"ok": True, "ignored": "stale_seq"})
        self.voice.resume_ok()
        return jsonify({"ok": True})

    def api_voice_tts(self):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        audio = self.tts.synthesize(text)
        if not audio:
            return jsonify({"ok": False, "error": "tts failed"}), 502
        return jsonify({"ok": True,
                        "audio": "data:audio/mp3;base64,"
                                 + base64.b64encode(audio).decode()})

    # ------------------------------------------------------------------
    def run(self):
        self.brain.health()
        self.app.run(host="127.0.0.1", port=self.cfg.port,
                     threaded=True, debug=False, use_reloader=False)


def create_app(project_root):
    """WSGI 工厂（便于 Flask test_client 测试）。"""
    return PetServer(project_root).app


if __name__ == "__main__":
    # project_root = nn_pet 包所在目录的上一级（即 xiaoyun/ 的等价物 = nn_pet/）
    PetServer(os.path.dirname(_PKG_PARENT)).run()
