// 点击反馈（戳一下）无头验证 —— 端到端，不依赖后端进程
//  链路：壳 POST /api/poke -> 页面 150ms 轮询 /api/gaze 读到 poke.seq 变化 -> 播反应
//  这里把 /api/gaze 用 fetch 垫片顶掉，直接操控 poke.seq 模拟壳的点击。
//
//  ★ 两条路径分开验，别混着断言：
//      poll 链路（seq 变化）只能验"触发到了"，因为轮询本身有 0~150ms 抖动，
//      读到的强度总是已衰减一轮的值；
//      真正的峰值表现用 __xyPoke.hit() 直接触发后立刻读。
//  ★ Page.captureScreenshot 在 SwiftShader 下会把渲染进程搞崩（页面重载、垫片全丢），
//    所以全程不截图，纯数值断言。
// 用法：node tools/poke_front_test.mjs [page]
import { spawn } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9338;
const PAGE = process.argv[2] || 'http://127.0.0.1:8090/desktoppmx';
const OUT = process.env.TEMP || '/tmp';

const proc = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--window-size=560,940',
  `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + OUT + '/xy_poke_prof',
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
  await sleep(18000);   // 等 PMX 解析 + 首帧（swiftshader 很慢）

  // ---- 0. 页面健康 ----
  const pageErrs = errors.filter(e => !/WebGL|favicon|net::ERR/i.test(e));
  check('页面无 JS 报错', pageErrs.length === 0, pageErrs.slice(0, 3).join(' | '));
  check('__xyPoke 调试接口存在', (await evalJs(`!!window.__xyPoke`)) === true);

  // ---- 1. 装垫片：/api/gaze 返回受控对象（含 poke）----
  const installShim = async () => evalJs(`(function(){
    window.__xyCursor = { ok:true, x:280, y:470, rect:[0,0,560,940], age:0, mode:'always',
                          poke:{ seq: 0, age: 0 } };
    if (!window.__xyOrigFetch) window.__xyOrigFetch = window.fetch;
    window.fetch = function (u) {
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

  const st = async () => {
    const alive = await evalJs(`!!window.__xyCursor`);
    if (alive !== true) { console.log('WARN 垫片丢失（页面被重载），重建'); await installShim(); }
    const v = await evalJs(`JSON.stringify(window.__xyPoke.state())`);
    try { return JSON.parse(v); } catch (e) { return { __err: v }; }
  };
  // 改 seq 后等一次轮询（150ms）+ 余量
  const bump = async (n = 1, wait = 230) => {
    await evalJs(`window.__xyCursor.poke.seq += ${n}`);
    await sleep(wait);
    return st();
  };

  // ---- 2. morph 索引 ----
  const names = await evalJs(`JSON.stringify(window.__xyPoke.names())`);
  console.log('INFO  morph ' + names);
  const nm = JSON.parse(String(names));
  /* 表情改成组合式（单个 びっくり 位移只有眨眼的 1/8，实测看不出）——
     这里断言组内容和顺序，名字变了就说明组合表没被正确解析。 */
  check('受惊表情组 = 瞳小+ワ+上', nm.sur === '瞳小:1,ワ:0.8,上:0.3', 'group=' + nm.sur);
  check('嫌弃表情组 = じと目+下', nm.jit === 'じと目:1,下:0.45', 'group=' + nm.jit);
  check('两组都非空（否则表情会静默失效）', nm.sur.length > 0 && nm.jit.length > 0,
        'count=' + nm.count);

  // ---- 3. 基线：首帧只记 seq，不播 ----
  await sleep(400);     // 让 poll 至少跑两轮
  const base = await st();
  console.log('INFO  基线 ' + JSON.stringify(base));
  check('初始静止（不会一进页面就抖）', base.active === false, 'active=' + base.active);
  check('seq 已消费（基线 >= 0）', base.seq >= 0, 'seq=' + base.seq);

  // ---- 4. poll 链路：seq 变化 -> 触发 ----
  const s1 = await bump();
  console.log('INFO  受惊 ' + JSON.stringify(s1));
  check('戳一下 -> 反应激活（poll 链路）', s1.active === true, 'active=' + s1.active);
  check('单次戳 = 受惊（不是嫌弃）', s1.annoyed === false);
  check('受惊表情已写入（>0）', s1.surW > 0, 'surW=' + s1.surW);
  check('嫌弃表情未启用', s1.jitW === 0, 'jitW=' + s1.jitW);
  check('躯干竖直压扁（sy < by）', s1.sy < s1.by - 0.0005, `sy=${s1.sy} by=${s1.by}`);
  check('等体积横向膨胀（sx > bx）', s1.sx > s1.bx + 0.0003, `sx=${s1.sx} bx=${s1.bx}`);
  check('无整体位移（posZ 恒 0 = 脚不动）', Math.abs(s1.posZ) < 1e-6, 'posZ=' + s1.posZ);

  // ---- 5. 峰值表现（hit 直接触发，避开轮询抖动）----
  await evalJs(`window.__xyPoke.hit(false, 'body', 'soft')`);
  await sleep(120);      // 过 rise(75ms)，才拿得到峰值而非爬升段
  const p1 = await st();
  console.log('INFO  峰值 ' + JSON.stringify(p1));
  check('受惊峰值表情 > 0.5', p1.surW > 0.5, 'surW=' + p1.surW);
  check('峰值压扁 1.5~2.5%（身体幅度收小后仍可见）',
        !!p1.peak && p1.peak.squash > 0.014 && p1.peak.squash < 0.026,
        'peak=' + (p1.peak && p1.peak.squash));
  check('强度曲线有界（含过冲 <= 1.2）', p1.e > 0 && p1.e <= 1.2, 'e=' + p1.e);
  check('峰值时仍无位移', Math.abs(p1.posZ) < 1e-6, 'posZ=' + p1.posZ);

  // ---- 6. 自动收尾 ----
  await sleep(750);
  const s2 = await st();
  check('反应自动结束（active=false）', s2.active === false, 'active=' + s2.active);
  check('收尾后受惊表情归零', s2.surW === 0, 'surW=' + s2.surW);
  /* 收尾后 rig.scale 立刻被待机呼吸 setScalar 接管，所以不能跟 poke 抓的基准比。
     真正的判据是"不再处于压扁状态"：sy 不该低于基准的 99.5%。 */
  check('收尾后不再压扁', s2.sy >= s2.by * 0.995, `sy=${s2.sy} by=${s2.by}`);
  check('收尾后仍无位移', Math.abs(s2.posZ) < 1e-6, 'posZ=' + s2.posZ);

  // ---- 7. 连戳 -> 嫌弃（poll 链路）----
  const c1 = await bump();
  await sleep(150);
  const c2 = await bump();
  console.log('INFO  连戳 ' + JSON.stringify(c2));
  check('连戳第二下 -> 嫌弃（poll 链路）', c2.annoyed === true, 'annoyed=' + c2.annoyed);
  check('嫌弃时受惊表情归零', c2.surW === 0, 'surW=' + c2.surW);

  // 嫌弃的峰值表现
  await evalJs(`window.__xyPoke.hit(true)`);
  await sleep(60);
  const p2 = await st();
  check('嫌弃峰值表情 > 0.5', p2.jitW > 0.5, 'jitW=' + p2.jitW);
  check('嫌弃时受惊表情保持归零', p2.surW === 0, 'surW=' + p2.surW);

  // ---- 7.5 三级延迟：头比躯干晚起 80ms（"不僵硬"的核心）----
  await sleep(900);
  await evalJs(`window.__xyPoke.hit(false, 'body', 'soft')`);
  await sleep(25);
  const d1 = await st();
  check('触发瞬间：躯干已起（e > 0.1）', d1.e > 0.1, 'e=' + d1.e);
  check('触发瞬间：头明显落后（eHead < e×0.25）', d1.eHead < d1.e * 0.25,
        `e=${d1.e} eHead=${d1.eHead}`);
  await sleep(130);
  const d2 = await st();
  check('触发 ~160ms：头已跟上（eHead > 0.1）', d2.eHead > 0.1, 'eHead=' + d2.eHead);
  /* ★ 头不是"始终落后"：延迟 80ms 之后它衰减更慢，会反超躯干 —— 这正是
     follow-through 想要的效果（躯干收住了，头还在动）。所以判据是"后段仍高于躯干"。 */
  await sleep(100);
  const d3 = await st();
  check('头收得比躯干慢（后段 eHead 明显高于 e）', d3.eHead > d3.e * 1.6,
        `e=${d3.e} eHead=${d3.eHead}`);

  // ---- 7.6 部位区分：点脸 = 躲（头为主），点身体 = 缩（躯干为主）----
  await sleep(900);
  await evalJs(`window.__xyPoke.hit(false, 'body', 'soft')`);
  await sleep(260);      // 260ms = 过峰（头部峰值在 155ms 左右），peak 已记满
  const pb = await st();
  await sleep(700);
  await evalJs(`window.__xyPoke.hit(false, 'head', 'soft')`);
  await sleep(260);
  const ph = await st();
  check('点身体 -> part=body', pb.part === 'body', 'part=' + pb.part);
  check('点脸 -> part=head', ph.part === 'head', 'part=' + ph.part);
  /* 头后仰量受"采样落在曲线哪一段"影响太大（SwiftShader 下 CDP 往返有几十毫秒抖动），
     所以部位差异只看两个稳定的量：脊柱（点身体才有）与歪头（点脸才有）。
     头后仰的绝对倍率在代码里是常量，不必再测一遍。 */
  check('点身体时脊柱收缩（点脸不做）', pb.peak.spineX > 0.005 && ph.peak.spineX === 0,
        `body=${pb.peak.spineX} head=${ph.peak.spineX}`);
  check('点脸时歪头躲闪更明显', ph.peak.headZ > pb.peak.headZ * 2,
        `head=${ph.peak.headZ} body=${pb.peak.headZ}`);
  /* 用户要求"点头部时幅度再大一点"：看本次反应的峰值注入（peak），
     不受采样时刻抖动影响。8° ≈ 0.14 rad，身体侧只给 0.35 倍。 */
  check('点头部时峰值后仰 >= 8°', ph.peak.headX >= 0.14, 'peak=' + ph.peak.headX);
  check('点头部时峰值歪头 >= 3°', ph.peak.headZ >= 0.052, 'peak=' + ph.peak.headZ);
  check('点头部时身体几乎不动（压扁 < 0.8%）', ph.peak.squash < 0.008,
        'squash=' + ph.peak.squash);
  check('点身体时身体压扁仍可见（>= 1.4%）', pb.peak.squash >= 0.014,
        'squash=' + pb.peak.squash);

  // ---- 7.7 手感两档：vivid 压扁明显比 soft 深 ----
  await sleep(900);
  await evalJs(`window.__xyPoke.hit(false, 'body', 'soft')`);
  await sleep(80);
  const hs = await st();
  await sleep(900);
  await evalJs(`window.__xyPoke.hit(false, 'body', 'vivid')`);
  await sleep(80);
  const hv = await st();
  check('vivid 档生效', hv.style === 'vivid', 'style=' + hv.style);
  check('vivid 压扁比 soft 深 1.5 倍以上',
        hv.inj.squash > hs.inj.squash * 1.5, `soft=${hs.inj.squash} vivid=${hv.inj.squash}`);

  // ---- 7.8 坐标透传：点窗口上/下 -> 部位判定 ----
  await sleep(900);
  await evalJs(`window.__xyPoke.hit(false, 'body', 'soft')`);   // 顺手把 style 归位
  await sleep(700);
  await evalJs(`window.__xyCursor.poke = { seq: window.__xyCursor.poke.seq + 1, age: 0, x: 0.5, y: 0.06 }`);
  await sleep(250);
  const up = await st();
  check('点窗口顶部 -> 判为 head', up.part === 'head', 'part=' + up.part + ' y=0.06');
  await sleep(700);
  await evalJs(`window.__xyCursor.poke = { seq: window.__xyCursor.poke.seq + 1, age: 0, x: 0.5, y: 0.86 }`);
  await sleep(250);
  const dn = await st();
  check('点窗口下部 -> 判为 body', dn.part === 'body', 'part=' + dn.part + ' y=0.86');

  // ---- 8. seq 一次跳多格：只播一次，不叠加 ----
  //  （真实场景：壳在两次轮询之间连发了几次 poke，页面只该看到"被戳了"这件事）
  await sleep(700);
  await evalJs(`window.__xyCursor.poke.seq += 5`);
  await sleep(230);
  const j1 = await st();
  check('seq 跳 5 只播一次反应（dt 未被叠加）', j1.active === true && j1.dt < 0.35,
        'dt=' + j1.dt + ' seq=' + j1.seq);

  // ---- 9. 眼神 off 时点击反馈照旧 ----
  await sleep(700);
  await evalJs(`window.__xyCursor.mode='off'`);
  await sleep(400);
  await evalJs(`window.__xyCursor.poke.seq += 1`);
  await sleep(230);
  const offMode = await st();
  check('gaze=off 时戳一下仍然有反应', offMode.active === true, 'active=' + offMode.active);
  await evalJs(`window.__xyCursor.mode='always'`);

  // ---- 10. 异常抗性：后端 500 不该炸页面 ----
  await evalJs(`window.fetch = function(u){
    var url=(typeof u==='string')?u:'';
    if (url.indexOf('/api/gaze')===0) return Promise.resolve(new Response('boom',{status:500}));
    return window.__xyOrigFetch.apply(window, arguments);
  }`);
  await sleep(600);
  check('后端 500 时页面存活', (await evalJs(`!!window.__xyPoke`)) === true);
  const errs2 = errors.filter(e => !/WebGL|favicon|net::ERR/i.test(e));
  check('500 不产生未捕获异常', errs2.length === 0, errs2.slice(0, 2).join(' | '));

  console.log(`\n${pass} passed, ${fail} failed`);
  try { proc.kill(); } catch (e) {}
  process.exit(fail ? 1 : 0);
}

main().catch(e => { console.error('FATAL ' + e); try { proc.kill(); } catch (_) {} process.exit(2); });
