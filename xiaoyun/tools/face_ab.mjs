// 情绪表情 A/B 无头验证：8 个标签逐个挂住，量"表情权重 / 姿态 / 画面差异"。
//
//  ★ 为什么不用截图：Page.captureScreenshot 在 SwiftShader 下会把渲染进程搞崩
//    （页面重载、垫片全丢）。这里改成在页内 readPixels —— 先强制渲染一帧再读
//    后台缓冲，不需要 preserveDrawingBuffer，也不会惊动浏览器进程。
//  ★ 为什么用 __xyDbg.faceSet：表情有 0.18s 上升 / 0.55s 回落，等 1.4s 到位再量，
//    不靠动画卡峰值（CDP 往返就几十毫秒，截出来多半是已经衰减完的一张扑克脸）。
//
// 用法：
//   node tools/face_ab.mjs [pageUrl]
// 前置：stub_server.py 或正式后端在跑（本脚本默认 http://127.0.0.1:8765/pages/desktopvrm.html）
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9371;
const PAGE = process.argv[2] || 'http://127.0.0.1:8765/pages/desktopvrm.html';
const LABELS = ['happy', 'soft', 'annoyed', 'jealous', 'shy', 'sad', 'tired', 'curious'];

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=700,1040',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/xy_face_ab',
  'about:blank'
], { stdio: 'ignore' });

