// 情绪表情目视验收：8 个标签逐个出 PNG，给眼睛看"像不像"。
//
// 与 face_ab.mjs 的分工：
//   face_ab.mjs  → 量数值（表情权重/姿态/灰度差），断言用，跑完不留文件；
//   face_shots.mjs → 出图（1440 级人脸特写），人工目视用。
//
// ★ 为什么走 __xyDbg.shot 而不是 CDP Page.captureScreenshot：
//   SwiftShader 下 captureScreenshot 会把渲染进程搞崩（页面重载、fetch 垫片全丢）。
//   renderer 建的时候带了 preserveDrawingBuffer，页内 canvas.toDataURL 稳定且不惊动浏览器。
//
// 用法：
//   node tools/face_shots.mjs [pageUrl] [outDir]
// 默认 pageUrl = http://127.0.0.1:8765/desktopvrm?model=小芸
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9373;
const PAGE = process.argv[2] || 'http://127.0.0.1:8765/pages/desktopvrm.html?model=%E5%B0%8F%E8%8A%B8';
const OUT = process.argv[3] || path.join(process.env.TEMP || '/tmp', 'xy_face_shots');
const LABELS = ['happy', 'soft', 'annoyed', 'jealous', 'shy', 'sad', 'tired', 'curious'];
const W = 620, H = 940;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  `--window-size=${W},${H}`,
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_face_shots_prof',
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
    if (r.result?.exceptionDetails) {
      return { __err: r.result.exceptionDetails.exception?.description || 'eval error' };
    }
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: PAGE });

  let ready = false;
  for (let i = 0; i < 160; i++) {
    const ok = await ev(`!!(window.__xyDbg && __xyDbg.ready && __xyDbg.ready())`);
    if (ok === true) { ready = true; break; }
    await sleep(300);
  }
  if (!ready) {
    console.log('FAIL 模型未加载完成：' + await ev(`JSON.stringify({
      dbg: typeof window.__xyDbg, shot: window.__xyDbg && typeof __xyDbg.shot,
      ready: (window.__xyDbg && __xyDbg.ready) ? __xyDbg.ready() : 'n/a' })`));
    for (const e of errors.slice(0, 3)) console.log('  ' + String(e).split('\n')[0]);
    proc.kill(); process.exit(1);
  }
  await sleep(800);

  const hs = await ev(`__xyDbg.headScreen()`);
  console.log('canvas=%sx%s 头骨屏幕坐标=(%s, %s)', hs?.w, hs?.h, hs?.x, hs?.y);

  const dump = (url, name) => {
    if (!url || !url.startsWith('data:image/png;base64,')) {
      console.log('  ✗ %s 没有拿到图（%s）', name, String(url).slice(0, 60));
      return false;
    }
    fs.writeFileSync(path.join(OUT, name + '.png'),
                     Buffer.from(url.split(',')[1], 'base64'));
    return true;
  };

  // 基线
  await ev(`__xyFace.set(null, 0, null, 'base'); __xyFace.holdUntil = -1; true`);
  await sleep(1400);
  const meta = { canvas: [hs.w, hs.h], head: [hs.x, hs.y], base: null, labels: [] };
  meta.base = dump(await ev(`__xyDbg.shot()`), '00_base');

  for (let i = 0; i < LABELS.length; i++) {
    const label = LABELS[i];
    await ev(`__xyFace.set(null, 0, null, 'clear'); __xyFace.holdUntil = -1; true`);
    await sleep(900);
    await ev(`__xyFace.set('${label}', 0.8, null, 'shot'); __xyFace.holdUntil = Infinity; true`);
    await sleep(1400);
    const st = await ev(`__xyDbg.face()`);
    const rot = await ev(`__xyDbg.headRot()`);
    const name = String(i + 1).padStart(2, '0') + '_' + label;
    const ok = dump(await ev(`__xyDbg.shot()`), name);
    const expr = Object.entries(st?.cur || {}).filter(([, v]) => v > 0.01)
      .map(([k, v]) => `${k}=${v.toFixed(2)}`).join(' ');
    console.log('%s %s | %s | headX=%s headZ=%s eyeY=%s breath=%s',
      ok ? '✓' : '✗', label.padEnd(8), (expr || '—').padEnd(34),
      rot?.head?.[0], rot?.head?.[2], rot?.eye?.[1], (st?.pose?.breath ?? 1).toFixed(2));
    meta.labels.push({ label, expr, head: rot?.head, eye: rot?.eye,
                       breath: st?.pose?.breath ?? 1, title: label });
  }

  fs.writeFileSync(path.join(OUT, 'meta.json'), JSON.stringify(meta, null, 2));
  if (errors.length) {
    console.log('\n页面异常：');
    for (const e of errors.slice(0, 3)) console.log('  ' + String(e).split('\n')[0]);
  }
  console.log('\n输出目录：' + OUT);
  proc.kill();
  process.exit(0);
}

main().catch((e) => { console.log('ERROR', e); proc.kill(); process.exit(1); });
