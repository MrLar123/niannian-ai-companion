// 真实桌面壳的情绪表情验收：直连 WebView2 远程调试端口，不动后端、不开无头浏览器。
//
// 与 pmx_emo_ab.mjs 的区别：那个是「开一个无头 Chrome 加载页面」做回归；
// 这个连的是**用户眼前那个窗口**（pywebview/WebView2），验证的是真机效果。
// 前置：桌面壳必须带 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9223 启动。
//
// 用法：
//   node tools/pet_emo_live.mjs                  # 8 标签，出图到 TEMP/xy_pet_live
//   node tools/pet_emo_live.mjs http://127.0.0.1:9223
import fs from 'node:fs';
import path from 'node:path';

const BREAK = process.argv[2] || 'http://127.0.0.1:9223';
const OUT = process.env.OUT || path.join(process.env.TEMP || '/tmp', 'xy_pet_live');
const LABELS = ['happy', 'soft', 'annoyed', 'jealous', 'shy', 'sad', 'tired', 'curious'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

const list = await (await fetch(BREAK + '/json/list')).json();
const page = list.find((t) => t.type === 'page' && /PMX/i.test(t.title || ''))
          || list.find((t) => t.type === 'page');
if (!page) { console.log('FAIL 没有可用页面'); process.exit(1); }
console.log('页面: %s | %s', page.title, page.url);

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let id = 0;
const pending = new Map();
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
  const d = r.result;
  if (d && d.exceptionDetails) return { __err: d.exceptionDetails.exception?.description || 'eval error' };
  return d && d.result ? d.result.value : undefined;
};

await send('Runtime.enable');

let ready = null;
for (let i = 0; i < 60; i++) {
  ready = await ev(`(function(){var d=window.__xyDbg;
    return d ? {emo: !!d.emoSet, shot: !!d.shot, head: !!d.headScreen, morph: !!d.morphAll} : null;})()`);
  if (ready && ready.emo && ready.shot && ready.head) break;
  await sleep(500);
}
if (!ready || !ready.emo || !ready.shot) {
  console.log('FAIL 页面诊断口不完整: %s', JSON.stringify(ready));
  process.exit(1);
}
console.log('诊断口: %s', JSON.stringify(ready));

const hs = await ev(`__xyDbg.headScreen()`);
const slots = await ev(`__xyDbg.emo().slots`);
console.log('canvas=%sx%s 头骨=(%s,%s)', hs.w, hs.h, hs.x, hs.y);
console.log('槽位: %s', JSON.stringify(slots));

const dump = (url, name) => {
  if (!url || !url.startsWith('data:image/png;base64,')) return false;
  fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(url.split(',')[1], 'base64'));
  return true;
};

const meta = { canvas: [hs.w, hs.h], head: [hs.x, hs.y], base: null, labels: [] };
await ev(`__xyDbg.emoClear(); true`);
await sleep(1600);
meta.base = dump(await ev(`__xyDbg.shot()`), '00_base');
console.log('基线: %s', meta.base ? 'ok' : 'FAIL');

for (let i = 0; i < LABELS.length; i++) {
  const label = LABELS[i];
  await ev(`__xyDbg.emoClear(); true`);
  await sleep(950);
  await ev(`__xyDbg.emoSet('${label}', 0.8); true`);
  await sleep(1450);
  const st = await ev(`__xyDbg.emo()`);
  const live = await ev(`__xyDbg.morphAll()`);
  const name = String(i + 1).padStart(2, '0') + '_' + label;
  const ok = dump(await ev(`__xyDbg.shot()`), name);
  const lv = st && st.level !== undefined ? Number(st.level).toFixed(2) : '?';
  console.log('%s %-9s lv=%s morph=%s', ok ? '✓' : '✗', label, lv, JSON.stringify(live));
  meta.labels.push({
    label, title: label,
    expr: Object.entries(live || {}).map(([k, v]) => k + '=' + v).join(' ') || '—',
    head: [st?.pose?.headX || 0, 0, st?.pose?.headZ || 0],
    breath: st?.pose?.breath || 1,
  });
}

fs.writeFileSync(path.join(OUT, 'meta.json'), JSON.stringify(meta, null, 2));
console.log('-> %s', OUT);
ws.close();
process.exit(0);
