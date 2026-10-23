// 列出模型 humanoid 实际骨骼名 + 每根骨的世界坐标（rest 或舞蹈时刻）
// Usage: node tools/vrm_bones.mjs <url> [seekT]
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9347;
const PAGE = process.argv[2];
const SEEK = process.argv[3] ? parseFloat(process.argv[3]) : null;
const proc = spawn(CHROME, ['--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=670,1010', `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_p2', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

let list = null;
for (let i = 0; i < 40; i++) {
  try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list && list.length) break; } catch (e) {}
  await sleep(300);
}
if (!list) { console.log('FATAL cdp'); process.exit(2); }
const t = list.find(x => x.type === 'page');
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let id = 0; const pend = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const send = (m, q = {}) => new Promise(res => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method: m, params: q })); });
const ev = async (x) => (await send('Runtime.evaluate', { expression: x, returnByValue: true })).result?.result?.value;
await send('Runtime.enable'); await send('Page.enable');
await send('Page.navigate', { url: PAGE });
await sleep(22000);
console.log('BONES ' + JSON.stringify(await ev('window.__xyBones ? window.__xyBones() : []')));
if (SEEK !== null) {
  await ev(`window.__xyDance && window.__xyDance.seek(${SEEK})`);
  await sleep(200);
}
const p = await ev('window.__xyProbe ? window.__xyProbe() : null');
console.log('PROBE ' + JSON.stringify(p && p._len) + ' handL=' + JSON.stringify(p && p._handL) + ' handR=' + JSON.stringify(p && p._handR));
process.exit(0);
