// 眼神诊断：manual 后读眼骨实际旋转 + 像素差分析
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9338;
const PAGE = 'http://127.0.0.1:8090/desktoppmx';
const OUT = process.env.TEMP || '/tmp';

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=560,940', `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + OUT + '/xy_gaze_diag', 'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (list?.length) break; } catch (e) {}
    await sleep(300);
  }
  const ws = new WebSocket(list.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const errors = []; const stackLog = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
    if (m.method === 'Runtime.consoleAPICalled') {
      const a = (m.params.args || []).map(x => x.value ?? x.description ?? '').join(' ');
      if (m.params.type === 'error') errors.push('console.error: ' + a);
      if (a.includes('__STACK__')) stackLog.push(a);
    }
  };
  const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result?.result?.value;
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: PAGE });
  await sleep(18000);

  // 0. 内部引用对比：bones.neck 与 __xyBones['首'] 是否同一对象
  const ident = await evalJs(`(function(){
    var d = window.__xyDbg;
    if (!d) return 'no __xyDbg（页面是旧版？）';
    var bmN = d.bm['首'], bn = d.bones.neck;
    var bmE = d.bm['左目'], be = d.bones.eyeL;
    return JSON.stringify({
      neckSame: (bmN && bn) ? (bmN === bn) : (bmN ? 'bones.neck缺失' : 'bm无首'),
      eyeLSame: (bmE && be) ? (bmE === be) : (bmE ? 'bones.eyeL缺失' : 'bm无左目'),
      bonesKeys: Object.keys(d.bones).join(','),
      hasBones: d.gaze.hasBones,
      gazeEnabled: d.gaze.enabled,
      manual: d.gaze.manual ? JSON.stringify(d.gaze.manual) : 'null'
    });
  })()`);
  console.log('IDENT ' + ident);

  // 0b. 受控单帧：__xyPose.shot(t) 用指定 t 驱动 animateBones 一帧，立即读写入值
  const shot5 = await evalJs(`(function(){
    window.__xyPose.shot(5.0);
    var d = window.__xyDbg;
    return JSON.stringify({
      neckY: +d.bones.neck.rotation.y.toFixed(4),
      headX: +d.bones.head.rotation.x.toFixed(4),
      eyeLY: +d.bones.eyeL.rotation.y.toFixed(4),
      eyeLX: +d.bones.eyeL.rotation.x.toFixed(4),
      homeNeck: JSON.stringify(d.boneHome.neck)
    });
  })()`);
  console.log('SHOT(t=5) ' + shot5);
  await sleep(300);
  const shot5b = await evalJs(`(function(){
    var d = window.__xyDbg;
    return JSON.stringify({ neckY: +d.bones.neck.rotation.y.toFixed(4), eyeLY: +d.bones.eyeL.rotation.y.toFixed(4) });
  })()`);
  console.log('SHOT(t=5) 300ms后(loop接管) ' + shot5b);

  // 0c. 一锤定音：哨兵值 + 直接调 animateBones(5)
  const sentinel = await evalJs(`(function(){
    var d = window.__xyDbg;
    d.bones.neck.rotation.y = -9;
    d.bones.eyeL.rotation.set(-8, -7, -6);
    d.animateBones(5.0);
    return JSON.stringify({
      neckAfter: +d.bones.neck.rotation.y.toFixed(4),
      eyeLAfter: JSON.stringify([+d.bones.eyeL.rotation.x.toFixed(4), +d.bones.eyeL.rotation.y.toFixed(4)]),
      headAfter: +d.bones.head.rotation.x.toFixed(4)
    });
  })()`);
  console.log('SENTINEL+animateBones(5) ' + sentinel);

  // 0e. 猴补丁：抓 rotation.set 的调用栈（只抓 neck，限 3 次）
  await evalJs(`(function(){
    var n = window.__xyDbg.bones.neck.rotation;
    var cnt = 0;
    var orig = n.set;
    n.set = function(x, y, z) {
      if (cnt < 3 && (x === 0 && z === 0)) {
        cnt++;
        console.log('__STACK__ y=' + y + ' cnt=' + cnt + '\\n' + new Error().stack);
      }
      return orig.apply(this, arguments);
    };
    window.__xyDbg.bones.neck.rotation.y = -9;
  })()`);
  await sleep(700);
  console.log('STACKS(' + stackLog.length + '):');
  stackLog.forEach(s => console.log(s.split('\\n').slice(0, 5).join('\\n')));

  // 0d. 读 loop 的时钟与动画状态
  for (let i = 0; i < 3; i++) {
    const clk = await evalJs(`(function(){
      var c = window.__xyPose.clock();
      var s = window.__xyPose.state();
      return JSON.stringify({ clock: +c.toFixed(3), look: +Math.sin(c*0.35).toFixed(4),
                              lookX011: +(Math.sin(c*0.35)*0.11).toFixed(4),
                              neckY: +window.__xyDbg.bones.neck.rotation.y.toFixed(4),
                              gesture: s.gesture ? s.gesture.type : null,
                              nextAt: +s.nextGestureAt.toFixed(1) });
    })()`);
    console.log('CLOCK ' + clk);
    await sleep(400);
  }

  // 1. manual set 后连续读 3 次旋转，看 animateBones 是否在驱动眼骨
  await evalJs(`window.__xyGaze.clear()`);
  await sleep(400);
  // 1a. 二分测试：手动直接写 rotation，看是否被覆盖
  const direct = await evalJs(`(function(){
    var b = window.__xyBones;
    b['左目'].rotation.set(0.3, 0.4, 0.5);
    b['首'].rotation.y = 0.6;
    return 'written';
  })()`);
  await sleep(250);
  const after = await evalJs(`(function(){
    var b = window.__xyBones;
    return JSON.stringify({
      eyeL: [+b['左目'].rotation.x.toFixed(3), +b['左目'].rotation.y.toFixed(3), +b['左目'].rotation.z.toFixed(3)],
      neckY: +b['首'].rotation.y.toFixed(3),
      headX: +b['頭'].rotation.x.toFixed(3)
    });
  })()`);
  console.log('DIRECT-WRITE 250ms后: ' + after);
  for (let i = 0; i < 3; i++) {
    await sleep(400);
    const rot = await evalJs(`(function(){
      var b = window.__xyBones;
      var L = b && b['左目'], R = b && b['右目'], N = b && b['首'];
      return JSON.stringify({
        eyeL: L ? [+L.rotation.x.toFixed(3), +L.rotation.y.toFixed(3), +L.rotation.z.toFixed(3)] : null,
        eyeR: R ? [+R.rotation.x.toFixed(3), +R.rotation.y.toFixed(3), +R.rotation.z.toFixed(3)] : null,
        neck: N ? +N.rotation.y.toFixed(3) : null,
        headX: (function(){var h=b&&b['頭'];return h?+h.rotation.x.toFixed(3):null})(),
        t: +((performance.now()/1000).toFixed(1))
      });
    })()`);
    console.log('T+' + (i * 0.4 + 0.4).toFixed(1) + 's  ' + rot);
  }
  if (errors.length) { console.log('--- JS ERRORS ---'); errors.slice(0, 8).forEach(e => console.log('  ' + String(e).slice(0, 300))); }
  else { console.log('--- no JS errors ---'); }

  // 2. 眼骨影响多少顶点？读 PMX 的 skinning（通过 three 的 skinIndex 属性）
  const winfo = await evalJs(`(function(){
    try {
      var mesh = window.__xyMesh || null;
      return 'no-handle';
    } catch(e) { return 'err'; }
  })()`);

  // 3. 直接改骨骼 rotation 到极限，看眼睛区域像素变化
  const shot = async (tag) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(`${OUT}/diag_${tag}.png`, Buffer.from(r.result.data, 'base64'));
  };
  await evalJs(`window.__xyGaze.clear()`);
  await sleep(600);
  await shot('a_clear');
  await evalJs(`window.__xyGaze.set(0.6, 0)`);
  await sleep(800);
  await shot('b_yaw06');
  await evalJs(`window.__xyGaze.set(-0.6, 0)`);
  await sleep(800);
  await shot('c_yawn06');
  await evalJs(`window.__xyGaze.set(0, 0.5)`);
  await sleep(800);
  await shot('d_pitch05');
  await evalJs(`window.__xyGaze.clear()`);
  console.log('SHOTS diag_a_clear / diag_b_yaw06 / diag_c_yawn06 / diag_d_pitch05');
  proc.kill();
  process.exit(0);
}
main().catch(e => { console.error('FATAL ' + e); proc.kill(); process.exit(2); });
