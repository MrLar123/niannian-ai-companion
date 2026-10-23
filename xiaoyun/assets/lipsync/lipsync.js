/* ============================================================================
 * lipsync.js —— 念念口型同步引擎（方案 B：文本→拼音→口型序列 + 音频对齐）
 * ----------------------------------------------------------------------------
 * 零依赖、无需构建，直接 <script src="/assets/lipsync/lipsync.js"></script>
 *
 * 原理（不靠 AI 反推语音内容，因为我们本来就知道要说什么）：
 *   1. 文本 → 逐字查拼音字典 → (声母口型, 韵母口型)
 *   2. 解码音频 → 20ms 一帧算 RMS 包络 → 找出真正在说话的时间段
 *   3. 把音节按"发音时长权重"铺进这些时间段，得到带时间戳的口型关键帧
 *   4. 播放时用 requestAnimationFrame 读 currentTime，插值 + 起落平滑，
 *      再乘上实时音量，输出 5 个元音口型的权重
 *
 * 输出给渲染端的是 weight = {a,i,u,e,o}（和为 1，静音时全 0）：
 *   MMD(PMX)  → あ / い / う / え / お 五个 morph
 *   VRM       → aa / ih / ou / ee / oh 五个 expression
 *   Live2D    → ParamMouthOpenY = 总和, ParamMouthForm 由圆唇度推
 *
 * 用法：
 *   await XiaoyunLipSync.init({ dictUrl: '/assets/lipsync/zh-visemes.json' });
 *   const sync = XiaoyunLipSync.attach(audioEl, text);
 *   // 每帧读取： sync.weights -> {a,i,u,e,o,open}
 *   // 用完后：   sync.stop()
 * ==========================================================================*/
