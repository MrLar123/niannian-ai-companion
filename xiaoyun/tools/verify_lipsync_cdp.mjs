// 用 CDP 驱动无头 Chrome，真实播放一段 TTS 并采样口型权重
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9333;
const URL_PAGE = 'http://127.0.0.1:8090/assets/lipsync/test.html';

const proc = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--mute-audio',
  '--autoplay-policy=no-user-gesture-required',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + process.env.TEMP + '/xy_ls_prof',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 25; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) { /* 还没起来 */ }
    await sleep(300);
  }
  if (!list || !list.length) throw new Error('chrome 未就绪');

  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: URL_PAGE });
  await sleep(1800);

  const expr = `(async () => {
    const dict = await XiaoyunLipSync.init({ dictUrl: '/assets/lipsync/zh-visemes.json' });
    const au = new Audio('/assets/lipsync/sample.mp3');
    const text = '你好呀，我是小芸。今天想陪你聊点什么？';
    const ctl = XiaoyunLipSync.attach(au, text, { debug: true });
    await new Promise(r => { au.onloadedmetadata = r; au.load(); });
    await au.play();
    const out = [];
    for (let i = 0; i < 55; i++) {
      await new Promise(r => setTimeout(r, 70));
      out.push([+au.currentTime.toFixed(2), ctl.ready ? 1 : 0, ctl.keys || 0,
                +ctl.weights.open.toFixed(2), ctl.viseme,
                +ctl.weights.a.toFixed(2), +ctl.weights.i.toFixed(2),
                +ctl.weights.u.toFixed(2), +ctl.weights.e.toFixed(2),
                +ctl.weights.o.toFixed(2)]);
      if (au.ended) break;
    }
    au.pause();
    return JSON.stringify({ dur: +au.duration.toFixed(2), mode: ctl.mode || 'viseme',
                            ready: ctl.ready, keys: ctl.keys, samples: out });
  })()`;

  const res = await send('Runtime.evaluate', {
    expression: expr, awaitPromise: true, returnByValue: true
  });

  if (res.result?.exceptionDetails) {
    console.log('JS 异常:', JSON.stringify(res.result.exceptionDetails).slice(0, 500));
  } else {
    const v = res.result?.result?.value;
    const j = JSON.parse(v);
    console.log('音频时长 =', j.dur, 's   模式 =', j.mode, '  就绪 =', j.ready, '  关键帧 =', j.keys);
    console.log('t     open  vis  a    i    u    e    o');
    for (const s of j.samples) {
      console.log(String(s[0]).padEnd(6), String(s[3]).padEnd(5), String(s[4]).padEnd(4),
                  String(s[5]).padEnd(4), String(s[6]).padEnd(4), String(s[7]).padEnd(4),
                  String(s[8]).padEnd(4), s[9]);
    }
    const moving = j.samples.filter(s => s[3] > 0.05).length;
    const distinct = new Set(j.samples.map(s => s[4])).size;
    console.log(`\n开口帧 = ${moving}/${j.samples.length}   出现的口型种类 = ${distinct}`);
  }

  ws.close();
  proc.kill();
}

main().catch(e => { console.error('FAIL', e.message); proc.kill(); process.exit(1); });
