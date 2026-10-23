// 骨骼数值探针：加载页面 -> seek 到指定时刻 -> 输出 __xyProbe() 的世界坐标
// Usage: node tools/vrm_probe.mjs <url> <seekT>
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9344;
const PAGE = process.argv[2];
const SEEK = parseFloat(process.argv[3] || '6');

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=670,1010',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_vrm_probe',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list?.length) break; } catch (e) {}
    await sleep(300);
  }
  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
  const evalJs = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true })).result?.result?.value;
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: PAGE });
  await sleep(24000);
  console.log('DANCE ' + JSON.stringify(await evalJs('window.__xyDance ? window.__xyDance.state() : {}')));
  await evalJs(`window.__xyDance && window.__xyDance.seek(${SEEK})`);
  await sleep(200);
  const p = await evalJs('window.__xyProbe ? window.__xyProbe() : null');
  console.log('PROBE t=' + SEEK);
  console.log(JSON.stringify(p, null, 1));
}
main().catch(e => { console.log('FATAL ' + (e.message || e)); process.exitCode = 2; })
  .finally(() => { try { proc.kill(); } catch (e) {} });
