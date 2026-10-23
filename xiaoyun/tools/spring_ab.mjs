// A/B: does spring bone physics cause the bangs to lift? Two separate screenshots.
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9347;
const PAGE = process.argv[2];
const OUT_ON = process.argv[3];
const OUT_OFF = process.argv[4];

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=1200,1200',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_spring_ab',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list && list.length) break; } catch (e) {}
    await sleep(300);
  }
  const tab = list.find(x => x.type === 'page');
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });

  let id = 0; const pending = new Map();
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise(res => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) {
      return { __err: r.result.exceptionDetails.exception?.description || 'err' };
    }
    return r.result?.result?.value;
  };
  const shot = async (out) => {
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(out, Buffer.from(s.result.data, 'base64'));
    console.log('SHOT ' + out);
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 670, height: 1010, deviceScaleFactor: 1, mobile: false
  });
  await send('Page.navigate', { url: PAGE });

  for (let i = 0; i < 70; i++) {
    await sleep(700);
    if (await ev('window.__xyDbg && window.__xyDbg.head() ? "ok" : "no"') === 'ok') break;
  }
  await sleep(3000);

  // 1) physics ON (normal)
  console.log('SPRING_STATE ' + JSON.stringify(await ev('window.__xyDbg.spring(true)')));
  await sleep(1200);
  await shot(OUT_ON);

  // 2) physics OFF (frozen at current pose, then let it settle a moment)
  console.log('SPRING_OFF ' + JSON.stringify(await ev('window.__xyDbg.spring(false)')));
  await sleep(1500);
  await shot(OUT_OFF);

  ws.close();
}

main().catch(e => { console.log('FATAL ' + e.message); }).finally(() => { try { proc.kill(); } catch (e) {} });
