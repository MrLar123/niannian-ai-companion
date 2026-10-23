// VRM preview in headless Chrome: load /bjd3dvr?model=... and screenshot.
//   Rules honored: screenshot AFTER all checks (SwiftShader crash guard);
//   one process per run; kills chrome before exit (no resident child).
// Usage: node tools/vrm_preview.mjs <page-url> <out-png>
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9338;
const PAGE = process.argv[2] || 'http://127.0.0.1:8096/bjd3dvr';
const OUT = process.argv[3] || (process.env.TEMP || '/tmp') + '/vrm_preview.png';

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
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: PAGE });

  // wait for canvas + settle (swiftshader is slow)
  await sleep(24000);

  const info = await evalJs(`JSON.stringify((function(){
    const cv = document.querySelector('canvas');
    return {
      canvas: !!cv,
      cw: cv ? cv.width : 0, ch: cv ? cv.height : 0,
      dpr: window.devicePixelRatio,
      title: document.title
    };
  })())`);
  console.log('INFO page ' + info);
  const errs = errors.filter(e => !/favicon|net::ERR/i.test(e));
  console.log('JSERR ' + errs.length + (errs.length ? ' :: ' + errs.slice(0, 4).join(' | ') : ''));

  // screenshot LAST (crash guard)
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result?.data) {
    fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
    console.log('SHOT ' + OUT);
  } else {
    console.log('SHOT-FAIL');
  }
}

main().catch(e => { console.log('FATAL ' + (e.message || e)); process.exitCode = 2; })
  .finally(() => { try { proc.kill(); } catch (e) {} });
