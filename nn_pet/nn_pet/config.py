# -*- coding: utf-8 -*-
"""AppConfig — 桌宠配置加载。

合并三层来源（优先级从高到低）：
1. 环境变量（XIAOYUN_PORT / XIAOYUN_TTS_ENGINE / MINIMAX_API_KEY ...）
2. 人设 JSON（人设/<key>.json，含 preset 文本与声线参数）
3. runtime/app/config.json（上游 Luna 配置：模型/音色/温度）
"""
import json
import os


class AppConfig:
    """集中式配置容器。实例化时读盘，之后只读。"""

    def __init__(self, project_root):
        # project_root = nn_pet 包所在目录（即 xiaoyun/ 的等价物）
        self.root = os.path.normpath(project_root)
        # 上游 Luna 配置目录（config.json + Live2D），对应原 LUNA_ROOT
        self.luna_root = os.path.normpath(
            os.path.join(self.root, os.pardir, "runtime", "app"))
        self.config_path = os.path.join(self.luna_root, "config.json")
        self.preset_dir = os.path.join(self.root, "人设")
        self.data_dir = os.path.join(self.root, "data")

        self.port = int(os.environ.get("XIAOYUN_PORT", "8090"))
        self.max_history = 24  # 最近 24 条（12 轮）

        # ---- 对话大脑（or_proxy，OpenAI 兼容）----
        self.brain_base = os.environ.get(
            "XIAOYUN_BRAIN_BASE", "http://127.0.0.1:8787/v1")
        self.brain_key = os.environ.get("XIAOYUN_BRAIN_KEY", "sk-local")
        self.model = "auto"

        # ---- TTS ----
        self.tts_engine = os.environ.get("XIAOYUN_TTS_ENGINE", "volc")
        self.volc_speaker = os.environ.get(
            "XIAOYUN_VOLC_SPEAKER", "zh_female_vv_uranus_bigtts")
        self.volc_speech_rate = int(os.environ.get("XIAOYUN_VOLC_RATE", "12"))
        self.edge_voice = "zh-CN-XiaoyiNeural"

        self._raw_luna = self._load_luna()
        self._apply_luna()
        self.preset = self.load_preset(self._current_preset_key())

    # ------------------------------------------------------------------
    def _load_luna(self):
        try:
            with open(self.config_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _apply_luna(self):
        c = self._raw_luna
        chat = c.get("chatgpt", {})
        openai = c.get("openai", {})
        self.model = chat.get("model") or openai.get("model") or "auto"
        self.temperature = chat.get("temperature", 0.8)
        self.max_tokens = chat.get("max_tokens", 600)
        edge = c.get("edge-tts", {})
        self.edge_voice = edge.get("voice", self.edge_voice)
        self.edge_rate = edge.get("rate", "+0%")
        self.edge_pitch = edge.get("pitch", "+0Hz")
        self.edge_volume = edge.get("volume", "+0%")
        if openai.get("api"):
            self.brain_base = openai["api"].rstrip("/")

    def _current_preset_key(self):
        try:
            with open(os.path.join(self.preset_dir, "current.txt"),
                      encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def load_preset(self, key):
        """按 key 读人设 JSON；失败返回内置兜底。"""
        if key:
            path = os.path.join(self.preset_dir, key + ".json")
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("preset"):
                    data.setdefault("key", key)
                    return data
            except Exception:
                pass
        return {
            "key": "default",
            "label": "默认",
            "preset": "你叫念念，27 岁，性格自然真实，回答简短口语化。",
        }

    def list_presets(self):
        """扫描人设目录，返回 [(key, label), ...]。"""
        out = []
        try:
            for name in os.listdir(self.preset_dir):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.preset_dir, name),
                              encoding="utf-8") as fh:
                        meta = json.load(fh)
                    if meta.get("preset"):
                        out.append((name[:-5], meta.get("label", name[:-5])))
                except Exception:
                    continue
        except OSError:
            pass
        return out

    def save_model_choice(self, model):
        """把对话模型选择写回 runtime/app/config.json（原 /api/chat-model 行为）。"""
        try:
            c = self._raw_luna
            c.setdefault("chatgpt", {})["model"] = model
            c.setdefault("openai", {})["model"] = model
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(c, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
            self.model = model
            return True
        except Exception:
            return False
