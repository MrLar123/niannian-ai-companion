# -*- coding: utf-8 -*-
"""故障复现 + 回归测试。
把干净语音劣化到 USB 麦的实测电平（RMS≈0.012、底噪≈0.0003），并构造
"纯静音段"（VAD 误触发时送进去的就是这种），对比旧/新参数是否还会编话。
用法: python tools\\asr_ab_test.py [wav]
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
SR = 16000

OLD_KW = dict(language="zh", beam_size=1, best_of=1, temperature=0.0,
              vad_filter=False, condition_on_previous_text=False,
              initial_prompt="以下是简体中文普通话的口语对话。")

NEW_KW = dict(language="zh", beam_size=1, best_of=5,
              temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
              vad_filter=False, condition_on_previous_text=False,
              initial_prompt="你好，念念。跟你说个事儿。",
              log_prob_threshold=-0.80, compression_ratio_threshold=1.80,
              no_speech_threshold=0.60, repetition_penalty=1.15,
              suppress_blank=True, word_timestamps=False)


def load_wav(path):
    import wave
    w = wave.open(path, "rb")
    sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        a = np.interp(np.linspace(0, a.size - 1, int(round(a.size * SR / sr))),
                      np.arange(a.size), a).astype(np.float32)
    return a


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tools", "ab_test.wav")
    if not os.path.exists(src):
        print("找不到:", src); return 1
    clean = load_wav(src)
    rng = np.random.default_rng(7)

    def degrade(a, target_rms=0.012, noise=0.0003):
        cur = float(np.sqrt(np.mean(a * a))) or 1e-9
        a = a * (target_rms / cur)
        return (a + rng.normal(0, noise, a.size)).astype(np.float32)

    cases = [
        ("干净原音", clean),
        ("劣化到USB麦电平", degrade(clean)),
        ("劣化+掐头去尾留2s", degrade(clean[SR:SR * 3])),
        ("纯底噪1.2s(VAD误触发)", (rng.normal(0, 0.0008, int(SR * 1.2))).astype(np.float32)),
        ("近似静音0.8s", (rng.normal(0, 0.0002, int(SR * 0.8))).astype(np.float32)),
    ]

    from faster_whisper import WhisperModel
    m = WhisperModel(os.path.join(ROOT, "models", "faster-whisper-small"),
                     device="cpu", compute_type="int8", cpu_threads=6)

    def run(audio, kw):
        t0 = time.time()
        segs, info = m.transcribe(np.ascontiguousarray(audio, dtype=np.float32), **kw)
        segs = list(segs)
        text = "".join(s.text for s in segs).strip()
        q = ""
        if segs:
            q = "对数概率=%.2f 无语音=%.2f 压缩比=%.2f" % (
                sum(s.avg_logprob for s in segs) / len(segs),
                max(s.no_speech_prob for s in segs),
                sum(s.compression_ratio for s in segs) / len(segs))
        else:
            q = "(无分段: 被无语音阈值跳过)"
        return text, time.time() - t0, q

    import asr_service as A
    bad_old = bad_new = 0
    for name, audio in cases:
        print("=" * 74)
        print("用例: %s   (%.2fs, rms=%.5f, peak=%.5f)"
              % (name, len(audio) / SR, float(np.sqrt(np.mean(audio * audio))),
                 float(np.max(np.abs(audio)))))
        to, _, qo = run(audio, OLD_KW)
        tn, _, qn = run(A._peak_normalize(audio), NEW_KW)
        if A._looks_like_hallucination(tn):
            tn_out = "%s  -> [已按幻觉丢弃]" % tn
        else:
            tn_out = tn
        print("  旧: %-44s %s" % (to or "(空)", qo))
        print("  新: %-44s %s" % (tn_out or "(空)", qn))
        # 判定：静音/底噪用例下，输出非空文字即为"编话"
        quiet = name.startswith("纯底噪") or name.startswith("近似静音")
        if quiet:
            if (to or "").strip():
                bad_old += 1; print("      ^ 旧参数【编话了】")
            if (tn or "").strip() and not A._looks_like_hallucination(tn):
                bad_new += 1; print("      ^ 新参数仍编话")
    print("=" * 74)
    print("静音类用例编话次数:  旧=%d  新=%d" % (bad_old, bad_new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
