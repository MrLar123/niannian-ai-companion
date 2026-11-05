// 离线自测：不出浏览器，只验证"文本 -> 口型关键帧"这一段纯逻辑
const fs = require('fs');
const path = require('path');

const dictPath = path.join(__dirname, '..', 'assets', 'lipsync', 'zh-visemes.json');
const LS = require('../assets/lipsync/lipsync.js');

global.fetch = (u) => Promise.resolve({ json: () => JSON.parse(fs.readFileSync(dictPath, 'utf8')) });

const text = '你好呀，我是念念。今天想聊点什么？';
const duration = 4.2;                          // 假设这段 TTS 4.2 秒
const segments = [[0.15, 3.95]];               // 假设中段一直在说话

LS.init({ dictUrl: 'x' }).then(() => {
  const sylls = LS.parseText(text);
  const voiced = sylls.filter(Boolean);
  console.log('字数(含停顿标记)=', sylls.length, ' 可发音音节=', voiced.length);
  console.log('前 8 个音节 =', voiced.slice(0, 8).map(s => `${s.vi}->${s.vf}`).join('  '));

  const keys = LS.buildTimeline(sylls, segments);
  console.log('关键帧数 =', keys.length);
  console.log('前 10 帧:');
  keys.slice(0, 10).forEach(k =>
    console.log(`  ${k.t0.toFixed(3)}~${k.t1.toFixed(3)}  口型${k.v}`));

  const last = keys[keys.length - 1];
  console.log('末帧 =', last.t1.toFixed(3), '（应 ≤ 3.95，不应超出语音段）');
  const bad = keys.filter(k => k.t1 <= k.t0);
  console.log('非法帧 =', bad.length);

  // 多段（中间有停顿）场景
  const keys2 = LS.buildTimeline(LS.parseText('第一句。第二句。'), [[0.1, 1.0], [1.4, 2.3]]);
  const cross = keys2.filter(k => k.t0 > 1.0 && k.t0 < 1.4);
  console.log('停顿区间内不应有帧，实际 =', cross.length);
});
