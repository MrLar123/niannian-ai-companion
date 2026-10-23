# -*- coding: utf-8 -*-
"""
句级流式语音合成 —— 方案 A：借 StreamVox 的**设计**，不借它的代码。

为什么要有这个文件
------------------
豆包合成 2.0 是单向流式接口：一次给全文，服务端分片吐音频。但 tts_mp3() 会等
全部分片收完才返回，do_chat 再整段塞进 data URI 交给前端 `new Audio(...)`。
于是"首包 0.7s"对用户毫无意义 —— 实测（前端事件日志.txt，52 条）每轮要等
首包中位 0.77s、总耗时中位 1.89s 才听到第一个字。

StreamVox 值得借的只有三个设计决定，全部与它的 GGUF 推理栈无关：
  ① 文本先按**句**切段，每段独立请求 —— 第一段一好就能开口，不必等全文；
  ② 单段内部**边收边吐**，不攒整段；
  ③ 逐块交裸 PCM（s16le），下游不需要解码器。
第 ③ 条豆包原生支持（audio_params.format="pcm" → 24kHz/16bit/单声道 s16le），
所以既不需要 StreamVox 那 232MB 的 wheel，也不需要它的 3.12 环境 ——
本模块只用 stdlib + requests，直接跑在后端现有的 3.10 解释器里。

契约
----
    stream_pcm(text, ...) -> Iterator[bytes]     # 每次 yield 一段 s16le/24k/单声道
    调用方按 (len(chunk) // 2 / SAMPLE_RATE) 就是这段的秒数。

★ 实测结论决定了切段策略（2026-10-19 本机 3 轮，60 次请求）：
  豆包的首包时间**与文本长度几乎无关** —— 2 字 0.41s / 14 字 0.50s /
  42 字 0.33s / 94 字 0.32s。真正的差别在**连接复用**：不复用 0.65~0.74s
  （每次重付 TLS 握手），复用后 0.32~0.39s。
  所以：
    ① 切段的目的**不是**抢首包，而是给超长回复分桶，避免一次请求拖太久；
    ② 绝不为"早开口"在逗号处切句 —— 白切，只会把一句拆成两段、
       凭空多出 0.6s 句间停顿（实测 3 段音频比整段长 1.3s）；
    ③ 单段内部本来就是流式分片（实测 54 字 = 25 片 / 2.1s），
       边收边吐就足以让第一块音频在 0.35s 落地。
  因此 max_len 默认放到 60：一两条句子的日常回复 = 一次请求，语调完整；
  只有真正长的回复才在**句号**处切开。
"""

import base64
import json
import re
import threading
import time
import uuid

import requests

SAMPLE_RATE = 24000
BYTES_PER_SEC = SAMPLE_RATE * 2          # 16bit 单声道

VOLC_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
VOLC_RESOURCE = "seed-tts-2.0"

# 句级停顿符 / 子句级停顿符（与 StreamVox 的默认集合一致，中文全角优先）
_SENT_STRONG = "。！？!?…\n"
_CLAUSE_STRONG = "，、；：,;:"
# 句末标点后紧跟的右引号/右括号，要跟着前一段走，不能飘到下一段开头
_CLOSERS = "”’」』）)】》\"'"


class VolcError(RuntimeError):
    """豆包这一路失败（HTTP/业务码/空音频）。上层据此决定是否回退。"""


# ============================ 文本处理 ============================

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2028-\u202f\u2060\ufeff]")
_MD_NOISE = re.compile(r"[`*#>]+")
_LIST_MARK = re.compile(r"(?m)^\s*(?:[-•·]|\d+[.、)])\s*")


