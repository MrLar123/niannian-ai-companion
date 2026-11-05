// PMX 情绪表情验收：8 个标签逐个设置，量状态 + 出图（人工目视 + 数值双通道）。
//
// 与 face_shots.mjs（VRM 版）同构，只是接口换成 __xyDbg.emoSet / emo()。
// 输出目录结构与 face_shots 一致，可直接丢给 tools/make_contact_sheet.py 拼图。
//
// 用法：
//   node tools/pmx_emo_ab.mjs [modelKey] [pageBase]
// 例：node tools/pmx_emo_ab.mjs elysia
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9377;
const KEY = process.argv[2] || 'elysia';
const BASE = process.argv[3] || 'http://127.0.0.1:8765';
const OUT = process.env.OUT || path.join(process.env.TEMP || '/tmp', 'xy_pmx_emo_' + KEY);
const LABELS = ['happy', 'soft', 'annoyed', 'jealous', 'shy', 'sad', 'tired', 'curious'];
const W = 620, H = 940;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  `--window-size=${W},${H}`,
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_pmx_ab_prof',
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
    if (r.result?.exceptionDetails) {
      return { __err: r.result.exceptionDetails.exception?.description || 'eval error' };
    }
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `${BASE}/desktoppmx?model=${encodeURIComponent(KEY)}` });

  let ready = false;
  for (let i = 0; i < 200; i++) {
    const ok = await ev(`!!(window.__xyDbg && __xyDbg.emo && __xyDbg.headScreen && __xyDbg.headScreen())`);
    if (ok === true) { ready = true; break; }
    await sleep(300);
  }
  if (!ready) {
    console.log('FAIL 模型/情绪口未就绪：' + JSON.stringify(await ev(
      `({dbg: typeof window.__xyDbg, keys: window.__xyDbg ? Object.keys(__xyDbg) : null})`)));
    for (const e of errors.slice(0, 4)) console.log('  ' + String(e).split('\n')[0]);
    proc.kill(); process.exit(1);
  }
  await sleep(700);

  const hs = await ev(`__xyDbg.headScreen()`);
  const slots = await ev(`__xyDbg.emo().slots`);
  console.log('模型 %s  canvas=%sx%s  头骨=(%s,%s)', KEY, hs.w, hs.h, hs.x, hs.y);
  console.log('槽位: %s', JSON.stringify(slots));

  const dump = (url, name) => {
    if (!url || !url.startsWith('data:image/png;base64,')) return false;
    fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(url.split(',')[1], 'base64'));
    return true;
  };

  const meta = { canvas: [hs.w, hs.h], head: [hs.x, hs.y], base: null, labels: [] };
  await ev(`__xyDbg.emoClear(); true`);
  await sleep(1500);
  meta.base = dump(await ev(`__xyDbg.shot()`), '00_base');

  const rows = [];
  for (let i = 0; i < LABELS.length; i++) {
    const label = LABELS[i];
    await ev(`__xyDbg.emoClear(); true`);
    await sleep(950);                      // 回落 0.55s，留足余量再切下一个
    await ev(`__xyDbg.emoSet('${label}', 0.8); true`);
    await sleep(1450);
    const st = await ev(`__xyDbg.emo()`);
    const live = await ev(`__xyDbg.morphAll()`);   // 实际落到 morph 上的权重（含常驻笑/眨眼）
    const name = String(i + 1).padStart(2, '0') + '_' + label;
    const ok = dump(await ev(`__xyDbg.shot()`), name);
    const expr = Object.entries(st.cur || {}).filter(([, v]) => v > 0.01)
      .map(([k, v]) => `${k}=${v.toFixed(2)}`).join(' ') || '—';
    const pose = st.pose || {};
    console.log('%s %s | %s | headX=%s headZ=%s eyeY=%s breath=%s',
      ok ? '✓' : '✗', label.padEnd(8), expr.padEnd(36),
      (pose.headX || 0).toFixed(2), (pose.headZ || 0).toFixed(2),
      (pose.gazePitch || 0).toFixed(2), (pose.breath || 1).toFixed(2));
    console.log('   morph=%s', JSON.stringify(live));
    rows.push({ label, expr, head: [pose.headX || 0, 0, pose.headZ || 0],
                eye: [pose.gazePitch || 0, pose.gazeYaw || 0],
                breath: pose.breath || 1 });
    meta.labels.push({ label, expr, title: label,
                       head: [pose.headX || 0, 0, pose.headZ || 0],
                       breath: pose.breath || 1 });
  }

  // ---- 断言：每个标签至少有一个槽位真的落到 morph 上 ----
  const fails = [];
  for (const r of rows) {
    if (r.expr === '—') fails.push(`${r.label} 没有任何表情权重`);
  }
  const missSlots = Object.entries(slots).filter(([, v]) => v < 0).map(([k]) => k);
  if (missSlots.length) console.log('\n该模型缺少槽位（配方会自动降级）：%s', missSlots.join(', '));

  fs.writeFileSync(path.join(OUT, 'meta.json'), JSON.stringify(meta, null, 2));
  if (errors.length) {
    console.log('\n页面异常：');
    for (const e of errors.slice(0, 4)) console.log('  ' + String(e).split('\n')[0]);
  }
  console.log('\n输出目录：' + OUT);
  console.log(fails.length ? 'FAIL\n  - ' + fails.join('\n  - ') : 'PASS');
  proc.kill();
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.log('ERROR', e); proc.kill(); process.exit(1); });
