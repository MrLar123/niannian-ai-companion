#!/usr/bin/env node
/* PMX 表情实验室：体检（audit）+ 实测标定（calib）
 *
 * 为什么需要它：MMD 模型的表情名是日文的、各模型还都不一样，同一个名字在不同模型上
 * 的**实际形态和幅度**完全不同（「笑い」在爱莉希雅上是闭眼笑、在别的模型上是睁眼笑；
 * kafka 的「にこり」幅度小到画面上几乎为零）。光按关键词猜必然出现"情绪算了但脸不动"。
 * 这里直接量：把某个表情单独摆上去，比画面和中性脸的差。
 *
 * 口径（重要，别换）：
 *   vis = 改动像素占比(%)  +  峰值 16×16 分块平均差 × 0.04  +  平均差 × 1.0
 *   三项各管一类变化：面积管大面积强变化（闭眼笑/睁大眼）；峰值管细小但局部很强
 *   （眼睛一眯、眉毛一压）；平均差管整片均匀的低幅度变化（脸红染色，面积可能是 0）。
 *   参考：全闭眼 ≈ 1.5~2；≥0.5 正常可见；0.2~0.5 轻微；<0.2 基本看不见。
 *
 * 两条铁律（踩过坑）：
 *   1) 「清零→摆表情→render→readPixels→算差」必须压进**同一个同步任务**（measureDiff），
 *      跨异步任务采样会让读数整体漂移、同一表情两次差一个数量级。
 *   2) 探测权重必须等于**真机会写进脸部的权重**，选完还要按该权重复测一遍，
 *      否则会出现"报表好看、真机不动"的假通过。
 *
 * 用法：
 *   node tools/pmx_emo_lab.mjs audit                 # 31 个模型 × 8 标签全量体检
 *   node tools/pmx_emo_lab.mjs audit all --shots     # 全量体检 + 每模型 9 张截图
 *   node tools/pmx_emo_lab.mjs calib                 # 全量标定，写 assets/emo_slots.json
 *   node tools/pmx_emo_lab.mjs calib kafka,elysia    # 只标定指定模型（合并进已有配置）
 *   node tools/pmx_emo_lab.mjs dump <model>          # 列出该模型全部 morph 名
 *   node tools/pmx_emo_lab.mjs eval <model> "<js>"   # 在页面里跑一段 JS（调试用）
 *   node tools/pmx_emo_lab.mjs shot <model> <png> "表情=权重,..."
 * 环境：
 *   BASE=http://127.0.0.1:8765   （stub_server.py）
 *   NOCONFIG=1                   标定时忽略已有配置，纯按关键词重测
 *   VERBOSE=1                    打出每个槽位的候选实测分
 */