def clean_text(text):
    """去掉对朗读有害的东西，但**保留全部停顿标点**（标点就是韵律）。"""
    if not text:
        return ""
    s = _ZERO_WIDTH.sub("", str(text))
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    s = _LIST_MARK.sub("", s)          # 列表符号不读
    s = _MD_NOISE.sub("", s)           # markdown 记号不读
    s = re.sub(r"[ \u3000]{2,}", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    # 压缩重复停顿标点（"。。。"/"！！！"），但"……"是中文省略号，保留原样
    s = re.sub(r"([。.！!？?，,；;：:、])\1+", r"\1", s)
    return s.strip()


def _clen(s):
    """有效字数（不含空白）。用字符数而非 token 数：中文 1 字≈1 token。"""
    return len(re.sub(r"\s+", "", s or ""))


def _split_sentences(text):
    """按句末标点切句，标点与随后的右引号都留在前一段。
    小数点/版本号里的 '.' 不切（3.14、1.5 这类不能被拆开念）。"""
    out, cur = [], ""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        cur += ch
        i += 1
        if ch in _SENT_STRONG:
            while i < n and text[i] in _SENT_STRONG:
                cur += text[i]
                i += 1
            while i < n and text[i] in _CLOSERS:
                cur += text[i]
                i += 1
            out.append(cur)
            cur = ""
        elif ch == ".":
            nxt = text[i] if i < n else ""
            if not nxt.isdigit():
                while i < n and text[i] in _SENT_STRONG:
                    cur += text[i]
                    i += 1
                while i < n and text[i] in _CLOSERS:
                    cur += text[i]
                    i += 1
                out.append(cur)
                cur = ""
    if cur.strip():
        out.append(cur)
    return out


def _split_clauses(text):
    """按逗号/顿号/分号切子句，标点留在前一段。"""
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in _CLAUSE_STRONG:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return out


def _hard_split(s, limit):
    """实在没有停顿符可切时按字数硬切，但尽量落在窗口后半段的标点上。"""
    out = []
    while _clen(s) > limit:
        window = s[:limit + 1]
        best = -1
        for ch in _CLAUSE_STRONG + _SENT_STRONG:
            k = window.rfind(ch)
            if k > best:
                best = k
        cut = best + 1 if best >= limit // 2 else limit
        out.append(s[:cut])
        s = s[cut:].lstrip()
    if s:
        out.append(s)
    return out


def _merge_short(segs, min_len, max_len):
    """把过短的段并入后一段，减少"每段一次请求"的固定开销。"""
    if len(segs) <= 1:
        return segs
    out, carry = [], ""
    for i, s in enumerate(segs):
        if carry:
            s = carry + s
            carry = ""
        if i < len(segs) - 1 and _clen(s) < min_len \
                and _clen(s) + _clen(segs[i + 1]) <= max_len:
            carry = s
            continue
        out.append(s)
    if carry:
        if out and _clen(out[-1]) + _clen(carry) <= max_len:
            out[-1] += carry
        else:
            out.append(carry)
    return out


def segment_text(text, max_len=60, min_len=8):
    """把回复切成"可以逐段合成"的片段。

    与 StreamVox 的 segment_text 形状相同，但**取舍相反**：它按固定 max_length
    积极切碎，因为它每段都要吃一次本地推理；这边首包与长度无关（见文件头实测），
    切得越碎只会让语调越碎、句间停顿越多。所以：
      - max_len 只用来兜住"特别长的回复"，日常一两条句子 = 一次请求；
      - 只在**句号级**切开；单句本身超长时才降级到子句，再不行才硬切；
      - 过短段向后合并，避免为两个字单开一次请求。
    """
    text = clean_text(text)
    if not text:
        return []
    max_len = max(12, int(max_len))

    segs, cur = [], ""

    def push(seg):
        seg = (seg or "").strip()
        if seg:
            segs.append(seg)

    for unit in _split_sentences(text):
        unit = unit.strip()
        if not unit:
            continue

        if _clen(unit) <= max_len:
            if cur and _clen(cur) + _clen(unit) > max_len:
                push(cur)
                cur = unit
            else:
                cur += unit
            continue

        # 单句就超过上限：先把已累计的吐出去，再降到子句层
        push(cur)
        cur = ""
        cb = ""
        for clause in _split_clauses(unit):
            if _clen(clause) > max_len:
                push(cb)
                cb = ""
                for piece in _hard_split(clause, max_len):
                    push(piece)
            elif cb and _clen(cb) + _clen(clause) > max_len:
                push(cb)
                cb = clause
            else:
                cb += clause
        cur = cb

    push(cur)
    return _merge_short(segs, min_len, max_len)


# ============================ 豆包 PCM 客户端 ============================

_SESS = None
_SESS_LOCK = threading.Lock()


def session():
    """共享一个 keep-alive 会话，省掉每次请求的 TLS 握手。

    ★ 这里**不能**用 threading.local()。Flask 是"一个请求一个线程"，thread-local
      等于每个请求都新建会话 —— 实测首响从 0.43s 退化到 0.71s，等于把连接复用
      的收益全部还回去。requests.Session 底层的连接池本身是线程安全的，这里只调
      post()、不并发修改 session 级状态（cookies/headers 都按次传），共享是安全的。
    ★ trust_env=False 必须保留：本机 shell/服务常被注入 http_proxy，
      走代理会连不上或明显变慢。
    """
    global _SESS
    if _SESS is None:
        with _SESS_LOCK:
            if _SESS is None:
                s = requests.Session()
                s.trust_env = False
                s.proxies = {"http": None, "https": None}
                ad = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
                s.mount("https://", ad)
                s.mount("http://", ad)
                _SESS = s
    return _SESS


def prewarm(timeout=3.0):
    """提前把到豆包的 TLS 连接建好、放进连接池（不合成、不计费）。

    ★ 为什么要这个：实测首包在**暖连接**下 0.32~0.45s，**冷连接**要多付
      0.5~0.7s 的握手（1097ms vs 465ms 就是这么来的）。而她现在"想事情"
      要 1~3 秒，这段时间连接池是空转的 —— 在等大脑返回时顺手热一下，
      用户感知的首响就能少半秒。
    失败静默：预热只是优化，绝不能因为它影响正常合成。"""
    try:
        host = VOLC_URL.split("/api/", 1)[0] + "/"
        session().get(host, timeout=timeout)
        return True
    except Exception:
        return False


class VolcPcm(object):
    """豆包语音合成 2.0 的裸 PCM 通道。单线程使用（生成器不可重入）。"""

    def __init__(self, key, speaker, speech_rate=0, sample_rate=SAMPLE_RATE,
                 url=None, resource=None):
        if not key:
            raise VolcError("未配置 VOLC_SPEECH_API_KEY")
        self.key = key
        self.speaker = speaker
        self.speech_rate = int(speech_rate or 0)
        self.sample_rate = int(sample_rate)
        self.url = url or VOLC_URL
        self.resource = resource or VOLC_RESOURCE

    def synth(self, text):
        """逐块 yield 该段文本的 s16le 字节；失败抛 VolcError。"""
        body = {
            "user": {"uid": "xiaoyun"},
            "req_params": {
                "text": text,
                "speaker": self.speaker,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "speech_rate": self.speech_rate,
                },
            },
        }
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.key,
            "X-Api-Resource-Id": self.resource,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        resp = session().post(self.url, json=body, headers=headers,
                              stream=True, timeout=(5, 60))
        try:
            if resp.status_code != 200:
                raise VolcError("HTTP %d %s" % (resp.status_code, (resp.text or "")[:160]))
            got = 0
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                s = line.strip()
                if s.startswith("data:"):
                    s = s[5:].strip()
                try:
                    j = json.loads(s)
                except Exception:
                    continue
                code = j.get("code")
                if code not in (0, 20000000, None):
                    raise VolcError("code=%s %s" % (code, (j.get("message") or "")[:160]))
                d = j.get("data")
                if not d:
                    continue
                b = base64.b64decode(d)
                if b:
                    got += len(b)
                    yield b
            if not got:
                raise VolcError("没有返回音频（额度/音色/Resource-Id？）")
        finally:
            resp.close()


