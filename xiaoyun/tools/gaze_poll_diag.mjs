// 定点诊断：轮询链路是否在连续 aim 之后失效
import { spawn } from 'node:child_process';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9341, OUT = process.env.TEMP || '/tmp';
const URL = process.argv[2] || 'http://127.0.0.1:8090/desktoppmx';

const proc = spawn(CHROME, ['--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=560,940', `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + OUT + '/xy_poll_diag', 'about:blank'], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list && list.length) break; } catch (e) {}
    await sleep(300);
  }
  const ws = new WebSocket(list.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const errs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  };
  const send = (method, params = {}) => new Promise(res => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
  const evalJs = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
    return r.result?.result?.value;
  };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: URL });
  await sleep(17000);

  // 装 shim + 计数器：记录 poll 被调次数
  await evalJs(`(function(){
    window.__xyCursor={ok:true,x:280,y:470,rect:[0,0,560,940],age:0,mode:'always'};
    if(!window.__xyOrigFetch) window.__xyOrigFetch=window.fetch;
    window.__xyFetchCount=0;
    window.fetch=function(u){var url=(typeof u==='string')?u:(u&&u.url)||'';
      if(url.indexOf('/api/gaze')===0){window.__xyFetchCount++;
        return Promise.resolve(new Response(JSON.stringify(window.__xyCursor),{status:200,headers:{'Content-Type':'application/json'}}));}
      return window.__xyOrigFetch.apply(window,arguments);};
    return 'ok';})()`);

  const probe = async (tag, x, y) => {
    await evalJs(`(function(){var c=window.__xyCursor;c.x=${x};c.y=${y};c.age=0;return 1})()`);
    await sleep(1100);
    const s = await evalJs(`JSON.stringify({
      tYaw:+window.__xyDbg.gaze.tYaw.toFixed(4), tPitch:+window.__xyDbg.gaze.tPitch.toFixed(4),
      yaw:+window.__xyDbg.gaze.yaw.toFixed(4), mode:window.__xyDbg.gaze.mode,
      hasBones:window.__xyDbg.gaze.hasBones, enabled:window.__xyDbg.gaze.enabled,
      fetchCount:window.__xyFetchCount,
      cursor:window.__xyCursor, vis:document.visibilityState,
      fetchIsShim: String(window.fetch).indexOf('__xyFetchCount')>=0 })`);
    console.log(tag.padEnd(10) + s);
    return s;
  };

  await probe('right', 1100, 470);
  await probe('left', -500, 470);
  await probe('down', 280, 1500);
  await probe('up', 280, -400);
  await probe('nearright', 350, 470);   // 关键：这一步历史上归零
  await probe('farright', 4000, 470);
  // 手动触发一次 poll，看是否恢复
  await evalJs(`window.__xyDbg.gaze.poll()`);
  await sleep(200);
  const manual = await evalJs(`JSON.stringify({tYaw:+window.__xyDbg.gaze.tYaw.toFixed(4),fetchCount:window.__xyFetchCount})`);
  console.log('manualPoll ' + manual);
  // 截图后再探一次：验证 Page.captureScreenshot 是否会让页面重载/停摆
  const tOrigin1 = await evalJs(`performance.timeOrigin`);
  await send('Page.captureScreenshot', { format: 'png' });
  await sleep(1200);
  const afterShot = await evalJs(`JSON.stringify({
    tOrigin:performance.timeOrigin, shim: !!window.__xyCursor,
    tYaw:+window.__xyDbg.gaze.tYaw.toFixed(4), fetchCount:window.__xyFetchCount })`);
  console.log('afterShot ' + afterShot + '  timeOriginBefore=' + tOrigin1);
  console.log('ERRORS ' + JSON.stringify(errs.slice(0, 5)));
  proc.kill(); process.exit(0);
}
main().catch(e => { console.error('FATAL ' + e); proc.kill(); process.exit(2); });