import { spawn, execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = Number(process.env.PORT || 9378);
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const CFG_OUT = process.env.CFG_OUT || 'assets/emo_slots.json';
const N = 96;                    // 采样网格（RGB，只截脸周围一小块）
const W = 620, H = 940;          // 与真机窗口同量级，取景公式一致
const FREEZE_T = 12;             // 冻结动画时钟（呼吸/扫视/眨眼都停在同相位）
const W_PROBE = 1.0;             // 单表情探针权重
/* 口径：score = 头部小块里"像素被改动超过 10 级"的占比（%）。
   为什么不用平均差：平均差会被面积稀释 —— 眼睛闭上这种剧烈变化只占 8% 面积，
   平均下来比"整幅画面漂移 2 级"还小，排序会全错。占比则直接对应"看得出多少"。 */
/* 实测标尺（96×96 采样、脸框 0.22 画布宽）：
   全闭眼（まばたき=1.0）≈ 1.5%（这是"极其明显"的上限参考）
   明显表情 0.5~1.5% / 轻微但可见 0.2~0.5% / 看不见 <0.15%
   —— 阈值不能拍脑袋，必须按这个标尺定，否则会把"看得见"判成"看不见"。 */
const TH_VISIBLE = 0.5;          // 体检：>=0.5% 面积 = 正常可见
const TH_PICK = 0.15;            // 标定：低于它的候选不参与挑选（等于没效果）
const AMP_GATE = 1.0;            // 候选最高分低于它才做"放大权重二轮探测"（0.5 只是勉强可见）

const LABELS = ['happy', 'soft', 'annoyed', 'jealous', 'shy', 'sad', 'tired', 'curious'];
const CN = { happy: '开心', soft: '温柔', annoyed: '不耐烦', jealous: '吃醋',
             shy: '害羞', sad: '难过', tired: '疲惫', curious: '好奇' };

/* 候选词库：顺序 = 语义贴合度优先。一个槽位可以有多个候选，谁在画面上看得见就选谁。
   ★ 词表要覆盖各模型的命名习惯，实测踩过的坑：
     · キリッ 有两种宽度（全角 キリッ / 半角 ｷﾘｯ），漏一个就等于整个模型没有"凌厉"表情；
     · 有的模型叫「怒った」而不是「怒り」，1 字词「怒」只做精确匹配，抓不到它；
     · 中文命名模型用「脸红 / 悲しい」而不是「照れ / 困る」。 */
const CAND = {
  smile: ['にこり', 'にっこり', '微笑', '微笑み', '笑顔', '笑い目', 'にやり２', 'にやり', '喜び', '笑い'],
  angry: ['怒り', '怒り２', '怒り目', '怒った', '真面目', 'キリッ', 'ｷﾘｯ', '不機嫌', '怒'],
  sad:   ['困る', '悲し', '悲しい', '悲しい目', '泣', '涙', 'たれ目', '眼角下'],
  calm:  ['なごみ', '慈愛', 'にっこり', '照れ', 'にやり'],
  shock: ['びっくり', '驚き', '驚かす', '驚', '目開き', 'はちゅ目', 'はちゅ', 'はぅ', '星目'],
  half:  ['じと目', 'ジト目', '半目', '瞳小', '瞳縦潰れ', '无神', '無神'],
  blush: ['照れ', '赤面', '脸红', '恥ずかしい', '頬', 'FaceRed'],
};
/* 组合兜底：单表情太弱时，和这些"局部表情"叠加往往才看得见（口角下撇 + 压眉）。
   也当"白名单全灭"时的最后手段：有的模型「怒り」是全权重都测不出的死 morph
   （实测 peier 的怒り在 0.44~1.0 权重下都只有 0.08~0.11），但同一个模型上
   「恐ろしい子！」（凶脸）居然有 1.16 —— 有得选总比"生气时面无表情"强。 */
const AUX = {
  angry: ['口角下げ', '下', '恐ろしい子'],
  sad:   ['口角下げ', '眼角下'],
  half:  ['瞳小', '口角下げ'],
};
const BLINK_WORDS = ['まばたき', 'blink', 'Blink', '瞬き', '眨眼'];
const BAN = ['消', '解除', 'off', 'Off'];
/* 各槽位在配方里的典型权重（算放大系数用） */
const TYPICAL = { smile: 0.85, angry: 0.55, sad: 0.65, calm: 0.65, shock: 0.42, half: 0.55, blush: 0.60 };
/* 伪表情：页面自己实现的"脸材质染色"兜底脸红（原生脸红贴图缺失时用）。
   它不来自模型，所以不能靠关键词匹配拿到，只能显式喂给标定。 */
const TINT_NAME = '__tint';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
/* 占比口径：每 3 个通道算一个像素，RGB 平均差 > 10 记作"这一像素被改动了" */
function dist(a, b, thr = 10) {
  if (!a || !b) return 0;
  let hit = 0;
  const n = a.length / 3;
  for (let i = 0; i < n; i++) {
    const d = (Math.abs(a[i * 3] - b[i * 3]) + Math.abs(a[i * 3 + 1] - b[i * 3 + 1])
             + Math.abs(a[i * 3 + 2] - b[i * 3 + 2])) / 3;
    if (d > thr) hit++;
  }
  return +(100 * hit / n).toFixed(2);
}
/* 平均差（只用于自噪声判定，越小越稳定） */
function adiff(a, b) {
  if (!a || !b) return 0;
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
  return s / a.length;
}
function cos(a, b) {
  let d = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return (na && nb) ? d / Math.sqrt(na * nb) : 0;
}
const sub = (a, b) => a.map((v, i) => v - b[i]);

/* ---------------- CDP ---------------- */
function killPort(port) {
  /* 上一轮被工具超时杀掉时 Chrome 会变成孤儿进程占着调试端口，新实例连不上自己的端口，
     表现是「卡住几分钟没有任何输出」。启动前先把这个端口的监听者清掉。
     这里只用 netstat + taskkill，不拼任何带转义的字符串（跨 shell 传参会被吃掉）。 */
  try {
    const out = execSync(`netstat -ano | findstr :${port}`, { encoding: "utf8", windowsHide: true });
    const LF = String.fromCharCode(10);
    const pids = [];
    for (const l of out.split(LF)) {
      if (l.indexOf("LISTENING") < 0) continue;
      const parts = l.trim().split(/ +/);
      const pid = parts[parts.length - 1];
      if (pid && pid !== "0" && pids.indexOf(pid) < 0) pids.push(pid);
    }
    for (const pid of pids) {
      try { execSync("taskkill /F /PID " + pid, { windowsHide: true, stdio: "ignore" }); } catch (e) { /* 已退出 */ }
    }
    if (pids.length) console.log(`（清理占用 ${port} 的旧进程 ${pids.join(",")}）`);
  } catch (e) { /* 端口本来就空闲 */ }
}

async function launch() {
  killPort(PORT);
  const profile = path.join(process.env.TEMP || '/tmp', 'xy_emo_lab_prof');
  const proc = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--mute-audio',
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    `--window-size=${W},${H}`, `--remote-debugging-port=${PORT}`,
    '--user-data-dir=' + profile, 'about:blank',
  ], { stdio: 'ignore' });

  let list = null;
  for (let i = 0; i < 60; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list && list.length) break;
    } catch (e) { /* 还没起来 */ }
    await sleep(300);
  }
  if (!list) throw new Error('Chrome 调试端口没起来');
  const page = list.find((t) => t.type === 'page') || list[0];
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (ev2) => {
    const m = JSON.parse(ev2.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const ev2 = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) {
      return { __err: r.result.exceptionDetails.exception?.description || 'eval error' };
    }
    return r.result?.result?.value;
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  return { proc, send, ev: ev2, errors, close: () => { try { ws.close(); } catch (e) {} proc.kill(); } };
}

/* ---------------- 采样 ---------------- */
async function snap(cdp) {
  const r = await cdp.ev(`__xyDbg.grabHead(${N}, ${FREEZE_T})`);
  return r && r.px ? r.px : null;
}

/* 等画面稳定：模型加载后取景/视线还要收敛一阵，直接采基线会把"还在动"当成表情信号。 */
async function stableBase(cdp, tries = 10) {
  let a = await snap(cdp), noise = 9e9;
  for (let i = 0; i < tries; i++) {
    await sleep(240);
    const b = await snap(cdp);
    if (!a || !b) break;
    noise = adiff(a, b); a = b;
    if (noise < 0.05) break;
  }
  return { base: a, noise };
}

/* 可见度 = 改动面积% + 峰值分块差×0.04 + 平均差×1.0。三项各管一种情况，缺一不可：
   - 面积：大面积强变化（闭眼笑、睁大眼）；
   - 峰值：细小但局部很强（眼睛一眯、眉毛一压，面积上不去但肉眼看得见）；
   - 平均差：**整片均匀的低幅度变化** —— 典型是脸红染色。实测 peier 的染色
     面积 0%、峰值 9.04、但平均差 1.33（整张脸每个像素都轻微偏红），
     只看面积会判成"没效果"，而截图里明明是一张红扑扑的脸。
     反过来真无效的表情平均差只有 0.02（peier 的 怒り/困る），不会被误判。
   噪声方面：readPixels 在同一同步任务内是确定性的，空 vs 空实测恒为 0，三项都不会凭空涨。 */
const PEAK_W = 0.04;
const MAG_W = 1.0;
function visOf(r) {
  if (!r) return 0;
  const peak = (r.vec && r.vec.length) ? Math.max.apply(null, r.vec) : 0;
  return +(((r.score) || 0) + peak * PEAK_W + (r.mag || 0) * MAG_W).toFixed(3);
}