def _emit(on_event, msg):
    if on_event:
        try:
            on_event(msg)
        except Exception:
            pass


def stream_pcm(text, key, speaker, speech_rate=0, sample_rate=SAMPLE_RATE,
               max_len=60, min_len=8, on_event=None):
    """切段 -> 逐段请求 -> 边收边吐。

    第一个 yield 就是"可以开口了"的时刻（实测 0.35s 量级）。
    段间串行、不并发：并行确实能缩短总时长，但会让音色/语调抖动；而豆包 RTF≈0.35，
    句级播放永远追不上合成，并发省下的时间对听感没有意义。
    """
    segs = segment_text(text, max_len=max_len, min_len=min_len)
    if not segs:
        return
    cli = VolcPcm(key, speaker, speech_rate, sample_rate)
    t_all = time.time()
    produced = 0
    _emit(on_event, "切段 %d 段: %s" % (len(segs), " | ".join(
        "%d字" % _clen(s) for s in segs)))

    for idx, seg in enumerate(segs):
        t0 = time.time()
        ttfb = None
        n = 0
        try:
            for chunk in cli.synth(seg):
                if ttfb is None:
                    ttfb = round(time.time() - t0, 3)
                n += len(chunk)
                produced += len(chunk)
                yield chunk
        except Exception as e:
            _emit(on_event, "第%d/%d段失败(%d字): %s" % (idx + 1, len(segs), _clen(seg), e))
            if idx == 0:
                raise
            # 后续段失败：保留已经吐出去的音频，不再继续（宁短不断）
            return
        _emit(on_event, "第%d/%d段 %d字 首包%.2fs 用时%.2fs 音频%.2fs"
              % (idx + 1, len(segs), _clen(seg), ttfb or -1,
                 time.time() - t0, n / float(BYTES_PER_SEC)))

    _emit(on_event, "流式完成 %d段 音频%.2fs 总耗时%.2fs"
          % (len(segs), produced / float(BYTES_PER_SEC), time.time() - t_all))


# ============================ 自测入口 ============================
if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    demo = ("今天下午三点我们再开一次会，请提前准备好演示材料。"
            "会议室在三楼，别忘了带上你的电脑！对了，晚上一起吃点什么？")
    if len(sys.argv) > 1:
        demo = sys.argv[1]

    print("原文 %d 字" % _clen(demo))
    for i, s in enumerate(segment_text(demo), 1):
        print("  段%d (%d字) %s" % (i, _clen(s), s))
