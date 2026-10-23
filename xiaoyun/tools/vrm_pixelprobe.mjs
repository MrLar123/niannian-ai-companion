// Pixel probe: load page, read canvas pixels at normalized positions after settle.
// Usage: node tools/vrm_pixelprobe.mjs <page-url>
// Reads 5x5 average at: face(0.5,0.30) arm(0.13,0.42) chest(0.5,0.47) skirt(0.5,0.63) legL(0.44,0.80) legR(0.56,0.80) hand(0.05,0.55)
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9339;
const PAGE = process.argv[2] || 'http://127.0.0.1:8096/bjd3dvr';

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
  if (!list) { console.log('FATAL cdp'); process.exit(2); }
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
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: PAGE });
  await sleep(24000);

  const out = await evalJs(`(function(){
    const cv = document.querySelector('canvas');
    if (!cv) return 'no canvas';
    const gl = cv.getContext('webgl2') || cv.getContext('webgl');
    if (!gl) return 'no gl';
    const W = gl.drawingBufferWidth, H = gl.drawingBufferHeight;
    const buf = new Uint8Array(W * H * 4);
    gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    const spots = { face:[0.50,0.30], armL:[0.13,0.42], chest:[0.50,0.47],
                    skirt:[0.50,0.63], legL:[0.44,0.80], legR:[0.56,0.80] };
    const res = { W: W, H: H };
    for (const k in spots) {
      const nx = spots[k][0], ny = spots[k][1];
      const cx = Math.round(nx * W), cy = Math.round((1 - ny) * H);  // gl y-up
      let r=0,g=0,b=0,a=0,n=0;
      for (let dy=-2; dy<=2; dy++) for (let dx=-2; dx<=2; dx++) {
        const x = Math.min(W-1, Math.max(0, cx+dx)), y = Math.min(H-1, Math.max(0, cy+dy));
        const o = (y*W+x)*4;
        r+=buf[o]; g+=buf[o+1]; b+=buf[o+2]; a+=buf[o+3]; n++;
      }
      res[k] = [Math.round(r/n), Math.round(g/n), Math.round(b/n), Math.round(a/n)];
    }
    return JSON.stringify(res);
  })()`);
  console.log('PIX ' + out);
}

main().catch(e => { console.log('FATAL ' + (e.message || e)); process.exitCode = 2; })
  .finally(() => { try { proc.kill(); } catch (e) {} });