async function setOverride(cdp, ov) {
  await cdp.ev(`window.__xyMorphOverride = ${ov ? JSON.stringify(ov) : 'null'}; true`);
}
/* 一次 CDP 往返完成「清空所有表情 → 摆待测表情 → render → 读像素 → 算差」。
   页面内是同步执行的，不依赖渲染循环、也不需要等任何一帧 —— 之前所有
   "读数全都一样 / 两次跑差一个数量级"都是采样跨了异步任务导致的。 */
async function probe(cdp, ov) {
  const r = await cdp.ev(`__xyDbg.measureDiff(null, ${JSON.stringify(ov || {})}, 96, ${process.env.FRAC || '0.22'})`);
  if (!r || r.__err || r.score === undefined) return { score: 0, area: 0, peak: 0, vec: null, mag: 0 };
  const peak = (r.vec && r.vec.length) ? Math.max.apply(null, r.vec) : 0;
  /* score = 可见度（决策用），area = 改动面积（报表用），两者口径见 visOf */
  return { score: visOf(r), area: r.score, peak: +peak.toFixed(2), vec: r.vec, mag: r.mag };
}

async function openModel(cdp, key, needBase = true) {
  /* _t 时间戳是**必须**的：页面是内联 module 的单文件 HTML，同一个模型的 URL 不带
     唯一参数时浏览器会直接用缓存的旧页面 —— 改了页面代码却测出旧结果，排查过两次。 */
  await cdp.send('Page.navigate', { url: `${BASE}/desktoppmx?model=${encodeURIComponent(key)}&_t=${Date.now()}` });
  let ok = false;
  for (let i = 0; i < 160; i++) {
    const r = await cdp.ev(`!!(window.__xyDbg && __xyDbg.headScreen && __xyDbg.headScreen() && __xyDbg.emo)`);
    if (r === true) { ok = true; break; }
    await sleep(300);
  }
  if (!ok) return null;
  /* ★ 必须等过 autofit 的两个定时器（loadModel 里 800ms / 2200ms）。
     它们会挪相机，在那之后取基线、之前取探针，整幅画面会平移 —— 实测每个表情
     都被算成"约 7% 面积变化"，候选之间完全分不开。这是标定不可靠的真正原因。 */
  await sleep(2600);
  await cdp.ev(`__xyDbg.emoClear(); __xyFreezeT=${FREEZE_T}; window.__xyMorphOverride=null;`
             + ` window.__xyCalibNoIdle=true; true`);
  const info = {
    meshes: await cdp.ev(`__xyDbg.meshes ? __xyDbg.meshes() : null`),
    names: await cdp.ev(`__xyDbg.morphs()`),
  };
  if (needBase) {
    const st = await stableBase(cdp);
    info.base = st.base; info.noise = st.noise;
  } else {
    info.base = null; info.noise = 0;
  }
  return info;
}

/* 该模型上包含关键词的表情名（排除反向表情与单边版） */
function stem(k) { return k.replace(/[\d.．。_\-ー\s]+$/g, ''); }   // 困る1/困る2 → 困る
function findCands(names, words, cap = 6) {
  const out = [];
  const seen = [];
  const push = (k) => {
    if (out.indexOf(k) >= 0) return;
    const st = stem(k);
    if (seen.indexOf(st) >= 0) return;          // 同一词干的变体只留第一个
    seen.push(st); out.push(k);
  };
  /* 先精确名（语义最贴），再包含匹配。★ 单字词只认精确匹配：
     AUX 里的「下」如果按包含匹配，会命中「眼角下」「舌下」「口角下げ」，
     实测让 elysia 的 angry 组合变成「怒り + 眼角下」（眼角下垂是难过，不是生气）。 */
  for (const w of words) {
    if (names.indexOf(w) >= 0 && !BAN.some((b) => w.indexOf(b) >= 0)) push(w);
  }
  for (const w of words) {
    if (w.length < 2) continue;
    for (const k of names) {
      if (k.indexOf(w) < 0) continue;
      if (BAN.some((b) => k.indexOf(b) >= 0)) continue;
      if (/_L|_R|左|右/.test(k) && k.length > w.length) continue;   // 单边版先不选
      push(k);
      if (out.length >= cap) break;
    }
    if (out.length >= cap) break;
  }
  return out;
}

