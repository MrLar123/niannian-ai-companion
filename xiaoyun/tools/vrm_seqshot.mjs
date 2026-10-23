// 定时刻连帧截图：加载后 24s，然后对每个 seek 时刻 seek(t) 后立即截图。
// Usage: node tools/vrm_seqshot.mjs <url> <out-prefix> <seekList e.g. "2,4,6,8">
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9338;
const PAGE = process.argv[2] || 'http://127.0.0.1:8090/desktopvrm';
const PREFIX = process.argv[3] || (process.env.TEMP || '/tmp') + '/seq';
const SEEKS = (process.argv[4] || '2,4,6,8').split(',').map(Number).filter(v => isFinite(v));

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=670,1010',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_vrm_prof',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) {}
    await sleep(300);
  }
  if (!list) { console.log('FATAL chrome cdp not up'); process.exit(2); }
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
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    return r.result?.result?.value;
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: PAGE });
  await sleep(24000);
  console.log('DANCE ' + JSON.stringify(await evalJs('window.__xyDance ? window.__xyDance.state() : {}')));
  for (let i = 0; i < SEEKS.length; i++) {
    await evalJs(`window.__xyDance && window.__xyDance.seek(${SEEKS[i]})`);
    await sleep(120);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    if (shot.result?.data) {
      fs.writeFileSync(`${PREFIX}_${i}.png`, Buffer.from(shot.result.data, 'base64'));
      console.log('SHOT t=' + SEEKS[i]);
    }
  }
}

main().catch(e => { console.log('FATAL ' + (e.message || e)); process.exitCode = 2; })
  .finally(() => { try { proc.kill(); } catch (e) {} });
