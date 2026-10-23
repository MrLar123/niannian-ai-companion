// Verify VMD dance playback on VRM page: sample bone quaternions over time.
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9351;
const PAGE = process.argv[2];
const OUT = process.argv[3];

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=1200,1200',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_dance',
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
  await send('Emulation.setDeviceMetricsOverride', { width: 670, height: 1010, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: PAGE });

  for (let i = 0; i < 70; i++) {
    await sleep(700);
    if (await ev('window.__xyDbg && window.__xyDbg.head() ? "ok" : "no"') === 'ok') break;
  }
  await sleep(2500);

  // baseline before dance
  console.log('BEFORE ' + await ev('JSON.stringify(window.__xyDbg.head())'));
  console.log('DANCE_PLAY ' + await ev('(window.__xyDance && window.__xyDance.play("changchangyao"), "sent")'));
  await sleep(3500);
  console.log('DANCE_STATE ' + await ev('JSON.stringify(window.__xyDance && window.__xyDance.state())'));
  console.log('AFTER1 ' + await ev('JSON.stringify(window.__xyDbg.head())'));
  await sleep(1200);
  console.log('AFTER2 ' + await ev('JSON.stringify(window.__xyDbg.head())'));
  await shot(OUT);
  ws.close();
}

main().catch(e => { console.log('FATAL ' + e.message); }).finally(() => { try { proc.kill(); } catch (e) {} });