/* ---------------- 标定 ---------------- */
async function calibModel(cdp, key, prevCfg) {
  const info = await openModel(cdp, key, false);
  if (!info) return { key, fail: '加载失败' };
  const names = info.names || [];

  /* 眨眼模板：眼睛全闭的画面差，用来判断某个"笑"是不是闭眼笑 */
  const blinkName = (BLINK_WORDS.map((w) => names.find((k) => k.indexOf(w) >= 0)).find(Boolean)) || null;
  let blinkVec = null;
  if (blinkName) {
    const r = await probe(cdp, { [blinkName]: 1.0 });
    blinkVec = r.vec;
  }

  const res = { slots: {}, scoreMap: {}, idle: null, meshes: (info.meshes || []).length,
                morphs: names.length, noise: +info.noise.toFixed(3) };
  /* 配方要从页面取（不在这边写第二份）—— 染色强度要按配方给 blush 的权重换算 */
  const recipe = await cdp.ev(`__xyDbg.recipe()`);
  const scores = {};   // 供报告
  let smileBestPick = null;   // smile 槽位里画面最亮的那个（calm 兜底要用它，不一定等于选中项）
  for (const slot of Object.keys(CAND)) {
    const cands = findCands(names, CAND[slot]);
    const aux = findCands(names, AUX[slot] || [], 3).filter((n) => cands.indexOf(n) < 0);

    const measured = [];
    /* 兜底脸红：不论模型有没有原生脸红，都量一次"脸材质染色"。
       很多模型的「照れ」是一块独立贴片，而作者打包时漏了那张贴图
       （实测 wuluo 的表情.png 根本不存在）→ 空贴图 + MMD 自定义混合 = 什么都不画，
       再怎么调权重都不会红。这时只能用页面自己染脸。

       ★ 强度必须**按模型**搜：脸材质底色/覆盖面积差异极大，同一个权重在
         wuluo 上是"刚好一层红"，在 nier42 上却是 22.7%（整张脸红透）。
         方法：以外观分 2.0 为目标，从强到弱探几个权重，取第一个落在
         [0.8, 3.0] 区间的；都不在区间就取最接近目标的（太低则视为不可用）。 */
    let tintScore = 0, tintW = 1;
    if (slot === 'blush') {
      const effBase = (recipe[slot] || recipe.shy || { expr: {} }).expr.blush * 0.8 || 0.48;
      const TARGET = 2.0;
      let best = null;
      for (const pw of [1, 0.85, 0.7, 0.6, 0.5, 0.4, 0.3, 0.22]) {
        const eff = effBase * pw;
        const rr = await probe(cdp, { [TINT_NAME]: eff });
        const cand = { w: pw, vis: rr.score, eff: +eff.toFixed(3) };
        if (process.env.VERBOSE) console.log(`      [tint] w=${pw} eff=${cand.eff} vis=${cand.vis}`);
        if (!best || Math.abs(cand.vis - TARGET) < Math.abs(best.vis - TARGET)) best = cand;
        if (cand.vis <= 3.0 && cand.vis >= 0.8) { best = cand; break; }   // 命中区间即停
      }
      if (best && best.vis >= 0.45) {
        tintScore = best.vis;
        tintW = best.w;
        cands.push(TINT_NAME);
        measured.push({ name: TINT_NAME, score: best.vis, vec: null, note: 'w=' + best.w + ' eff=' + best.eff });
      }
    }
    if (!cands.length && !aux.length) { res.slots[slot] = []; scores[slot] = []; continue; }

    /* ★ 候选必须按**实际会用的权重**量，不能按 1.0 满权重：
       配方给每个槽位的权重只有 0.3~0.85，再乘说话让位 0.8。满权重量出来的
       "看得见"，到实际权重上可能是 0 —— feixue 的「照れ」满权重 2.04、实际 0.48 下为 0，
       报表写着害羞有效果，真机根本没红。 */
    const probeW = Math.min(1, (TYPICAL[slot] || 0.6) * 0.8);
    for (const nm of cands.concat(aux)) {
      if (nm === TINT_NAME) continue;          // 上面已经量过
      const r = await probe(cdp, { [nm]: probeW });
      measured.push({ name: nm, score: r.score, vec: r.vec });
    }
    scores[slot] = measured.map((m) => `${m.name}=${m.score}`);

    /* ★ 二轮：在**放大后**的权重上再量一次（2026-11-05）。
       顶点位移要大到跨过栅格化阈值才会出现在画面上，所以 morph → 像素 不是线性的：
       feixue 的「はちゅ」在 0.336 权重下改动面积 0、在 0.672 下却有 1.72。
       而这个槽位配方只给 0.42 × 0.8 = 0.336 —— 单选一轮必然挑到"放大也救不回来"的那个
       （实际挑成了 びっくり 0.36，把本来能到 1.72 的 はちゅ 漏掉）。
       所以：一轮最高分连 TH_VISIBLE 都不到时，说明后面一定会被放大，
       那就按放大后的权重重测，用"真机上会看到的强度"来选。
       谁被选中，谁就带上 amp = 放大系数，后面不再按最终分重复放大。 */
    const baseCandMax = Math.max(0, ...measured.filter((m) => cands.indexOf(m.name) >= 0)
                                                   .map((m) => m.score || 0));
    const ampW = Math.min(2.0, Math.max(1, Math.round((0.9 / (TYPICAL[slot] || 0.6)) * 10) / 10));
    /* 门槛用 1.0 而不是 TH_VISIBLE：0.5 只代表"刚好看得见"，卡在这里会漏掉真正的强表情。
       feixue 的「びっくり」在本权重下是 0.505（过线，于是不再二轮），而同一模型上
       「はちゅ」放大后是 1.70 —— 只差 0.005 就把"好奇"从 2.17 打回 0.38。 */
    if (baseCandMax < AMP_GATE && ampW > 1.05) {
      const pw2 = Math.min(1, probeW * ampW);
      for (const m of measured) {
        if (m.name === TINT_NAME) continue;
        const r2 = await probe(cdp, { [m.name]: pw2 });
        /* 只有"放大确实更好"才记下系数：morph → 像素不是单调的（位移跨过栅格化阈值后会
           重新落回边界），放大反而变淡的情况实测存在，不能一律乘上去。 */
        if (r2.score > m.score) { m.score = r2.score; m.vec = r2.vec; m.amp = ampW; }
      }
      scores[slot] = measured.map((m) => `${m.name}=${m.score}${m.amp ? '@x' + m.amp : ''}`);
    }
    if (process.env.VERBOSE) console.log(`    [${slot}] ${scores[slot].join(' ')}`);

    /* 选谁：在**该槽位的语义白名单**里取画面最明显的那个。
       白名单本身已经做过语义筛选（「眼角下」只出现在 sad，「口角下げ」只在兜底），
       所以"最明显"不会跑偏；只有白名单全军覆没时才动 AUX 组合。
       反例（第一版）：按缩写语义顺序取第一个"够亮"的，结果 elysia 的 happy 用了
       「にこり」（实测只改动 0.49% 面积，肉眼几乎为零），而明显得多的「笑い」被跳过。 */
    const eyeClosed = (m) => (blinkVec && m.vec && m.vec.length)
      ? Math.abs(cos(m.vec, blinkVec)) : 0;
    const table = measured.filter((m) => cands.indexOf(m.name) >= 0)
                          .sort((a, b) => b.score - a.score);
    const auxM = measured.filter((m) => aux.indexOf(m.name) >= 0)
                         .sort((a, b) => b.score - a.score);
    if (slot === 'smile') smileBestPick = table[0] || null;

    /* ★ 睁眼优先（硬约束，2026-11-05）：MMD 的「笑い」在多数模型上是**闭眼笑**（^ ^），
       画面改动面积最大，纯按"最明显"选必然选到它 —— 结果 happy 时角色闭着眼，像在睡觉。
       所以 smile 槽位先按"与眨眼模板的相关度"把闭眼笑剔出去。
       ★ 但"睁眼"不值得用"看不见"去换：feixue 姐妹的睁眼笑只有 0.26~0.29（几乎为零），
         闭眼笑却有 1.8。这种情况退回闭眼笑、把权重压到 0.7（眼睛半闭，读作开心眯眼），
         比"睁着眼但毫无表情"正确得多。门槛用 TH_VISIBLE 而不是 TH_PICK。 */
    let closedEyePick = false;
    let pool = table;
    if (slot === 'smile' && blinkVec) {
      /* 界线 0.6 而不是 0.5：实测真闭眼笑的相关度都在 0.76 以上，睁眼笑在 0.43 以下，
         中间是空的。peier 的「喜び」正好算出 0.50 —— 卡在 0.5 会两次运行给出相反结论
         （一次判闭眼笑、一次判睁眼笑），把界线放进空档里才有确定性。 */
      const openAll = table.filter((m) => eyeClosed(m) < 0.6);
      const closedAll = table.filter((m) => eyeClosed(m) >= 0.6);
      const bestOpen = openAll[0], bestClosed = closedAll[0];
      if (bestOpen && bestOpen.score >= TH_VISIBLE) {
        pool = openAll;
      } else if (bestClosed && bestClosed.score >= TH_PICK) {
        pool = closedAll; closedEyePick = true;
      } else {
        pool = openAll.length ? openAll : table;
      }
    }

    let pick = pool[0] && pool[0].score >= TH_PICK ? pool[0] : null;
    let partner = null;
    let finalScore = pick ? pick.score : 0;

    /* 单表情不够亮（甚至够不到门槛）→ 和语义相容的局部表情叠加。
       有些模型「怒り」「困る」单独几乎看不出（feixue2 的怒り只有 0.1%），
       叠上「口角下げ / 下（压眉）」才成立。 */
    const basePick = pick || (pool[0] && pool[0].score > 0.04 ? pool[0] : null);
    if (basePick && finalScore < TH_VISIBLE * 1.5) {
      const a2 = auxM.filter((m) => m.name !== basePick.name && m.score > 0.04)[0];
      if (a2) {
        const r = await probe(cdp, { [basePick.name]: probeW, [a2.name]: probeW });
        if (r.score > Math.max(basePick.score * 1.15, TH_PICK)) {
          pick = basePick; partner = a2; finalScore = r.score;
          scores[slot].push(`组合(${basePick.name}+${a2.name})=${r.score}`);
        }
      }
    }
    /* 组合也不成立时，退到"局部表情单点"（口角下撇总比没有强） */
    if (!pick) pick = auxM[0] && auxM[0].score >= TH_PICK ? auxM[0] : null;
    /* ★ 脸红兜底：原生脸红看不见（贴图缺失 / 位移太小）甚至压根没有这个表情时，
       改用页面自己实现的「脸材质染色」。前提是它测得出来（tintScore 达标），
       否则宁可不选 —— 选一个看不见的表情只会污染体检数字。 */
    if (slot === 'blush' && (!pick || pick.score < TH_VISIBLE) && tintScore >= TH_VISIBLE) {
      pick = { name: TINT_NAME, score: tintScore };
      partner = null; finalScore = tintScore;
      scores[slot].push(`兜底染色=${tintScore}`);
    }
    if (!pick) { res.slots[slot] = []; continue; }
    if (!finalScore) finalScore = pick.score;

    const parts = [{ name: pick.name, w: 1 }];
    if (partner) parts.push({ name: partner.name, w: 1 });
    /* 染色用的是上面按模型搜出来的权重，不能被放大系数覆盖 */
    if (pick.name === TINT_NAME) parts[0].w = tintW;

    /* 还是弱 → 用放大系数把配方权重顶上去（配方里 shock 只给 0.42，不放大就永远轻）。
       ★ 已经做过二轮的话，分数本身就是放大权重下的读数，必须把同一个系数写进 parts，
         否则会"按放大权重量到 1.72、按 1.0 权重写进脸"，分数对、真机还是 0。 */
    let w = pick.amp || 1;
    if (!pick.amp && finalScore < TH_VISIBLE && pick.name !== TINT_NAME) {
      w = Math.min(2.0, Math.max(1, Math.round((0.9 / (TYPICAL[slot] || 0.6)) * 10) / 10));
      if (finalScore < 0.25) w = Math.max(w, 1.6);
    }
    if (pick.name !== TINT_NAME) {
      parts[0].w = w;
      if (partner) parts[1].w = partner.amp || 1;
    }

    /* ★ 闭眼笑只给七成，而且必须在下面"最终复测"之前压 —— 原来是复测之后才压，
       报表量到的是 1.0 权重下的分数、真机写进去的是 0.7，又踩了一次"分数好看真机不对"。 */
    if (closedEyePick && parts[0].w > 0.7) parts[0].w = 0.7;

    /* ★ 闭眼笑配嘴角上扬：MMD 的「笑い」是整张脸的表情，但不少模型上它只动眼睛 ——
       实测 yoohee 的「笑い」在真机权重下眼睛照旧睁着、嘴角一动不动，读起来是"面无表情"。
       配上「口角上げ」才是"在笑"。这里只在闭眼笑时补，且只在嘴角确实能动时补，
       不改变"眼睛用哪个 morph"的决定。 */
    if (slot === 'smile' && closedEyePick && !partner) {
      const mouthCands = findCands(names, ['口角上げ', '口角上げ2'], 2)
        .filter((n) => n !== pick.name && !parts.some((p) => p.name === n));
      const eyeEff = Math.min(1, (TYPICAL[slot] || 0.6) * 0.8 * parts[0].w);
      for (const mn of mouthCands) {
        const rM = await probe(cdp, { [mn]: 0.8 * 0.8 });
        if ((rM.score || 0) < 0.08) continue;      // 嘴角压根不动就别硬塞
        parts.push({ name: mn, w: 1 });
        scores[slot].push('嘴角+' + mn + '=' + rM.score);
        break;
      }
    }

    res.slots[slot] = parts;
    /* 最终复测：按"实际会写进脸部的权重"（配方 × 0.8 × 放大系数）再量一次。
       报表里的数字必须是这一发，否则会出现"分数很好看、真机没反应"的假通过。 */
    const effMap = {};
    for (const p of parts) {
      const e2 = Math.min(1, (TYPICAL[slot] || 0.6) * 0.8 * (p.w === undefined ? 1 : p.w));
      if (e2 > (effMap[p.name] || 0)) effMap[p.name] = e2;
    }
    if (pick.name !== TINT_NAME) {
      const rFin = await probe(cdp, effMap);
      finalScore = rFin.score;
    }
    res.scoreMap[slot] = +finalScore;
    if (slot === 'smile') {
      res.scoreMap.smileBlinkCos = +eyeClosed(pick).toFixed(2);
      if (closedEyePick) res.scoreMap.smileClosedEye = 1;
    }
  }

  /* calm 兜底：部分模型没有「なごみ」这类"平和"表情（31 个里有 4 个缺）。
     温柔对桌宠来说就是"浅笑"，用 smile 的表情压到 0.6 顶上去，比完全不动作强。 */
  /* ★ 不止"缺"要兜底：有的模型只给了「にやり」（咧嘴笑）这类语义不贴、幅度还只有 0.16 的，
     拿它当"温柔"不如把微笑压淡 —— 所以"分数连可见阈值都不到"也走同一条兜底。
     只在微笑确实更亮时才换，绝不把本来清楚的换没了。 */
  const calmPrev = res.scoreMap.calm || 0;
  if (!(res.slots.calm || []).length || calmPrev < TH_VISIBLE) {
    /* 用「画面上最亮的那个笑」，不用 smile 槽位选中的那个：smile 槽位为了避免闭眼笑，
       可能挑了一个很淡的睁眼笑（peier 的「喜び」0.73），而同一模型上「笑い」有 8.86。
       温柔只要"浅笑"，眼睛半闭反而更贴，所以这里优先看得见。 */
    const sp = smileBestPick || (res.slots.smile && res.slots.smile[0]);
    if (sp) {
      const r = await probe(cdp, { [sp.name]: Math.min(1, 0.8 * 0.6 * (sp.w || 1)) });
      if (!(res.slots.calm || []).length || r.score > calmPrev) {
        res.slots.calm = [{ name: sp.name, w: 0.6 }];
        res.scoreMap.calm = r.score;
        res.scoreMap.calmFrom = 'smile';
      }
    }
  }

  /* 常驻微笑：要"睁着眼"的笑 —— 按语义顺序取第一个"不算闭眼"（与眨眼模板相关 < 0.5）
     且至少能看出来的。全都闭眼就取闭眼程度最低的，并把权重压到 0.22（避免一直闭眼）。 */
  const smileCands = findCands(names, CAND.smile);
  const idleRank = [];
  for (const nm of smileCands) {
    const r = await probe(cdp, { [nm]: 0.55 });
    const ce = (blinkVec && r.vec) ? Math.abs(cos(r.vec, blinkVec)) : 0;
    idleRank.push({ name: nm, score: r.score, ce });
  }
  const okIdle = idleRank.filter((x) => x.score > 0.15 && x.ce < 0.6);
  const pickIdle = okIdle[0]
    || idleRank.filter((x) => x.score > 0.15).sort((a, b) => a.ce - b.ce)[0]
    || null;
  if (pickIdle) {
    res.idle = { name: pickIdle.name, w: pickIdle.ce >= 0.6 ? 0.22 : 0.55, ce: +pickIdle.ce.toFixed(2) };
  }
  res.idleRank = idleRank.map((x) => `${x.name}=${x.score},闭眼=${x.ce.toFixed(2)}`);
  if (process.env.VERBOSE) console.log('    [idle] ' + res.idleRank.join(' '));

  /* 把配置推到页面，做"看得见吗"的实测验收（这才是用户真正看到的） */
  await cdp.ev(`window.__xyEmoSlotsCfg = window.__xyEmoSlotsCfg || {};`
    + ` window.__xyEmoSlotsCfg[${JSON.stringify(key)}] = ${JSON.stringify({ slots: res.slots, idle: res.idle })};`
    + ` if (window.__xyRediscoverSlots) window.__xyRediscoverSlots(); true`);
  await setOverride(cdp, null);
  /* 验收用"表情改动面积"（同步比对），不用带姿态的真实渲染：
     歪头/低头在任何模型上都很明显，会把"表情几乎没动"完全掩盖掉。 */
  const slotsNow = await cdp.ev(`__xyDbg.emo().slots`);
  res.labels = {};
  res.labelsArea = {};
  res.labelsPeak = {};
  for (const lb of LABELS) {
    const rec = recipe[lb] || { expr: {} };
    const morphs = {};
    for (const s2 in rec.expr) {
      for (const part of (slotsNow[s2] || [])) {
        const w = Math.min(1, rec.expr[s2] * 0.8 * (part.w === undefined ? 1 : part.w));
        if (w > (morphs[part.name] || 0)) morphs[part.name] = w;
      }
    }
    const r = await cdp.ev(`__xyDbg.measureDiff(null, ${JSON.stringify(morphs)})`);
    res.labels[lb] = r ? +visOf(r).toFixed(2) : 0;
    res.labelsArea[lb] = r ? r.score : 0;
    res.labelsPeak[lb] = r ? +Math.max.apply(null, r.vec || [0]).toFixed(1) : 0;
  }
  return res;
}

