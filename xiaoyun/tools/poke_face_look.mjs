// 表情观感对照截图：无表情 / 受惊 / 嫌弃，各截一张。
//
//  ★ 为什么一个状态一个进程：Page.captureScreenshot 在 SwiftShader 下会把渲染进程
//    搞崩（页面重载、垫片全丢）。截完就走，崩了也不影响下一张。
//  ★ 表情用 __xyPoke.hold() 静态挂住，不靠动画卡峰值 —— 瞬时动画在无头环境里
//    根本卡不准（CDP 往返就几十毫秒），截出来多半是已经衰减完的一张扑克脸。
//
// 用法：node tools/poke_face_look.mjs [page] [outDir]
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PAGE = process.argv[2] || 'http://127.0.0.1:8090/desktoppmx';
const OUT = process.argv[3] || path.join(process.env.TEMP || '/tmp', 'xy_face_look');
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// 每张只留脸部那一块，画面小、看起来清楚，也省得整窗缩成一条
const STATES = [
  { key: 'none', expr: `__xyPoke.hold(null)` },
  { key: 'surprise', expr: `__xyPoke.hold('surprise')` },
  { key: 'annoyed', expr: `__xyPoke.hold('annoyed')` },
];

async function shot(state, idx) {
  const port = 9350 + idx;
  const prof = path.join(OUT, 'prof_' + state.key);
  const proc = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--mute-audio',
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--window-size=560,940',
    `--remote-debugging-port=${port}`,
    '--user-data-dir=' + prof,
    'about:blank'
  ], { stdio: 'ignore' });

  try {
    let list = null;
    for (let i = 0; i < 60; i++) {
      try {
        list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
        if (list && list.length) break;
      } catch (e) {}
      await sleep(300);
    }
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
      return r.result?.result?.value;
    };

    await send('Runtime.enable');
    await send('Page.enable');
    // 不跟后端切换模型：直接指到佩儿
    await send('Page.navigate', { url: PAGE });
    await sleep(17000);      // 等 PMX 解析 + 首帧

    const ok = await evalJs(`!!window.__xyPoke`);
    if (!ok) { console.log('FAIL  ' + state.key + ' __xyPoke 不存在'); return; }

    // 先播个静止动作把姿势固定住？不 —— 保持页面默认状态更接近真实观感
    const r = await evalJs(state.expr);
    await sleep(900);        // 等两帧渲染

    const st = JSON.parse(String(await evalJs(`JSON.stringify(__xyPoke.state())`)));
    const nm = JSON.parse(String(await evalJs(`JSON.stringify(__xyPoke.names())`)));

    const shotRes = await send('Page.captureScreenshot', { format: 'png' });
    const data = shotRes.result?.data;
    const file = path.join(OUT, `face_${state.key}.png`);
    if (data) {
      fs.writeFileSync(file, Buffer.from(data, 'base64'));
      console.log('OK    ' + state.key + '  ' + file + '  ' + state.expr
        + '  surW=' + st.surW + ' jitW=' + st.jitW);
    } else {
      console.log('FAIL  ' + state.key + ' 截图失败');
    }
    console.log('      组：' + nm.sur + ' | ' + nm.jit);
    ws.close();
  } finally {
    try { proc.kill(); } catch (e) {}
    await sleep(600);
  }
}

for (let i = 0; i < STATES.length; i++) await shot(STATES[i], i);
console.log('\n输出目录：' + OUT);
