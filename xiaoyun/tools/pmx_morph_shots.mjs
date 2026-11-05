// 单个 morph 语义探针：强行摆某个日文表情并出图，确认它到底长什么样。
//
// 为什么需要它：MMD 的表情名相同、效果可能完全不同 ——
//   「笑い」在有些模型上是闭眼笑（^ ^），在另一些上是睁眼笑容；
//   「怒り」有的只改眉、有的连嘴一起改；
//   「じと目」是半睁死鱼眼，也可能只是眼角微调。
// 光看名字定配方不可靠，尤其要覆盖 31 个模型时。
//
// 用法：
//   node tools/pmx_morph_shots.mjs [modelKey] [morph名,morph名,...]
// 默认：elysia + 常用语义候选
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9379;
const KEY = process.argv[2] || 'elysia';
const NAMES = (process.argv[3] || 'まばたき,笑い,にこり,じと目,怒り,困る,なごみ,はぅ,照れ').split(',');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const OUT = path.join(process.env.TEMP || '/tmp', 'xy_pmx_morph_' + KEY);
const W = 620, H = 940;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  `--window-size=${W},${H}`,
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_pmx_morph_prof',
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
  const ws = new WebSocket(list.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `${BASE}/desktoppmx?model=${encodeURIComponent(KEY)}` });

  let ready = false;
  for (let i = 0; i < 200; i++) {
    if (await ev(`!!(window.__xyDbg && __xyDbg.headScreen && __xyDbg.headScreen())`) === true) {
      ready = true; break;
    }
    await sleep(300);
  }
  if (!ready) {
    console.log('FAIL 模型未就绪'); for (const e of errors.slice(0, 3)) console.log(String(e).split('\n')[0]);
    proc.kill(); process.exit(1);
  }
  await sleep(700);
  const hs = await ev(`__xyDbg.headScreen()`);
  const all = await ev(`__xyDbg.morphs()`);
  console.log('模型 %s，表情 %d 个：%s', KEY, all.length, all.join('|'));

  const dump = (url, name) => {
    if (!url || !url.startsWith('data:image/png;base64,')) return false;
    fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(url.split(',')[1], 'base64'));
    return true;
  };
  const meta = { canvas: [hs.w, hs.h], head: [hs.x, hs.y], base: null, labels: [] };

  // 基线：先把常驻微笑压掉，否则每个格子都带着 0.55 的笑，看不出单个表情的净效果
  await ev(`__xyDbg.emoClear(); window.__xyMorphOverride = null; true`);
  await sleep(900);
  // 找一个"笑"表情来压（有笑い用笑い，没有就用解析出的 smile 槽位名）
  const smileName = await ev(`(function(){var d=__xyDbg.morphs();
    for (var i=0;i<d.length;i++) if (d[i].indexOf('笑い')>=0 || d[i].indexOf('にこり')>=0) return d[i];
    return null;})()`);
  /* ★ 每次必须把**全部** morph 显式写一遍：applyFace 只主动写微笑/眨眼/口型，
     上一轮 override 设过的表情没人复位，会一直挂在那儿（第一版就踩了这个坑，
     结果「怒り」的截图里叠着上一格的「じと目」）。 */
  const baseOv = {};
  for (const n of all) baseOv[n] = 0;
  await ev(`window.__xyMorphOverride = ${JSON.stringify(baseOv)}; true`);
  await sleep(1000);
  meta.base = dump(await ev(`__xyDbg.shot()`), '00_base');
  console.log('基线（全部表情归零，常驻笑 %s 也压掉）', smileName || '无');

  for (let i = 0; i < NAMES.length; i++) {
    const nm = NAMES[i];
    const ov = Object.assign({}, baseOv);
    ov[nm] = 0.8;
    await ev(`window.__xyMorphOverride = ${JSON.stringify(ov)}; true`);
    await sleep(700);
    const name = String(i + 1).padStart(2, '0') + '_' + nm;
    const ok = dump(await ev(`__xyDbg.shot()`), name);
    console.log('%s %s 设 0.8', ok ? '✓' : '✗', nm.padEnd(10));
    meta.labels.push({ label: nm, expr: '0.8', title: nm, head: [0, 0, 0], breath: 1 });
  }

  fs.writeFileSync(path.join(OUT, 'meta.json'), JSON.stringify(meta, null, 2));
  console.log('\n输出目录：' + OUT);
  proc.kill();
  process.exit(0);
}

main().catch((e) => { console.log('ERROR', e); proc.kill(); process.exit(1); });