/* ---------------- 体检（只量不修） ----------------
   口径与标定一致：把该标签的配方表情按当前槽位摆上去，量脸部改动的像素占比。
   不掺姿态 —— 姿态在任何模型上都明显，会把"表情没动"掩盖成"有效果"。 */
async function auditModel(cdp, key, shots) {
  const info = await openModel(cdp, key, false);
  if (!info || !(info.names || []).length) return { key, fail: '加载失败/无表情' };
  const slots = await cdp.ev(`__xyDbg.emo().slots`);
  const recipe = await cdp.ev(`__xyDbg.recipe()`);
  const hs = await cdp.ev(`__xyDbg.headScreen()`);
  const out = { key, slots: slots, labels: {}, pose: {},
                canvas: [hs.w, hs.h], head: [hs.x, hs.y] };
  const rows = [];
  for (const lb of LABELS) {
    const rec = recipe[lb] || { expr: {}, pose: {} };
    const morphs = {};
    for (const s2 in rec.expr) {
      for (const part of (slots[s2] || [])) {
        const w = Math.min(1, rec.expr[s2] * 0.8 * (part.w === undefined ? 1 : part.w));
        if (w > (morphs[part.name] || 0)) morphs[part.name] = w;
      }
    }
    const r = await cdp.ev(`__xyDbg.measureDiff(null, ${JSON.stringify(morphs)})`);
    out.labels[lb] = r ? +visOf(r).toFixed(2) : 0;
    out.pose[lb] = rec.pose || {};
    if (process.env.VERBOSE) console.log(`    ${lb}: ${JSON.stringify(morphs)} -> ${out.labels[lb]}`);
    if (shots) {
      await cdp.ev(`__xyDbg.emoClear(); __xyCalibNoIdle=false; true`);
      await sleep(300);
      await cdp.ev(`__xyDbg.emoSet(${JSON.stringify(lb)}, 0.8); true`);
      await sleep(1000);
      const url = await cdp.ev(`__xyDbg.shot()`);
      if (url && url.indexOf('data:image/png;base64,') === 0) {
        fs.mkdirSync(shots, { recursive: true });
        fs.writeFileSync(path.join(shots, String(rows.length + 1).padStart(2, '0') + '_' + lb + '.png'),
                         Buffer.from(url.split(',')[1], 'base64'));
      }
      const st = await cdp.ev(`__xyDbg.emo()`);
      rows.push({ label: lb, expr: Object.entries(st.cur || {}).filter((e) => e[1] > 0.01)
        .map((e) => e[0] + '=' + e[1].toFixed(2)).join(' ') || '—',
        head: [(st.pose || {}).headX || 0, 0, (st.pose || {}).headZ || 0],
        breath: (st.pose || {}).breath || 1 });
    }
  }
  if (shots) {
    await cdp.ev(`__xyDbg.emoClear(); true`);
    await sleep(1200);
    const u0 = await cdp.ev(`__xyDbg.shot()`);
    if (u0 && u0.indexOf('data:') === 0) {
      fs.writeFileSync(path.join(shots, '00_base.png'), Buffer.from(u0.split(',')[1], 'base64'));
    }
    fs.writeFileSync(path.join(shots, 'meta.json'), JSON.stringify(
      { canvas: out.canvas, head: out.head, labels: rows }, null, 2));
    fs.writeFileSync(path.join(shots, 'labels.json'), JSON.stringify(out, null, 2));
  }
  return out;
}

