// 诊断：桌宠页面是否陷入 location.reload() 死循环
// 统计 15 秒内的 /api/models、/api/ui/rects 请求数、导航次数、JS 异常
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9335;
const PAGE = process.argv[2] || 'http://127.0.0.1:8090/desktoppmx';
const RUN_MS = Number(process.argv[3] || 15000);

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=720,1200',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + process.env.TEMP + '/xy_loop_prof',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) { }
    await sleep(300);
  }
  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0; const pending = new Map();
  const errors = [], consoles = [];
  const reqCount = {}; let navs = 0;

  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
    if (m.method === 'Runtime.consoleAPICalled') {
      consoles.push(m.params.type + ': ' + m.params.args.map(a => a.value ?? a.description ?? '').join(' '));
    }
    if (m.method === 'Network.requestWillBeSent') {
      const u = m.params.request.url.replace(/^https?:\/\/[^/]+/, '');
      const key = u.split('?')[0];
      reqCount[key] = (reqCount[key] || 0) + 1;
    }
    if (m.method === 'Page.frameNavigated' && !m.params.frame.parentId) navs++;
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Network.enable');
  await send('Page.navigate', { url: PAGE });

  const t0 = Date.now();
  const marks = [];
  for (let i = 0; i < Math.floor(RUN_MS / 3000); i++) {
    await sleep(3000);
    marks.push(`+${Math.round((Date.now() - t0) / 1000)}s rects=${reqCount['/api/ui/rects'] || 0} models=${reqCount['/api/models'] || 0} nav=${navs}`);
  }

  console.log('=== 时间线 ===');
  marks.forEach(m => console.log('  ' + m));
  console.log('=== 请求排行（前 8）===');
  Object.entries(reqCount).sort((a, b) => b[1] - a[1]).slice(0, 8)
    .forEach(([k, v]) => console.log(`  ${String(v).padStart(5)}  ${k}`));
  console.log('=== 导航次数 =', navs, '===');

  const probe = await send('Runtime.evaluate', {
    expression: `JSON.stringify({
       curKey: (typeof CUR_MODEL_KEY !== 'undefined') ? CUR_MODEL_KEY : 'undef',
       mouthReady: window.__xyMouthReady === undefined ? null : window.__xyMouthReady,
       hasLip: !!window.XiaoyunLipSync,
       dict: (window.XiaoyunLipSync && window.XiaoyunLipSync.dictSize) || null
     })`, returnByValue: true
  });
  console.log('=== 页面状态 ===');
  console.log('  ' + (probe.result?.result?.value || 'null'));

  console.log('=== JS 异常（前 5）===');
  errors.length ? errors.slice(0, 5).forEach(e => console.log('  ' + String(e).slice(0, 240))) : console.log('  无');
  console.log('=== 控制台（末 6）===');
  consoles.slice(-6).forEach(l => console.log('  ' + String(l).slice(0, 200)));

  ws.close(); proc.kill();
}

main().catch(e => { console.error('FAIL', e.message); proc.kill(); process.exit(1); });
