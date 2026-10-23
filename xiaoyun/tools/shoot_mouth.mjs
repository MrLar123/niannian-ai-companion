// 口型形变可视验证：闭嘴 / あ / い / う 各截一张嘴部特写
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9335;

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=720,1200',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + process.env.TEMP + '/xy_ls_prof3',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 30; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list.length) break; }
    catch (e) {}
    await sleep(300);
  }
  const ws = new WebSocket(list.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise(res => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: 'http://127.0.0.1:8090/desktoppmx' });
  await sleep(16000);

  const states = {
    closed: { a: 0, i: 0, u: 0, e: 0, o: 0 },
    a: { a: 1, i: 0, u: 0, e: 0, o: 0 },
    i: { a: 0, i: 1, u: 0, e: 0, o: 0 },
    u: { a: 0, i: 0, u: 1, e: 0, o: 0 }
  };

  for (const [name, w] of Object.entries(states)) {
    await send('Runtime.evaluate', {
      expression: `window.__xyMouthOverride = ${JSON.stringify(w)};`
    });
    await sleep(700);
    const shot = await send('Page.captureScreenshot', {
      format: 'png',
      clip: { x: 268, y: 150, width: 190, height: 120, scale: 3 }
    });
    fs.writeFileSync(`tools/mouth_${name}.png`, Buffer.from(shot.result.data, 'base64'));
    console.log('saved tools/mouth_' + name + '.png');
  }
  ws.close(); proc.kill();
}

main().catch(e => { console.error('FAIL', e.message); proc.kill(); process.exit(1); });
