// 读 PMX 的表情（morph）明细：类型、影响顶点数、位移量级。
// 用途：判断"某个表情设成 1.0 到底看不看得出来"——
//   顶点数少 / 最大位移小于 0.01 模型单位的表情，肉眼基本无感。
// 用法：node tools/pmxmorphs.mjs <file.pmx> [关键词...]
import fs from 'node:fs';
import { MMDParser } from '../assets/lib/addons/libs/mmdparser.module.js';

const file = process.argv[2];
const greps = process.argv.slice(3);
const buf = fs.readFileSync(file);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const pmx = new MMDParser.Parser().parsePmx(ab);

const TYPE = { 0: 'group', 1: 'vertex', 2: 'bone', 3: 'UV', 4: 'UV1', 5: 'UV2',
               6: 'UV3', 7: 'UV4', 8: 'material', 9: 'flip', 10: 'impulse' };

console.log('morphs:', pmx.morphs.length, ' vertices:', pmx.vertices.length);
console.log('idx | name (en) | type | n | maxDisp');
pmx.morphs.forEach((m, i) => {
  if (greps.length && !greps.some(g => (m.name || '').indexOf(g) >= 0)) return;
  const t = TYPE[m.type] || ('?' + m.type);
  const els = m.elements || [];
  let n = els.length, maxD = 0, sumD = 0;
  if (m.type === 1) {
    for (const e of els) {
      const p = e.position || [0, 0, 0];
      const d = Math.hypot(p[0], p[1], p[2]);
      if (d > maxD) maxD = d;
      sumD += d;
    }
  } else if (m.type === 2) {
    for (const e of els) {
      const p = e.position || [0, 0, 0];
      const d = Math.hypot(p[0], p[1], p[2]);
      if (d > maxD) maxD = d;
    }
  } else if (m.type === 0) {
    for (const e of els) maxD = Math.max(maxD, e.ratio == null ? 0 : e.ratio);
  }
  const extra = m.type === 1 ? (' avg=' + (sumD / Math.max(1, n)).toFixed(4)) : '';
  console.log(String(i).padStart(3) + ' | ' + m.name + ' (' + (m.nameEnglish || '') + ') | '
    + t + ' | ' + n + ' | ' + maxD.toFixed(4) + extra);
});