function dist(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
  return s / a.length;
}

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) {}
    await sleep(300);
  }
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
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) {
      return { __err: r.result.exceptionDetails.exception?.description || 'eval error' };
    }
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: 700, height: 1040, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: PAGE });

  // 等模型加载：vrm 有 humanoid 且 boneHome 就绪
  let ready = false;
  for (let i = 0; i < 150; i++) {
    const ok = await ev(`!!(window.__xyDbg && window.__xyDbg.ready && __xyDbg.ready())`);
    if (ok === true) { ready = true; break; }
    await sleep(300);
  }
  if (!ready) {
    const diag = await ev(`JSON.stringify({
      face: typeof window.__xyFace, dbg: typeof window.__xyDbg,
      ready: (window.__xyDbg && __xyDbg.ready) ? __xyDbg.ready() : 'n/a',
      canvas: !!document.querySelector('canvas') })`);
    console.log('FAIL 模型未加载完成：' + diag);
    for (const e of errors.slice(0, 5)) console.log('  ' + String(e).split('\n')[0]);
    proc.kill(); process.exit(1);
  }
  await sleep(600);

  // 基线：无情绪
  await ev(`__xyFace.set(null, 0, null, 'base'); __xyFace.holdUntil = -1; true`);
  await sleep(1400);
  const base = await ev(`__xyDbg.grab(128)`);
  const baseRot = await ev(`__xyDbg.headRot()`);

  const rows = [];
  const pix = {};
  for (const label of LABELS) {
    // 先回基线：tau_down=0.55，直接切下一个标签会带 8% 上一个标签的余量
    await ev(`__xyFace.set(null, 0, null, 'clear'); __xyFace.holdUntil = -1; true`);
    await sleep(900);
    await ev(`__xyDbg.faceSet('${label}', 0.8)`);
    await sleep(1400);
    const st = await ev(`__xyDbg.face()`);
    const rot = await ev(`__xyDbg.headRot()`);
    const p = await ev(`__xyDbg.grab(128)`);
    pix[label] = p;
    rows.push({
      label,
      expr: Object.entries(st.cur).filter(([, v]) => v > 0.01)
        .map(([k, v]) => `${k}=${v.toFixed(2)}`).join(' ') || '—',
      headX: rot?.head ? rot.head[0] : null,
      headZ: rot?.head ? rot.head[2] : null,
      eyeY: rot?.eye ? rot.eye[1] : null,
      breath: st.pose?.breath ?? null,
      diff: dist(base, p),
    });
  }

  console.log('\nlabel      expr                              headX    headZ     eyeY   breath   vs基线');
  for (const r of rows) {
    console.log(r.label.padEnd(10) + ' ' + r.expr.padEnd(33) + ' ' +
      String(r.headX).padStart(7) + ' ' + String(r.headZ).padStart(8) + ' ' +
      String(r.eyeY).padStart(8) + ' ' + String(r.breath).padStart(7) + ' ' +
      r.diff.toFixed(2).padStart(7));
  }

  // ---- 断言 ----
  const fails = [];
  for (const r of rows) {
    if (r.expr === '—') fails.push(`${r.label} 没有任何表情权重`);
    if (!(r.diff > 0.15)) fails.push(`${r.label} 画面几乎没变化（diff=${r.diff.toFixed(3)}）`);
  }
  // 三兄弟必须靠姿态分开（表情余弦 >0.85，光权重分不开）
  const trio = ['shy', 'sad', 'tired'].map(k => rows.find(r => r.label === k));
  /* 三兄弟的分离依据有两层：姿态角 + 呼吸幅度。
     sad/tired 的头部角度本来就只差 1°（配方有意如此），它们靠"呼吸"分：
     sad 变浅（0.75）、tired 变重（1.35）。所以两者任一维度分开就算过。 */
  const tdist = (a, b) => Math.abs(a.headX - b.headX) + Math.abs(a.eyeY - b.eyeY);
  for (let i = 0; i < trio.length; i++) {
    for (let j = i + 1; j < trio.length; j++) {
      const d = tdist(trio[i], trio[j]);
      const bd = Math.abs((trio[i].breath ?? 1) - (trio[j].breath ?? 1));
      console.log('姿态差异 %s~%s = %s，呼吸差 = %s',
        trio[i].label, trio[j].label, d.toFixed(3), bd.toFixed(3));
      if (!(d > 0.05 || bd > 0.15)) {
        fails.push(`${trio[i].label}/${trio[j].label} 姿态与呼吸都没分开（${d.toFixed(3)}/${bd.toFixed(3)}）`);
      }
    }
  }
  const sad = rows.find(r => r.label === 'sad'), tired = rows.find(r => r.label === 'tired');
  if (!(sad && tired && sad.breath < 1 && tired.breath > 1)) {
    fails.push('呼吸维度方向不对：sad 应变浅(<1)、tired 应变重(>1)');
  }
  // 画面上也要能分开
  for (let i = 0; i < trio.length; i++) {
    for (let j = i + 1; j < trio.length; j++) {
      const d = dist(pix[trio[i].label], pix[trio[j].label]);
      console.log('画面差异 %s~%s = %s', trio[i].label, trio[j].label, d.toFixed(2));
      if (!(d > 0.05)) fails.push(`画面 shy/sad/tired 区分不足：${trio[i].label}~${trio[j].label}=${d.toFixed(3)}`);
    }
  }

  // ---- 戳一下不再清零情绪（poke 已降级为 overlay）----
  await ev(`__xyDbg.faceSet('jealous', 0.8)`);
  await sleep(1200);
  const before = await ev(`__xyDbg.face()`);
  await ev(`__xyVrmPoke._face(0); true`);        // poke 退出时的那句清零
  await sleep(300);
  const after = await ev(`__xyDbg.face()`);
  console.log('\n戳一下后：label=%s angry=%s → angry=%s（应保持）',
    after.label,
    (before.cur.angry || 0).toFixed(2),
    (after.cur.angry || 0).toFixed(2));
  if (after.label !== 'jealous') fails.push('poke._face(0) 把情绪标签清了');
  if ((after.cur.angry || 0) < 0.15) fails.push('poke._face(0) 把表情权重清零了（overlay 降级失败）');

  if (errors.length) {
    console.log('\n页面异常：');
    for (const e of errors.slice(0, 5)) console.log('  ' + String(e).split('\n')[0]);
  }
  console.log('\n%s', fails.length ? 'FAIL\n  - ' + fails.join('\n  - ') : 'PASS 全部通过');
  proc.kill();
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.log('ERROR', e); proc.kill(); process.exit(1); });
