// desktopvrm2 interactivity probe: gaze smoothing + poke squash + framing.
// All assertions BEFORE the screenshot (SwiftShader crash guard).
// Usage: node tools/vrm2_interact.mjs <page-url> <out-png>
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9339;
const PAGE = process.argv[2] || 'http://127.0.0.1:8097/desktopvrm2';
const OUT = process.argv[3] || (process.env.TEMP || '/tmp') + '/vrm2_interact.png';

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=670,1010',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_vrm2_prof',
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

  // wait until model loaded (boneHome populated => head() has values)
  let ready = false;
  for (let i = 0; i < 60; i++) {
    await sleep(700);
    const h = await evalJs('window.__xyDbg && window.__xyDbg.head() ? "ok" : "no"');
    if (h === 'ok') { ready = true; break; }
  }
  console.log('READY ' + ready);
  if (!ready) { console.log('FATAL model not loaded'); ws.close(); proc.kill(); process.exit(3); }

  // let autofit settle
  await sleep(2500);

  const geo = await evalJs('JSON.stringify({pos: window.__xyDbg.rigPos(), baseY: window.__xyDbg.baseY(), scale: window.__xyDbg.rigScale()})');
  console.log('GEO ' + geo);

  // ---- 1) gaze: freeze server input, inject target yaw, verify smoothing + bone response ----
  await evalJs('window.__xyDbg.holdGaze(true); "hold"');
  const h0 = await evalJs('JSON.stringify(window.__xyDbg.head())');
  await evalJs('window.__xyDbg.setGaze(0.22, 0.10); "set"');
  await sleep(900);
  const g1 = await evalJs('JSON.stringify(window.__xyDbg.gaze())');
  const h1 = await evalJs('JSON.stringify(window.__xyDbg.head())');
  console.log('GAZE ' + g1);
  console.log('HEAD0 ' + h0);
  console.log('HEAD1 ' + h1);

  // release: back to server-driven input
  await evalJs('window.__xyDbg.setGaze(0, 0); window.__xyDbg.holdGaze(false); "release"');
  await sleep(600);

  // ---- 2) poke: prime seq then fire, sample squash mid-animation ----
  const s0 = await evalJs('JSON.stringify(window.__xyDbg.rigScale())');
  await evalJs('window.__xyVrmPoke.feed({seq:1,x:0.5,y:0.85}); "prime"');
  await evalJs('window.__xyVrmPoke.feed({seq:2,x:0.5,y:0.85}); "fire"');
  await sleep(140);
  const ps = await evalJs('JSON.stringify(window.__xyVrmPoke.feed ? window.__xyDbg.poke() : null)');
  const s1 = await evalJs('JSON.stringify(window.__xyDbg.rigScale())');
  const ph = await evalJs('JSON.stringify(window.__xyDbg.head())');
  console.log('POKESCALE0 ' + s0);
  console.log('POKESTATE ' + ps);
  console.log('POKESCALE1 ' + s1);
  console.log('POKEHEAD ' + ph);

  // ---- 3) after animation ends, scale must return to baseline ----
  await sleep(900);
  const s2 = await evalJs('JSON.stringify(window.__xyDbg.rigScale())');
  console.log('POKESCALE2 ' + s2);

  const errs = errors.filter(e => !/favicon|net::ERR/i.test(e));
  console.log('JSERR ' + errs.length + (errs.length ? ' :: ' + errs.slice(0, 4).join(' | ') : ''));

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result?.data) {
    fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
    console.log('SHOT ' + OUT);
  } else {
    console.log('SHOT-FAIL');
  }
  try { ws.close(); } catch (e) {}
}

main().catch(e => { console.log('FATAL ' + (e.message || e)); process.exitCode = 2; })
  .finally(() => { try { proc.kill(); } catch (e) {} });