/* ---------------- 主流程 ---------------- */
async function main() {
  const mode = process.argv[2] || 'audit';
  const arg = (process.argv[3] || 'all').split(',').filter(Boolean);
  const shots = process.argv.includes('--shots');
  if (!fs.existsSync(CHROME)) { console.log('找不到 Chrome：' + CHROME); process.exit(1); }

  const list = await (await fetch(`${BASE}/api/models`)).json();
  let keys = (list.models || []).filter((m) => m.kind === 'pmx' || (!m.kind && m.pmx)).map((m) => m.key);
  if (arg.length && arg[0] !== 'all') keys = keys.filter((k) => arg.indexOf(k) >= 0);
  if (!keys.length) { console.log('没有匹配的模型'); process.exit(1); }

  let cfg = {};
  if (fs.existsSync(CFG_OUT)) {
    try { cfg = JSON.parse(fs.readFileSync(CFG_OUT, 'utf8')); } catch (e) { cfg = {}; }
  }
  if (process.env.NOCONFIG) cfg = {};

  const cdp = await launch();
  console.log(`模式=${mode} 模型数=${keys.length} 采样=${N}x${N}(RGB) 可见阈值=${TH_VISIBLE}%面积`);

  /* shot：出一张"只摆某个表情"的截图（排障用，肉眼确认到底看不看得见）
     用法: shot <模型> <输出png> [表情名=权重,...] */
  if (mode === 'shot') {
    try {
      const info = await openModel(cdp, keys[0], false);
      if (!info) { console.log('加载失败'); return; }
      const outPng = process.argv[4];
      const ov = {};
      for (const kv of (process.argv[5] || '').split(',').filter(Boolean)) {
        const p2 = kv.split('=');
        ov[p2[0]] = p2.length > 1 ? +p2[1] : 1;
      }
      const js = `(function(){ window.__xyCalibNoIdle = true; __xyDbg.emoClear();` +
        Object.keys(ov).map((k) => ` __xyDbg.morphSet(${JSON.stringify(k)}, ${ov[k]});`).join('') +
        (ov[TINT_NAME] !== undefined ? ` __xyDbg.applyWeights(${JSON.stringify(ov)});` : '') +
        ` return __xyDbg.shot(); })()`;
      const u = await cdp.ev(js);
      if (u && String(u).indexOf('data:') === 0) {
        fs.writeFileSync(outPng, Buffer.from(String(u).split(',')[1], 'base64'));
        console.log('已写入 ' + outPng + '  表情=' + JSON.stringify(ov));
      } else { console.log('截图失败：' + String(u).slice(0, 120)); }
    } finally { cdp.close(); }
    return;
  }

  /* eval：打开模型后跑一段页面内 JS 并打印结果（排障通用口，
     比如 `__xyDbg.morphInfo('照れ')` 看顶点增量到底有没有内容） */
  if (mode === 'eval') {
    try {
      const info = await openModel(cdp, keys[0], false);
      if (!info) { console.log('加载失败'); return; }
      const js = process.argv.slice(4).join(',');
      if (!js) { console.log('用法: eval <模型> <js>'); return; }
      const r = await cdp.ev(js);
      console.log(JSON.stringify(r, null, 2));
    } finally { cdp.close(); }
    return;
  }


  /* dump：把每个模型的 morph 名字全打出来 —— 查"某个槽位为什么缺"用，
     只能靠眼睛看它到底有没有语义等价的表情（名字各家都不一样）。 */
  if (mode === 'dump') {
    try {
      for (const key of keys) {
        const info = await openModel(cdp, key, false);
        if (!info) { console.log('### ' + key + ' 加载失败'); continue; }
        const nm = info.names || [];
        console.log('### ' + key + '  (' + nm.length + ')');
        console.log(nm.join(' | '));
      }
    } finally { cdp.close(); }
    return;
  }

  if (mode === 'probe') {
    const cdp = await launch();
    try {
      const key = keys[0];
      const info = await openModel(cdp, key, false);
      if (!info) { console.log('加载失败'); process.exit(1); }
      const a = await cdp.ev(`__xyDbg.measureDiff(null, {})`);
      const b = await cdp.ev(`__xyDbg.measureDiff(null, {})`);
      console.log(`噪声（空 vs 空，两次）：${a.score}% / ${b.score}%  盒=${JSON.stringify(a.box)}`);
      const list = arg.slice(1).length ? arg.slice(1) : (info.names || []).slice(0, 6);
      for (const nm of list) {
        const r = await cdp.ev(`__xyDbg.measureDiff(null, ${JSON.stringify({ [nm]: 1 })}, 96, ${process.env.FRAC || '0.22'})`);
        console.log(`  ${nm.padEnd(12)} 面积=${String(r.score).padStart(6)}%  平均差=${r.mag}`);
      }
    } finally { cdp.close(); }
    return;
  }

  const results = [];
  try {
    for (const key of keys) {
      const t = Date.now();
      let r;
      try {
        r = mode === 'calib'
          ? await calibModel(cdp, key, cfg[key])
          : await auditModel(cdp, key, shots ? path.join(process.env.TEMP || '/tmp', 'xy_emo_audit_' + key) : null);
      } catch (e) {
        r = { key, fail: String(e && e.message || e) };
      }
      r.sec = +((Date.now() - t) / 1000).toFixed(1);
      results.push(r);
      if (mode === 'calib' && !r.fail) {
        cfg[key] = { slots: r.slots, idle: r.idle };
        cfg[key]._diag = { meshes: r.meshes, morphs: r.morphs, noise: r.noise,
                           labels: r.labels, labelsArea: r.labelsArea, labelsPeak: r.labelsPeak,
                           scores: r.scoreMap,
                           at: new Date().toISOString().slice(0, 19) };
      }
      if (r.fail) {
        console.log(`✗ ${key.padEnd(16)} ${r.fail}`);
      } else if (mode === 'calib') {
        const s = Object.keys(r.slots).map((k) => k + ':' +
          (r.slots[k].map((p) => p.name + '@' + p.w).join('+') || '—') +
          '(' + (r.scoreMap[k] || 0) + ')').join(' ');
        console.log(`✓ ${key.padEnd(16)} mesh=${r.meshes} morph=${r.morphs} noise=${r.noise} ${s}`);
        console.log(`    体检 ${JSON.stringify(r.labels)} | idle=` +
          (r.idle ? `${r.idle.name}@${r.idle.w}(闭眼相关 ${r.idle.ce})` : '无'));
      } else {
        const bad = Object.entries(r.labels).filter(([, v]) => v < TH_VISIBLE).map(([k]) => k);
        console.log(`${bad.length ? '⚠' : '✓'} ${key.padEnd(16)} ` +
          Object.entries(r.labels).map(([k, v]) => k + '=' + v).join(' ') +
          (bad.length ? '  弱/看不见: ' + bad.join(',') : ''));
      }
      if (mode === 'calib') {
        fs.writeFileSync(CFG_OUT + '.tmp',
          JSON.stringify(Object.assign({ _meta: {
            tool: 'tools/pmx_emo_lab.mjs calib',
            at: new Date().toISOString().slice(0, 19),
            score: '可见度 = 改动像素占比% + 峰值分块差*0.04 + 平均差*1.0；同一同步任务内 清零→摆表情→readPixels 比对，噪声实测 0%',
            note: 'slots[槽位] = [{name,w}]，w 是配方权重的放大系数；idle = 常驻微笑与其权重',
          } }, cfg), null, 2));
      }
    }
  } finally {
    cdp.close();
  }

  const ok = results.filter((r) => !r.fail);
  console.log(`\n完成 ${ok.length}/${results.length}（失败 ${results.length - ok.length}）`);

  if (mode === 'calib') {
    fs.writeFileSync(CFG_OUT, JSON.stringify(Object.assign({ _meta: {
      tool: 'tools/pmx_emo_lab.mjs calib',
      at: new Date().toISOString().slice(0, 19),
      score: '可见度 = 改动像素占比% + 峰值分块差*0.04 + 平均差*1.0；同一同步任务内 清零→摆表情→readPixels 比对，噪声实测 0%',
      note: 'slots[槽位] = [{name,w}]，w 是配方权重的放大系数；idle = 常驻微笑与其权重',
    } }, cfg), null, 2));
    fs.rmSync(CFG_OUT + '.tmp', { force: true });
    console.log(`写入 ${CFG_OUT}（${Object.keys(cfg).filter((k) => k[0] !== '_').length} 个模型）`);
  }

  /* 汇总：每个标签在多少个模型上"看得见" */
  const tally = {};
  for (const r of ok) {
    if (!r.labels) continue;
    for (const lb of LABELS) {
      tally[lb] = tally[lb] || { ok: 0, weak: 0, dead: 0 };
      const v = r.labels[lb] || 0;
      if (v >= TH_VISIBLE) tally[lb].ok++;
      else if (v >= 0.2) tally[lb].weak++;
      else tally[lb].dead++;
    }
  }
  console.log('\n标签覆盖（可见/勉强/看不见）：');
  for (const lb of LABELS) {
    const t = tally[lb];
    if (t) console.log(`  ${lb.padEnd(8)} ${CN[lb]}  ${t.ok}/${t.weak}/${t.dead}`);
  }
}

main().catch((e) => { console.log('ERROR', e); process.exit(1); });
