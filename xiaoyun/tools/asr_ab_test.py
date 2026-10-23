# -*- coding: utf-8 -*-
"""A/B 实测：同一段真实中文语音，分别用【旧参数】和【新参数】转写，对比结果。
用于验证本次"识别不准"的修复是否真的生效。
用法: python tools\\asr_ab_test.py <wav文件>
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def load_wav(path):
    import wave
    w = wave.open(path, "rb")
    n = w.getnframes()
    sr = w.getframerate()
    ch = w.getnchannels()
    raw = w.readframes(n)
    w.close()
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        idx = np.linspace(0, a.size - 1, int(round(a.size * 16000.0 / sr)))
        a = np.interp(idx, np.arange(a.size), a).astype(np.float32)
    return a


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "ab_test.wav")
    if not os.path.exists(path):
        print("找不到音频:", path)
        return 1
    audio = load_wav(path)
    print("音频: %s  %.2fs  rms=%.5f  peak=%.5f"
          % (os.path.basename(path), len(audio) / 16000.0,
             float(np.sqrt(np.mean(audio * audio))), float(np.max(np.abs(audio)))))
    print("-" * 72)

    from faster_whisper import WhisperModel
    model_dir = os.path.join(ROOT, "models", "faster-whisper-small")
    m = WhisperModel(model_dir, device="cpu", compute_type="int8", cpu_threads=6)

    def run(tag, **kw):
        t0 = time.time()
        segs, info = m.transcribe(np.ascontiguousarray(audio, dtype=np.float32), **kw)
        text = "".join(s.text for s in segs).strip()
        took = time.time() - t0
        print("[%s] %.2fs" % (tag, took))
        print("   文本: %s" % (text or "(空)"))
        try:
            print("   质量: 语言=%s(%.2f) 对数概率=%.3f 无语音概率=%.3f 压缩比=%.3f"
                  % (info.language, info.language_probability, info.avg_logprob,
                     info.no_speech_prob, info.compression_ratio))
        except Exception:
            pass
        print()
        return text

    # ---- 旧参数（修复前）----
    run("旧", language="zh", beam_size=1, best_of=1, temperature=0.0,
        vad_filter=False, condition_on_previous_text=False,
        initial_prompt="以下是简体中文普通话的口语对话。")

    # ---- 新参数（修复后）----
    run("新", language="zh", beam_size=1, best_of=5,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        vad_filter=False, condition_on_previous_text=False,
        initial_prompt="你好，念念。跟你说个事儿。",
        log_prob_threshold=-0.80, compression_ratio_threshold=1.80,
        no_speech_threshold=0.60, repetition_penalty=1.15,
        suppress_blank=True, word_timestamps=False)

    # ---- 新参数 + 峰值归一化 ----
    import asr_service as A
    run("新+归一化", language="zh", beam_size=1, best_of=5,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        vad_filter=False, condition_on_previous_text=False,
        initial_prompt="你好，念念。跟你说个事儿。",
        log_prob_threshold=-0.80, compression_ratio_threshold=1.80,
        no_speech_threshold=0.60, repetition_penalty=1.15,
        suppress_blank=True, word_timestamps=False) if False else None
    na = A._peak_normalize(audio)
    t0 = time.time()
    segs, info = m.transcribe(np.ascontiguousarray(na, dtype=np.float32),
                              language="zh", beam_size=1, best_of=5,
                              temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                              vad_filter=False, condition_on_previous_text=False,
                              initial_prompt="你好，念念。跟你说个事儿。",
                              log_prob_threshold=-0.80,
                              compression_ratio_threshold=1.80,
                              no_speech_threshold=0.60,
                              repetition_penalty=1.15,
                              suppress_blank=True, word_timestamps=False)
    text = "".join(s.text for s in segs).strip()
    print("[新+归一化] %.2fs" % (time.time() - t0))
    print("   文本: %s" % (text or "(空)"))
    print("   质量: 对数概率=%.3f 无语音概率=%.3f 压缩比=%.3f"
          % (info.avg_logprob, info.no_speech_prob, info.compression_ratio))
    return 0


if __name__ == "__main__":
    sys.exit(main())
