// 打开真实桌宠页面，检查口型 morph 是否被识别、有无 JS 报错
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9334;
const PAGE = process.argv[2] || 'http://127.0.0.1:8090/desktoppmx';

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=720,1200',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + process.env.TEMP + '/xy_ls_prof2',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 30; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) {}
    await sleep(300);
  }
  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0; const pending = new Map(); const errors = []; const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
    if (m.method === 'Runtime.consoleAPICalled') {
      logs.push(m.params.type + ': ' + m.params.args.map(a => a.value ?? a.description ?? '').join(' '));
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: PAGE });
  await sleep(16000);

  const probe = await send('Runtime.evaluate', {
    expression: `JSON.stringify({
      mouthReady: window.__xyMouthReady === undefined ? null : window.__xyMouthReady,
      hasEngine: !!window.XiaoyunLipSync,
      speaking: !!window.xiaoyunSpeaking,
      visemes: (typeof mVowels !== 'undefined') ? mVowels : 'not-in-global',
      err: window.__lastErr || null
    })`,
    returnByValue: true
  });
  console.log('探针 =', probe.result?.result?.value);

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result?.data) {
    const out = 'tools/pmx_page_check.png';
    fs.writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
    console.log('截图 =', out);
  }

  console.log('--- 控制台 ---');
  logs.slice(-12).forEach(l => console.log(l));
  console.log('--- 异常 ---');
  errors.length ? errors.slice(0, 5).forEach(e => console.log(String(e).slice(0, 300))) : console.log('无');

  ws.close(); proc.kill();
}

main().catch(e => { console.error('FAIL', e.message); proc.kill(); process.exit(1); });
