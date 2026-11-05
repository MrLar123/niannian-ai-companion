// onset 锚定正确性测试：合成 RMS 包络（音节起始沿 = 已知时刻的通量峰），
// 验证 buildTimeline 把音节起点钉在这些峰上，而不是按权重比例摊。
const fs = require('fs');
const path = require('path');

const dictPath = path.join(__dirname, '..', 'assets', 'lipsync', 'zh-visemes.json');
const LS = require('../assets/lipsync/lipsync.js');
global.fetch = (u) => Promise.resolve({ json: () => JSON.parse(fs.readFileSync(dictPath, 'utf8')) });

const STEP = 0.02;                    // 20ms 帧
function fakeAnalysis(pulseTimes, dur) {
  const n = Math.floor(dur / STEP);
  const rms = new Float32Array(n);
  // 每个脉冲：起始帧能量阶跃到 0.25 再指数衰减（制造强通量峰），持续 0.20s
  for (let i = 0; i < n; i++) {
    const t = i * STEP;
    rms[i] = 0.012;                   // 静音底噪
    for (const pt of pulseTimes) {
      const dt = t - pt;
      if (dt >= 0 && dt < 0.20) rms[i] = Math.max(rms[i], 0.25 * Math.exp(-dt * 6));
    }
  }
  let peak = 0;
  for (const r of rms) if (r > peak) peak = r;
  return { n, rms, ref: peak, peak, dur, step: STEP };
}
// 误差指标：每个真实脉冲到"最近的关键帧起点"的距离（声母/韵母分帧不影响，
// 因为音节起点本身就是关键帧起点之一）
function errVsPulses(keys, pulses) {
  if (!keys.length) return 9;
  const starts = keys.map(k => k.t0);
  return pulses.reduce((a, p) => {
    let best = 9;
    for (const s of starts) best = Math.min(best, Math.abs(s - p));
    return a + best;
  }, 0) / pulses.length;
}

(async () => {
  await LS.init({ dictUrl: 'x' });

  const text = '今天天气真好，我们出去玩吧';        // 12 个汉字 -> 12 个音节
  const sylls = LS.parseText(text);
  const S = sylls.filter(Boolean).length;
  console.log('音节数 =', S);

  // ---- 场景 1：12 个脉冲，一一对应 ----
  const pulses = [0.30, 0.54, 0.78, 1.02, 1.26, 1.50, 1.74, 1.98, 2.28, 2.52, 2.76, 3.00];
  const an = fakeAnalysis(pulses, 3.5);
  const segs = LS.speechSegments(an);
  const onsets = LS.detectOnsets(an, segs, S);
  console.log('场景1 语音段 =', segs.map(s => s.map(x => x.toFixed(2)).join('~')).join('  '));
  console.log('场景1 onset =', onsets.length, '->', onsets.map(t => t.toFixed(2)).join(' '));

  let maxErr = 0, matched = 0;
  const used = new Array(pulses.length).fill(false);
  for (const o of onsets) {
    let best = -1, bd = 1e9;
    for (let j = 0; j < pulses.length; j++) {
      if (used[j]) continue;
      const d = Math.abs(o - pulses[j]);
      if (d < bd) { bd = d; best = j; }
    }
    if (best >= 0 && bd <= 0.03) { used[best] = true; matched++; maxErr = Math.max(maxErr, bd); }
  }
  console.log(`场景1 脉冲命中 = ${matched}/${pulses.length}  最大误差 = ${(maxErr * 1000).toFixed(0)}ms`);
  console.log(matched === pulses.length && maxErr <= 0.03 ? 'PASS: onset 全部命中真实音节起始沿' : 'FAIL: onset 与真实起始沿不符');

  const keys = LS.buildTimeline(sylls, segs, an);
  const keysOld = LS.buildTimeline(sylls, segs);   // 不传 an -> 旧比例路径
  const eNew = errVsPulses(keys, pulses), eOld = errVsPulses(keysOld, pulses);
  console.log(`场景1 平均对齐误差: 旧(比例) = ${(eOld * 1000).toFixed(0)}ms  新(onset) = ${(eNew * 1000).toFixed(0)}ms`);
  console.log(eNew < eOld ? 'PASS: onset 版误差显著小于比例版' : 'FAIL: 没有改进');

  // ---- 场景 2：欠检（只有 10 个脉冲，第 5/9 个音节是轻读没起音）----
  const pulses2 = pulses.filter((_, i) => i !== 4 && i !== 8);
  const an2 = fakeAnalysis(pulses2, 3.5);
  const segs2 = LS.speechSegments(an2);
  const onsets2 = LS.detectOnsets(an2, segs2, S);
  const keys2 = LS.buildTimeline(sylls, segs2, an2);
  let mono = true;
  for (let i = 1; i < keys2.length; i++) if (keys2[i].t0 < keys2[i - 1].t0 - 1e-9) mono = false;
  const inSeg = keys2.every(k => segs2.some(s => k.t0 >= s[0] - 1e-6 && k.t1 <= s[1] + 1e-6));
  console.log(`场景2 onset = ${onsets2.length}/${S}  keys = ${keys2.length}  单调 = ${mono}  全在语音段内 = ${inSeg}`);
  console.log(mono && inSeg && keys2.length > 0 ? 'PASS: 欠检时补位不乱、不跨停顿' : 'FAIL: 欠检路径有问题');

  // ---- 场景 3：无脉冲（通量趋 0）自动退化 ----
  const flat = fakeAnalysis([], 3.0);
  const flatKeys = LS.buildTimeline(sylls, LS.speechSegments(flat), flat);
  console.log('场景3 无脉冲 -> keys =', flatKeys.length, flatKeys.length > 0 ? 'PASS: 已退化为比例铺排' : 'FAIL: 空时间轴');

  // ---- 场景 4：多段（中间停顿）onset 不跨段 ----
  const an4 = fakeAnalysis([0.2, 0.45, 0.7, 1.6, 1.85, 2.1], 2.6);
  const segs4 = LS.speechSegments(an4);
  const keys4 = LS.buildTimeline(LS.parseText('你好世界，好久不见'), segs4, an4);
  const cross = keys4.filter(k => k.t0 > 1.0 && k.t0 < 1.4);
  console.log('场景4 停顿区间内帧数 =', cross.length, cross.length === 0 ? 'PASS: 无跨停顿帧' : 'FAIL: 帧落进了停顿');
})();
