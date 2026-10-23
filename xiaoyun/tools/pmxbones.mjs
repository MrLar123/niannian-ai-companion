// 复用项目自带的 three.js MMDParser，离线导出 PMX 骨骼名与表情名
import fs from 'node:fs';
import { MMDParser } from '../assets/lib/addons/libs/mmdparser.module.js';

const file = process.argv[2];
const buf = fs.readFileSync(file);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

const parser = new MMDParser.Parser();
const pmx = parser.parsePmx(ab);

console.log('metadata:', JSON.stringify(pmx.metadata));
console.log('bones:', pmx.bones.length, ' morphs:', pmx.morphs.length, ' rigidBodies:', (pmx.rigidBodies || []).length);

console.log('\n=== BONES ===');
pmx.bones.forEach((b, i) => {
  console.log(String(i).padStart(3) + ' | ' + b.name + '  (en:' + (b.nameEnglish || '') + ') parent=' + b.parentIndex);
});

console.log('\n=== MORPHS ===');
(pmx.morphs || []).forEach((m, i) => {
  console.log(String(i).padStart(3) + ' | ' + m.name + '  (en:' + (m.nameEnglish || '') + ') panel=' + m.panel + ' type=' + m.type);
});