(function (global) {
  'use strict';

  var CFG = {
    frameMs: 20,            // 包络分析帧长
    silenceRatio: 0.10,     // 低于峰值 10% 视为静音（相对阈值）
    minGap: 0.12,           // 小于 120ms 的静音缝隙合并掉（避免字间碎切）
    minSeg: 0.06,           // 短于 60ms 的语音段丢弃
    crossMs: 55,            // 口型切换的交叉淡化时长
    attackMs: 40,           // 张嘴速度
    releaseMs: 90,          // 闭嘴速度（比张嘴慢，更自然）
    onsetRatio: 0.22,       // 音节内"声母"占的比例
    minOnset: 0.035,        // 声母最短时间
    maxOnset: 0.11,         // 声母最长时间
    minOnsetGap: 0.13,      // 两个音节起始沿的最小间隔（正常语速 ~150~250ms/字）
    onsetFluxRatio: 0.12    // 通量候选峰下限（相对最大通量，滤呼吸底噪）
  };

  /* 韵母口型权重（决定这个音节占多长时间） */
  var FINAL_WEIGHT = { 1: 1.30, 5: 1.18, 3: 1.15, 4: 1.00, 2: 0.85, 0: 0.55 };

  /* 数字 / 英文字母的读法口型（拼音字典里没有） */
  var DIGIT_VIS = {
    '0': [2, 2], '1': [2, 2], '2': [2, 4], '3': [2, 1], '4': [2, 2],
    '5': [3, 3], '6': [2, 5], '7': [2, 2], '8': [0, 1], '9': [2, 5]
  };
  var LETTER_VIS = {
    a: [1, 1], e: [4, 4], i: [2, 2], o: [5, 5], u: [3, 3], y: [3, 2],
    b: [0, 2], c: [2, 2], d: [2, 2], f: [2, 4], g: [2, 2], h: [2, 4],
    j: [2, 4], k: [2, 4], l: [2, 4], m: [0, 2], n: [2, 4], p: [0, 2],
    q: [2, 3], r: [1, 1], s: [2, 4], t: [2, 4], v: [3, 2], w: [0, 3],
    x: [2, 4], z: [2, 4]
  };

  var dict = null;
  var dictPromise = null;
  var ctx = null;

  function ac() {
    if (!ctx) {
      var AC = global.AudioContext || global.webkitAudioContext;
      if (AC) ctx = new AC();
    }
    return ctx;
  }

  /* ------------------------------------------------------------------ 字典 */
  function init(opt) {
    opt = opt || {};
    if (dict) return Promise.resolve(dict);
    if (dictPromise) return dictPromise;
    var url = opt.dictUrl || '/assets/lipsync/zh-visemes.json';
    dictPromise = fetch(url).then(function (r) { return r.json(); })
      .then(function (j) { dict = j.map || {}; return dict; })
      .catch(function (e) {
        console.warn('[lipsync] 字典加载失败，退化为音量模式', e);
        dict = {};
        return dict;
      });
    return dictPromise;
  }

  /* -------------------------------------------------------------- 文本解析 */
  function isCJK(ch) {
    var c = ch.charCodeAt(0);
    return c >= 0x4E00 && c <= 0x9FFF;
  }

  /* 文本 -> 音节数组 [{vi, vf, w}]，标点/空白产生 null（停顿） */
  function parseText(text) {
    text = (text || '').replace(/[*#`_~>\[\]()（）「」『』【】]/g, '');
    var out = [];
    for (var i = 0; i < text.length; i++) {
      var ch = text[i], code = null;
      if (isCJK(ch)) {
        var v = dict && dict[ch];
        if (v) code = [parseInt(v[0], 10), parseInt(v[1], 10)];
        else code = [2, 2];                       // 字典没收录：给个中性口型
      } else if (ch >= '0' && ch <= '9') {
        code = DIGIT_VIS[ch];
      } else if (/[a-zA-Z]/.test(ch)) {
        code = LETTER_VIS[ch.toLowerCase()] || [2, 2];
      } else {
        out.push(null);                           // 标点 / 空格 -> 停顿
        continue;
      }
      var vi = code[0], vf = code[1];
      var w = (FINAL_WEIGHT[vf] || 1) + (vi !== vf ? 0.12 : 0);
      out.push({ vi: vi, vf: vf, w: w });
    }
    return out;
  }

  /* ------------------------------------------------------ 音频包络与语音段 */
  function analyzeBuffer(buf) {
    var sr = buf.sampleRate, dur = buf.duration;
    var n = Math.max(1, Math.floor(dur * 1000 / CFG.frameMs));
    var rms = new Float32Array(n);
    var ch0 = buf.getChannelData(0);
    var ch1 = buf.numberOfChannels > 1 ? buf.getChannelData(1) : null;
    var step = Math.floor(sr * CFG.frameMs / 1000) || 1;
    var peak = 0, sorted;

    for (var i = 0; i < n; i++) {
      var s = i * step, e = Math.min(buf.length, s + step), sum = 0, c = 0;
      for (var k = s; k < e; k += 2) {           // 隔点取样，够用且快
        var v = ch1 ? (ch0[k] + ch1[k]) * 0.5 : ch0[k];
        sum += v * v; c++;
      }
      var r = Math.sqrt(sum / Math.max(1, c));
      rms[i] = r;
      if (r > peak) peak = r;
    }
    // 参考音量取 92 分位（防止个别爆音把阈值抬太高）
    sorted = Float32Array.from(rms).sort();
    var ref = sorted[Math.floor(n * 0.92)] || peak || 1e-6;
    return { n: n, rms: rms, ref: ref, peak: peak, dur: dur, step: CFG.frameMs / 1000 };
  }

  /* 找出语音段 [[t0,t1], ...] */
  function speechSegments(an) {
    var thr = Math.max(an.ref * CFG.silenceRatio, 0.004);
    var raw = [], i, start = -1;
    for (i = 0; i < an.n; i++) {
      var loud = an.rms[i] > thr;
      if (loud && start < 0) start = i;
      if (!loud && start >= 0) { raw.push([start, i]); start = -1; }
    }
    if (start >= 0) raw.push([start, an.n]);

    // 合并短缝隙
    var merged = [];
    for (i = 0; i < raw.length; i++) {
      var seg = raw[i];
      if (merged.length &&
          (seg[0] - merged[merged.length - 1][1]) * an.step < CFG.minGap) {
        merged[merged.length - 1][1] = seg[1];
      } else merged.push([seg[0], seg[1]]);
    }
    var out = [];
    for (i = 0; i < merged.length; i++) {
      var t0 = merged[i][0] * an.step, t1 = merged[i][1] * an.step;
      if (t1 - t0 >= CFG.minSeg) out.push([t0, Math.min(t1, an.dur)]);
    }
    if (!out.length) out.push([0.08 * an.dur, 0.95 * an.dur]);
    return out;
  }

  /* ------------------------------------------- 音节起始沿检测（onset 锚定） */
  /* 在语音段内找"能量突然变大"的位置。合成文本已知 -> 音节数是精确先验：
   * 直接取通量最强的 count 个峰，峰值 picking 加最小间隔约束，不做阈值猜谜。
   * 返回按时间升序的时刻（秒）；峰不够时由调用方按权重比例补位。 */
  function detectOnsets(an, segments, count) {
    var n = an.n, rms = an.rms, i;
    if (!n || count <= 0 || !segments.length) return [];

    // 3 帧平滑，抑制单帧毛刺
    var sm = new Float32Array(n);
    for (i = 0; i < n; i++) {
      var a = i > 0 ? rms[i - 1] : rms[i];
      var b = i < n - 1 ? rms[i + 1] : rms[i];
      sm[i] = (a + rms[i] + b) / 3;
    }
    // 能量通量：只取上升半部（onset = 能量上升沿）
    var flux = new Float32Array(n);
    var fmax = 0;
    for (i = 1; i < n; i++) {
      var d = sm[i] - sm[i - 1];
      flux[i] = d > 0 ? d : 0;
      if (flux[i] > fmax) fmax = flux[i];
    }
    if (fmax <= 0) return [];

    // 候选峰：±2 帧窗口内的局部极大，且通量不低于峰值的 onsetFluxRatio
    var cands = [];
    for (i = 2; i < n - 2; i++) {
      var f = flux[i];
      if (f < fmax * CFG.onsetFluxRatio) continue;
      if (f >= flux[i - 1] && f >= flux[i - 2] && f >= flux[i + 1] && f >= flux[i + 2]) {
        cands.push({ i: i, f: f });
      }
    }
    if (!cands.length) return [];

    // 候选峰必须落在某语音段内（前后各放宽 1 帧）
    function inSeg(idx) {
      for (var s = 0; s < segments.length; s++) {
        var lo = Math.floor(segments[s][0] / an.step) - 1;
        var hi = Math.ceil(segments[s][1] / an.step) + 1;
        if (idx >= lo && idx <= hi) return true;
      }
      return false;
    }

    // 按通量降序贪心选 count 个，满足最小间隔；再按时间升序返回。
    // 居中平滑会让通量峰比真实起音早 1 帧，段首处尤其明显 —— 返回前夹回
    // 所在语音段，保证 onset 不早于段首、不晚于段尾。
    cands.sort(function (x, y) { return y.f - x.f; });
    var minGapF = Math.max(1, Math.round(CFG.minOnsetGap / an.step));
    var picked = [];
    for (i = 0; i < cands.length && picked.length < count; i++) {
      if (!inSeg(cands[i].i)) continue;
      var ok = true;
      for (var j = 0; j < picked.length; j++) {
        if (Math.abs(picked[j].i - cands[i].i) < minGapF) { ok = false; break; }
      }
      if (ok) picked.push(cands[i]);
    }
    picked.sort(function (x, y) { return x.i - y.i; });
    var out = [];
    for (i = 0; i < picked.length; i++) {
      var idx = picked[i].i;
      for (var s = 0; s < segments.length; s++) {
        var lo2 = Math.floor(segments[s][0] / an.step) - 1;
        var hi2 = Math.ceil(segments[s][1] / an.step) + 1;
        if (idx >= lo2 && idx <= hi2) {
          if (idx < lo2 + 1) idx = lo2 + 1;
          if (idx > hi2 - 1) idx = hi2 - 1;
          break;
        }
      }
      out.push(idx * an.step);
    }
    return out;
  }

  /* ------------------------------------------------------------ 关键帧生成 */
  /* 把音节铺到语音段里。
   * 有 onset 数据时：前 O-1 个音节的边界直接由 onset 锚定（数据驱动），
   *   剩余音节在"最后一个 onset 之后的语音时间"里按权重比例补位；
   * 无 onset 数据（峰不够 / 没传 an）：退化为整体按权重比例铺排。
   * 段内时间 <-> 真实时间用分段线性映射，停顿区间内不会落帧。 */
  function buildTimeline(sylls, segments, an) {
    var voiced = [];
    for (var i = 0; i < sylls.length; i++) if (sylls[i]) voiced.push(sylls[i]);
    if (!voiced.length) return [];

    var segLen = [], total = 0, s;
    for (s = 0; s < segments.length; s++) {
      var L = segments[s][1] - segments[s][0];
      segLen.push(L); total += L;
    }
    if (total <= 0) return [];

    function p2t(p) {                            // p: 累计"语音时间"
      for (var j = 0; j < segments.length; j++) {
        if (p <= segLen[j] || j === segments.length - 1) {
          return segments[j][0] + Math.min(p, segLen[j]);
        }
        p -= segLen[j];
      }
      return segments[segments.length - 1][1];
    }
    // 真实时刻 -> "语音时间"（夹到所在段；不在任何段内则夹到最近的段界）
    function t2p(t) {
      for (var j = 0; j < segments.length; j++) {
        if (t <= segments[j][1] + 1e-6) {
          return (j > 0 ? segLen.slice(0, j).reduce(function (a, b) { return a + b; }, 0) : 0)
                 + Math.max(0, t - segments[j][0]);
        }
      }
      return total;
    }
    // 时刻 t 所在语音段的结束时刻（不在任何段内则返回 null）
    function segEndOf(t) {
      for (var j = 0; j < segments.length; j++) {
        if (t >= segments[j][0] - 1e-6 && t <= segments[j][1] + 1e-6) return segments[j][1];
      }
      return null;
    }

    var S = voiced.length;
    var onsets = (an && S >= 2) ? detectOnsets(an, segments, S) : [];
    var spans;                                    // [{t0, t1}]，t1 可为 null（段尾兜底）

    if (onsets.length >= 2) {
      spans = [];
      var O = onsets.length, k;
      // 前 O-1 个音节：边界由 onset 直接锚定；t1 不超过所在段尾（不跨停顿）
      for (k = 0; k < O - 1; k++) {
        var t1 = onsets[k + 1];
        var end = segEndOf(onsets[k]);
        if (end !== null && t1 > end) t1 = end;
        spans.push({ t0: onsets[k], t1: t1 });
      }
      // 剩余音节（含最后一个 onset 起的那个）在尾部语音时间里按权重补位
      var tailP0 = t2p(onsets[O - 1]);
      var tailW = 0, m;
      for (m = O - 1; m < S; m++) tailW += voiced[m].w;
      var acc = 0;
      for (k = O - 1; k < S; k++) {
        var d = tailW > 0 ? (total - tailP0) * (voiced[k].w / tailW) : 0;
        spans.push({ t0: p2t(tailP0 + acc), t1: p2t(tailP0 + acc + d) });
        acc += d;
      }
    } else {
      // 退化：整体按权重比例铺排
      spans = [];
      var sumW = 0;
      for (i = 0; i < S; i++) sumW += voiced[i].w;
      var acc2 = 0;
      for (i = 0; i < S; i++) {
        var d2 = total * (voiced[i].w / sumW);
        spans.push({ t0: p2t(acc2), t1: p2t(acc2 + d2) });
        acc2 += d2;
      }
    }

    var keys = [];
    for (i = 0; i < spans.length; i++) {
      var sp = spans[i];
      var syl = voiced[i];
      var t0 = sp.t0;
      var t1 = sp.t1;
      if (t1 === null) {                          // 理论上不会到这，兜底用所在段尾
        var e2 = segEndOf(t0);
        t1 = e2 === null ? t0 : e2;
      }
      if (t1 - t0 < 0.02) continue;

      var dOn = Math.min(CFG.maxOnset, Math.max(CFG.minOnset, (t1 - t0) * CFG.onsetRatio));
      var own = syl.vi !== syl.vf || syl.vi === 0;
      if (own) keys.push({ t0: t0, t1: t0 + dOn, v: syl.vi });
      keys.push({ t0: own ? t0 + dOn : t0, t1: t1, v: syl.vf });
    }
    return keys;
  }

  /* ---------------------------------------------------------------- 运行时 */
  function weightsFor(vis, amp) {
    var w = { a: 0, i: 0, u: 0, e: 0, o: 0, open: 0 };
    if (!amp || vis < 0) return w;
    var key = vis === 1 ? 'a' : vis === 2 ? 'i' : vis === 3 ? 'u' : vis === 4 ? 'e' : vis === 5 ? 'o' : null;
    if (key) w[key] = 1;
    else return w;                               // 闭唇：全 0
    // 闭口型也要保留一点开合度，避免"完全不动"
    var open = vis === 0 ? 0 : 0.25 + 0.75 * amp;
    for (var k in w) if (k !== 'open') w[k] *= open;
    w.open = open;
    return w;
  }

  function attach(audio, text, opt) {
    opt = opt || {};
    var ctl = {
      ready: false,
      weights: { a: 0, i: 0, u: 0, e: 0, o: 0, open: 0 },
      viseme: -1,
      stop: stop
    };
    var keys = [], an = null, raf = 0, cur = { a: 0, i: 0, u: 0, e: 0, o: 0 };
    var lastT = 0, lastWall = 0, running = false;

    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf), raf = 0;
      ctl.weights = { a: 0, i: 0, u: 0, e: 0, o: 0, open: 0 };
      ctl.viseme = -1;
    }

    function ampAt(t) {
      if (!an) return 1;
      var idx = Math.floor(t / an.step);
      // 前后各取一帧的最大值，避免帧边界上音量掉到 0 导致嘴抖
      var v = 0;
      for (var k = idx - 1; k <= idx + 1; k++) {
        if (k >= 0 && k < an.n && an.rms[k] > v) v = an.rms[k];
      }
      var a = v / (an.ref || 1);
      return Math.max(0, Math.min(1, a));
    }

    /* 解码失败时的兜底：直接测播放中的音量（方案 A 的做法） */
    var analyser = null, tdata = null;
    function startAmplitudeFallback() {
      try {
        var c = ac();
        if (!c) return;
        var srcNode = c.createMediaElementSource(audio);
        analyser = c.createAnalyser();
        analyser.fftSize = 1024;
        srcNode.connect(analyser);
        analyser.connect(c.destination);
        tdata = new Uint8Array(analyser.fftSize);
        if (c.resume) c.resume();
      } catch (e) {
        console.warn('[lipsync] 音量兜底初始化失败', e);
      }
    }

    function liveLevel() {
      if (!analyser) return 0;
      analyser.getByteTimeDomainData(tdata);
      var sum = 0;
      for (var i = 0; i < tdata.length; i += 2) {
        var v = (tdata[i] - 128) / 128;
        sum += v * v;
      }
      var r = Math.sqrt(sum / (tdata.length / 2));
      return Math.max(0, Math.min(1, r * 6));      // 6 倍放大，语音 RMS 约 0.05~0.25
    }

    function frame() {
      if (!running) return;
      raf = requestAnimationFrame(frame);
      var t = audio.currentTime || 0;
      var now = performance.now();

      // 找到当前关键帧（顺序扫描 + 缓存，音频偶尔 seek 也能自愈）
      var key = null;
      for (var i = 0; i < keys.length; i++) {
        if (t >= keys[i].t0 && t < keys[i].t1) { key = keys[i]; break; }
      }
      if (!key && keys.length) {
        if (t < keys[0].t0) key = null;
        else if (t >= keys[keys.length - 1].t1) key = null;
      }

      var target = key ? weightsFor(key.v, ampAt(t)) : { a: 0, i: 0, u: 0, e: 0, o: 0, open: 0 };
      if (!an && analyser) {                     // 兜底：只按音量张嘴
        var lv = liveLevel();
        target = { a: lv * 0.85, i: 0, u: 0, e: 0, o: 0, open: lv };
      }
      // 交叉淡化：刚进入这一帧时，从上一帧口型过渡
      var k = 1;
      if (key) {
        var into = t - key.t0;
        if (into < CFG.crossMs / 1000) k = Math.max(0, into / (CFG.crossMs / 1000));
      }

      var dt = Math.min(0.05, Math.max(0.001, (now - lastWall) / 1000 || 0.016));
      lastWall = now;
      var rising = target.open > ctl.weights.open;
      var tau = (rising ? CFG.attackMs : CFG.releaseMs) / 1000;
      var alpha = 1 - Math.exp(-dt / Math.max(0.004, tau));

      var names = ['a', 'i', 'u', 'e', 'o'], sum = 0;
      for (var n = 0; n < names.length; n++) {
        var nm = names[n];
        var goal = target[nm] * k + cur[nm] * (1 - k);
        cur[nm] += (goal - cur[nm]) * alpha;
        if (cur[nm] < 0) cur[nm] = 0;
        sum += cur[nm];
      }
      // 各通道分别平滑后总和可能 >1（MMD 多表情叠加会变形成怪物），按比例压回
      if (sum > 1) {
        for (n = 0; n < names.length; n++) cur[names[n]] /= sum;
        sum = 1;
      }
      for (n = 0; n < names.length; n++) ctl.weights[names[n]] = cur[names[n]];
      ctl.weights.open = sum;
      ctl.viseme = key ? key.v : -1;
      lastT = t;
    }

    /* 异步准备：解码 + 建时间轴，好了再开跑（播放不等人，先张嘴靠兜底） */
    init(opt).then(function () {
      var src = audio.src || audio.currentSrc;
      if (!src) return;
      fetch(src).then(function (r) { return r.arrayBuffer(); })
        .then(function (ab) {
          var c = ac();
          if (!c) throw new Error('no AudioContext');
          return new Promise(function (res, rej) {
            var p = c.decodeAudioData(ab, res, rej);
            if (p && p.then) p.then(res, rej);
          });
        })
        .then(function (buf) {
          an = analyzeBuffer(buf);
          var segs = speechSegments(an);
          keys = buildTimeline(parseText(text), segs, an);
          ctl.ready = true;
          ctl.segments = segs;
          ctl.keys = keys.length;
          ctl.onsets = an ? detectOnsets(an, segs, parseText(text).filter(Boolean).length).length : 0;
          if (opt.debug) console.log('[lipsync] segs=' + segs.length + ' keys=' + keys.length +
                                      ' onsets=' + ctl.onsets + ' dur=' + an.dur.toFixed(2));
        })
        .catch(function (e) {
          console.warn('[lipsync] 解码失败，退化为纯音量模式', e);
          ctl.mode = 'amplitude';
          startAmplitudeFallback();
        });

      running = true;
      lastWall = performance.now();
      frame();
    });

    audio.addEventListener('ended', stop);
    audio.addEventListener('pause', function () { /* 暂停时保持，恢复继续 */ });
    return ctl;
  }

  global.XiaoyunLipSync = {
    init: init,
    attach: attach,
    parseText: parseText,
    buildTimeline: buildTimeline,
    detectOnsets: detectOnsets,
    analyzeBuffer: analyzeBuffer,
    speechSegments: speechSegments,
    attachCfg: CFG
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = global.XiaoyunLipSync;
})(typeof window !== 'undefined' ? window : globalThis);
