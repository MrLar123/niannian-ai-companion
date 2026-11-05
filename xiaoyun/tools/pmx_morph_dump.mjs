// 遍历 PMX 模型，导出每个模型自带的 morph（表情）名，用于设计情绪映射表。
//
// 为什么需要这个：MMD 表情名是日文且各模型不同（笑い/にこり/笑顔、困る/悲しみ、
// ジト目/じと目…），情绪配方不能写死索引，只能按关键词解析、缺了自动降级。
// 先扫一遍全部模型，才知道"关键词表要覆盖到什么程度"。
//
// 用法：
//   node tools/pmx_morph_dump.mjs                # 全部模型
//   node tools/pmx_morph_dump.mjs elysia kafka   # 指定 key
// 前置：stub_server.py（8765）或正式后端（8090）在跑；默认走 8765，避免打扰生产。
//       BASE=http://127.0.0.1:8090 node tools/pmx_morph_dump.mjs
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9375;
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const OUT = process.env.OUT || path.join(process.cwd(), 'docs', 'pmx_morphs.json');

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=560,820',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_pmx_dump_prof',
  'about:blank'
], { stdio: 'ignore' });

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) {}
    await sleep(300);
  }
  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { __err: 'eval error' };
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');

  // 模型清单（stub_server 的 /api/models 不带 kind，只有 pmx 字段，两种都认）
  let keys = process.argv.slice(2);
  if (!keys.length) {
    const j = JSON.parse(await (await fetch(BASE + '/api/models')).text());
    keys = (j.models || [])
      .filter(m => m.kind === 'pmx' || (!m.kind && m.pmx))
      .map(m => m.key);
  }
  console.log('待扫描 %d 个模型（后端 %s）', keys.length, BASE);

  const out = {};
  for (const key of keys) {
    const t0 = Date.now();
    await send('Page.navigate', { url: `${BASE}/desktoppmx?model=${encodeURIComponent(key)}` });
    let morphs = null;
    for (let i = 0; i < 200; i++) {
      const r = await ev(`(function(){var d=window.__xyDbg;
        return (d && d.morphs && d.morphs().length) ? d.morphs() : null;})()`);
      if (Array.isArray(r) && r.length) { morphs = r; break; }
      await sleep(300);
    }
    if (!morphs) {
      console.log('✗ ' + key.padEnd(16) + ' 60s 内没拿到 morph（加载失败或该模型无表情）');
      out[key] = null;
      continue;
    }
    const ms = Date.now() - t0;
    out[key] = morphs;
    const hit = [];
    for (const [tag, words] of Object.entries(PROBE_WORDS)) {
      const f = morphs.find(n => words.some(w => n.indexOf(w) >= 0));
      hit.push(tag + '=' + (f || '✗'));
    }
    console.log('✓ ' + key.padEnd(16) + String(morphs.length).padStart(3) + ' 个 ' +
      String(ms).padStart(5) + 'ms  ' + hit.join(' '));
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  console.log('\n写入 %s', OUT);
  summary(out);  proc.kill();
  process.exit(0);
}

// 情绪配方需要用到的语义槽位 → 关键词候选（顺序即优先级）
const PROBE_WORDS = {
  smile:  ['笑い', 'にこり', 'にっこり', '笑顔', '微笑', 'smile'],
  angry:  ['怒り', '怒', 'angry'],
  sad:    ['困る', '困', '悲し', '泣き', 'sad'],
  calm:   ['なごみ', 'にっこり', 'relaxed', '照れ'],
  shock:  ['びっくり', '驚き', '驚', 'surprised', '目開き'],
  half:   ['じと目', 'ジト目', '无神', '無神', '半目', '瞳小'],
  blush:  ['照れ', '赤面', '頬'],
};

function summary(out) {
  const all = Object.values(out).filter(Boolean);
  if (!all.length) return;
  console.log('\n槽位覆盖率（%d 个模型）：', all.length);
  for (const [tag, words] of Object.entries(PROBE_WORDS)) {
    let ok = 0; const miss = [];
    for (const [k, ms] of Object.entries(out)) {
      if (!ms) continue;
      if (ms.some(n => words.some(w => n.indexOf(w) >= 0))) ok++; else miss.push(k);
    }
    console.log('  ' + tag.padEnd(6) + ' ' + String(ok).padStart(2) + '/' +
      String(all.length) + '  ' + (miss.length ? '缺: ' + miss.join(',') : '全覆盖'));
  }
}

main().catch((e) => { console.log('ERROR', e); proc.kill(); process.exit(1); });
