// Verify framing at the REAL pet window size (670x1010, no browser chrome).
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9345;
const PAGE = process.argv[2] || 'http://127.0.0.1:8098/desktopvrm';
const OUT = process.argv[3] || (process.env.TEMP || '/tmp') + '/vrm_petsize.png';

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=1200,1200',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_petsize',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list && list.length) break; } catch (e) {}
    await sleep(300);
  }
  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    return r.result?.exceptionDetails ? { __err: r.result.exceptionDetails.exception?.description } : r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 670, height: 1010, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: PAGE });

  for (let i = 0; i < 60; i++) {
    await sleep(700);
    if (await ev('window.__xyDbg && window.__xyDbg.head() ? "ok" : "no"') === 'ok') break;
  }
  await sleep(3000);
  console.log('VIEWPORT ' + await ev('JSON.stringify({w:innerWidth,h:innerHeight})'));
  console.log('CAM ' + await ev('JSON.stringify(window.__xyDbg.cam())'));
  console.log('SILH ' + await ev('JSON.stringify(window.__xyDbg.silh())'));
  console.log('MATS ' + await ev('JSON.stringify(window.__xyDbg.mats())'));
  const errs = errors.filter(e => !/favicon|net::ERR/i.test(e));
  console.log('JSERR ' + errs.length + (errs.length ? ' :: ' + errs[0] : ''));

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result?.data) { fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64')); console.log('SHOT ' + OUT); }
  try { ws.close(); } catch (e) {}
}

main().catch(e => { console.log('FATAL ' + (e.message || e)); process.exitCode = 2; })
  .finally(() => { try { proc.kill(); } catch (e) {} });
