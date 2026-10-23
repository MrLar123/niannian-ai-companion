// 眼神功能无头验证（端到端，不依赖后端进程：把 /api/gaze 用 fetch 垫片顶掉）
//  验证链：假光标 -> gaze.poll() 算目标 -> 指数平滑 -> applyGazeEyes/applyGazeHead 写骨骼
//  重点覆盖"动作模式"（页面默认就在播 VMD，animateBones 根本不跑 —— 之前的真 bug）
//
//  ★ 不在测量过程中截图：无头 SwiftShader 下 Page.captureScreenshot 会把渲染进程搞崩，
//    页面随即重载（fetch 垫片全丢、轮询读到真实后端 age=-1 → 目标恒 0），
//    后续断言全错但看不出原因。截图统一放到全部量化断言之后。
// 用法：node tools/gaze_front_test.mjs [page]
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9337;
const PAGE = process.argv[2] || 'http://127.0.0.1:8090/desktoppmx';
const OUT = process.env.TEMP || '/tmp';

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=560,940',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + OUT + '/xy_gaze_prof',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('PASS  ' + name + ' ' + extra); }
  else { fail++; console.log('FAIL  ' + name + ' ' + extra); }
};

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
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description };
    return r.result?.result?.value;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: PAGE });
  await sleep(18000);   // 等 PMX 解析 + 首帧渲染（swiftshader 慢）

  // ---- 1. 页面健康 ----
  const gazeErrs = errors.filter(e => !/WebGL|favicon|net::ERR/i.test(e));
  check('页面无 JS 报错', gazeErrs.length === 0, gazeErrs.slice(0, 3).join(' | '));

  const hasBones = await evalJs(`window.__xyDbg && window.__xyDbg.gaze.hasBones === true`);
  check('gaze.hasBones = true（左右目均匹配）', hasBones === true);

  const state = await evalJs(`JSON.stringify(window.__xyPose.state())`);
  console.log('INFO  姿势状态 ' + state);
  const inDance = /"playing":true/.test(String(state));
  console.log('INFO  当前分支 = ' + (inDance ? 'dance(VMD 接管) —— 正是之前失效的场景' : 'idle(animateBones)'));

  // ---- 2. 装 fetch 垫片：把 /api/gaze 换成可控假光标 ----
  const installShim = () => evalJs(`(function(){
    window.__xyCursor = { ok: true, x: 0, y: 0, rect: [0, 0, 560, 940], age: 0, mode: 'always' };
    if (!window.__xyOrigFetch) window.__xyOrigFetch = window.fetch;
    window.fetch = function (u, o) {
      var url = (typeof u === 'string') ? u : (u && u.url) || '';
      if (url.indexOf('/api/gaze') === 0) {
        return Promise.resolve(new Response(JSON.stringify(window.__xyCursor),
          { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return window.__xyOrigFetch.apply(window, arguments);
    };
    return 'ok';
  })()`);
  check('fetch 垫片安装', (await installShim()) === 'ok');

  // 读眼骨/头骨旋转 + 平滑后的目标角
  const readPose = async () => {
    const v = await evalJs(`(function(){
      var d = window.__xyDbg, L = d.bones.eyeL, R = d.bones.eyeR;
      if (!L || !R) return JSON.stringify({ __err: 'no eye bone' });
      return JSON.stringify({
        ey: [+L.rotation.y.toFixed(4), +R.rotation.y.toFixed(4)],
        ex: [+L.rotation.x.toFixed(4), +R.rotation.x.toFixed(4)],
        tYaw: +d.gaze.tYaw.toFixed(4), tPitch: +d.gaze.tPitch.toFixed(4),
        yaw: +d.gaze.yaw.toFixed(4), pitch: +d.gaze.pitch.toFixed(4),
        neckY: +(d.bones.neck ? d.bones.neck.rotation.y : 0).toFixed(4),
        neckX: +(d.bones.neck ? d.bones.neck.rotation.x : 0).toFixed(4),
        headY: +(d.bones.head ? d.bones.head.rotation.y : 0).toFixed(4),
        headX: +(d.bones.head ? d.bones.head.rotation.x : 0).toFixed(4)
      });
    })()`);
    try { return JSON.parse(v); } catch (e) { return { __err: v }; }
  };

  // aim 只改光标 + 等收敛，不截图（见文件头注释）
  const aim = async (x, y, tag, wait = 1100) => {
    const alive = await evalJs(`!!window.__xyCursor`);
    if (alive !== true) { console.log('WARN 垫片丢失（页面被重载），重建'); await installShim(); }
    await evalJs(`(function(){var c=window.__xyCursor;c.x=${x};c.y=${y};c.age=0;return 1})()`);
    await sleep(wait);            // tau=0.1s，1.1s ≈ 11 tau，已收敛
    const r = await readPose();
    console.log(`AIM   ${tag} cursor=(${x},${y}) -> ${JSON.stringify(r)}`);
    return r;
  };

  // 窗口 560x940，中心 (280,470)
  const center = await aim(280, 470, 'center');
  const right  = await aim(1100, 470, 'right');   // 光标在右（窗外）
  const left   = await aim(-500, 470, 'left');    // 光标在左
  const down   = await aim(280, 1500, 'down');    // 光标在下
  const up     = await aim(280, -400, 'up');      // 光标在上
  const nearR  = await aim(350, 470, 'nearright');// 刚出死区 24px：验证曲线连续、不跳变
  const farR   = await aim(4000, 470, 'farright');// 极远：验证幅度上限

  // ---- 3. 方向断言（rotation.y+ = 看画面右、rotation.x+ = 看画面下）----
  const ok = (v) => v && !v.__err;
  check('center 目标≈0（死区内不追）', ok(center) && Math.abs(center.yaw) < 0.02 && Math.abs(center.pitch) < 0.02,
        ok(center) ? `yaw=${center.yaw} pitch=${center.pitch}` : String(center));
  check('光标在右 → 双眼 y 同正（一起看右）', ok(right) && right.ey[0] > 0.10 && right.ey[1] > 0.10,
        ok(right) ? `ey=${JSON.stringify(right.ey)}` : String(right));
  check('光标在左 → 双眼 y 同负（一起看左）', ok(left) && left.ey[0] < -0.10 && left.ey[1] < -0.10,
        ok(left) ? `ey=${JSON.stringify(left.ey)}` : String(left));
  check('左右目同号 = 不斗鸡眼', ok(right) && Math.sign(right.ey[0]) === Math.sign(right.ey[1]),
        ok(right) ? `ey=${JSON.stringify(right.ey)}` : '');
  check('光标在下 → 双眼 x 为正（看下）', ok(down) && down.ex[0] > 0.08 && down.ex[1] > 0.08,
        ok(down) ? `ex=${JSON.stringify(down.ex)}` : String(down));
  check('光标在上 → 双眼 x 为负（看上）', ok(up) && up.ex[0] < -0.08 && up.ex[1] < -0.08,
        ok(up) ? `ex=${JSON.stringify(up.ex)}` : String(up));
  check('眼骨由 poll 链路驱动（非人工 set）', ok(right) && right.tYaw > 0.15 && right.yaw > 0.15,
        ok(right) ? `tYaw=${right.tYaw} yaw=${right.yaw}` : '');
  // 幅度护栏：瞳孔飞出眼眶的根因是「越过死区直接跳 11 度」+ 满幅 35 度
  check('刚出死区 -> 幅度极小（曲线连续不跳变）',
        ok(nearR) && Math.abs(nearR.yaw) > 0.0005 && Math.abs(nearR.yaw) < 0.05,
        ok(nearR) ? `yaw=${nearR.yaw} tYaw=${nearR.tYaw}` : String(nearR));
  check('极远光标 -> 不超过水平极限 0.26',
        ok(farR) && Math.abs(farR.yaw) <= 0.2601 && Math.abs(farR.ey[0]) <= 0.2601,
        ok(farR) ? `yaw=${farR.yaw} ey=${JSON.stringify(farR.ey)}` : String(farR));

  // ---- 3b. 头部跟随幅度（用户反馈：左右还要更大、上下基本没动）----
  // 分量算式：yaw 0.25×1.6（首45/頭55）、pitch 0.18×2.0（首25/頭75）
  const partsRaw = await evalJs(`(function(){window.__xyGaze.set(0.25,0.18);var p=window.__xyGaze.parts();return JSON.stringify(p)})()`);
  let parts = null; try { parts = JSON.parse(partsRaw); } catch (e) {}
  check('头部左右分量够大（首>0.15 頭>0.18 rad）', parts && parts.neckY > 0.15 && parts.headY > 0.18,
        parts ? `neckY=${parts.neckY.toFixed(3)} headY=${parts.headY.toFixed(3)}` : String(partsRaw));
  check('头部上下分量够大（頭>0.20 首>0.05 rad）', parts && parts.headX > 0.20 && parts.neckX > 0.05,
        parts ? `headX=${parts.headX.toFixed(3)} neckX=${parts.neckX.toFixed(3)}` : String(partsRaw));

  // 实时路径：dance 分支是在 VMD 轨道上做 +=，单帧读数会被 VMD 干扰，
  // 所以取 1.2s 均值再比 —— 差值必须接近头部上下分量（≈2×headX），证明真的叠加了
  const sampleHeadX = async () => {
    const arr = [];
    for (let i = 0; i < 30; i++) {
      const v = await evalJs(`(function(){var d=window.__xyDbg;return d.bones.head?+d.bones.head.rotation.x.toFixed(4):0})()`);
      if (typeof v === 'number') arr.push(v);
      await sleep(40);
    }
    return arr.reduce((a, b) => a + b, 0) / (arr.length || 1);
  };
  await evalJs(`window.__xyGaze.set(0, 0.18)`); const meanDown = await sampleHeadX();
  await evalJs(`window.__xyGaze.set(0, -0.18)`); const meanUp = await sampleHeadX();
  await evalJs(`window.__xyGaze.clear()`);
  check('实时循环里头部上下真的在动（均值差 > 0.25rad）',
        (meanDown - meanUp) > 0.25,
        `meanX(下)=${meanDown.toFixed(3)} meanX(上)=${meanUp.toFixed(3)} diff=${(meanDown - meanUp).toFixed(3)}`);

  // ---- 4. 数据过期 -> 回中位 ----
  await evalJs(`(function(){var c=window.__xyCursor;c.x=1100;c.y=470;c.age=5;return 1})()`);
  await sleep(900);
  const stale = await readPose();
  check('数据过期 age>1s → 目标回中位', ok(stale) && Math.abs(stale.yaw) < 0.03 && Math.abs(stale.pitch) < 0.03,
        ok(stale) ? `yaw=${stale.yaw} pitch=${stale.pitch}` : String(stale));

  // ---- 5. 关开关 -> 完全不动 ----
  await evalJs(`window.__xyGaze.on(false)`);
  await evalJs(`(function(){var c=window.__xyCursor;c.age=0;c.x=1100;c.y=470;return 1})()`);
  await sleep(900);
  /* 判"停止驱动"只能看 offsets()（驱动决策唯一入口），不能看眼骨值：
     页面若在播 VMD，动作自带的眼骨轨道每帧都在写骨骼 —— 这时眼骨值
     本来就非 0 且持续变化（2026-10-19 实测：ey 在两次运行间 -0.056/-0.080 波动）。
     旧断言"眼骨归位 <0.01"只在待机（无 VMD）时成立，是环境敏感的假判据。 */
  const offDriving = await evalJs(`String(window.__xyDbg.gaze.offsets() === null)`);
  check('__xyGaze.on(false) 后停止驱动（offsets=null）', offDriving === 'true',
        'offsets=' + (await evalJs(`String(window.__xyDbg.gaze.offsets())`)));
  await evalJs(`window.__xyGaze.on(true)`);

  // ---- 6. 三档模式（右键菜单的三种选择）----
  // 判据走 gaze.offsets()：它是驱动决策的唯一入口，返回 null = 本帧不碰眼骨。
  // 不比较眼骨数值 —— 动作在播，VMD 自己也在写眼骨，两次采样必然不同，比了等于白比。
  const setMode = async (m) => { await evalJs(`window.__xyCursor.mode='${m}'`); await sleep(450); };
  const modeNow = async () => evalJs(`window.__xyDbg.gaze.mode`);
  const offs = async () => evalJs(`(function(){var g=window.__xyDbg.gaze.offsets();return g?('yaw='+g.yaw.toFixed(3)):'null'})()`);

  await setMode('idle');
  check('mode 从 /api/gaze 同步到页面', (await modeNow()) === 'idle', 'mode=' + (await modeNow()));
  const danceNow = await evalJs(`(function(){var s=window.__xyPose&&window.__xyPose.state?window.__xyPose.state():null;return s?!!s.playing:false})()`);
  check('idle + 动作播放中 → 让位给 VMD（offsets=null）',
        danceNow ? ((await offs()) === 'null') : true,
        danceNow ? '正在播 VMD（预期）' : '本环境未播 VMD，跳过');

  await setMode('off');
  check('mode 同步 off', (await modeNow()) === 'off', 'mode=' + (await modeNow()));
  check('off → offsets=null', (await offs()) === 'null');
  const offT = await evalJs(`(function(){var g=window.__xyDbg.gaze;return JSON.stringify([g.tYaw,g.tPitch])})()`);
  check('off 时目标角清零（切回不会跳到旧角度）', offT === '[0,0]', 'target=' + offT);

  await setMode('always');
  check('切回 always → 恢复驱动', (await offs()) !== 'null', 'offsets=' + (await offs()));

  // ---- 7. 抗异常：后端 500 不该炸页面 ----
  await evalJs(`window.fetch = function(u){ var url=(typeof u==='string')?u:''; if(url.indexOf('/api/gaze')===0){ return Promise.reject(new Error('boom')); } return (window.__xyOrigFetch||window.fetch).apply(window, arguments); }`);
  await sleep(500);
  check('fetch 失败静默不抛错', errors.length === 0, errors.slice(0, 2).join(' | '));

  // ---- 8. 最后才截图（截图会让无头渲染进程重载，必须放在所有测量之后）----
  try {
    await evalJs(`window.__xyCursor.age=0`);
    const aims = [['center', 280, 470], ['right', 1100, 470], ['down', 280, 1500]];
    for (const [tag, x, y] of aims) {
      const alive = await evalJs(`!!window.__xyCursor`);
      if (alive !== true) await installShim();
      await evalJs(`(function(){var c=window.__xyCursor;c.x=${x};c.y=${y};c.age=0;c.mode='always';return 1})()`);
      await sleep(1000);
      const sh = await send('Page.captureScreenshot', { format: 'png' });
      if (sh.result?.data) fs.writeFileSync(`${OUT}/gaze_${tag}.png`, Buffer.from(sh.result.data, 'base64'));
    }
    console.log('SHOT  ' + aims.map(a => `${OUT}/gaze_${a[0]}.png`).join('  '));
  } catch (e) { console.log('WARN 截图失败（不影响量化断言）: ' + e.message); }

  console.log('\n===== 前端眼神: ' + pass + ' passed, ' + fail + ' failed =====');
  if (errors.length) { console.log('--- console 错误 ---'); errors.slice(0, 8).forEach(e => console.log('  ' + e)); }
  proc.kill();
  process.exit(fail ? 1 : 0);
}
main().catch(e => { console.error('FATAL ' + e); proc.kill(); process.exit(2); });
