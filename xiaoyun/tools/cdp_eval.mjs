// 在真实桌面壳页面里执行一段 JS（走 WebView2 远程调试端口），打印返回值。
//
// 用途：临时诊断，不必反复改页面代码。例：
//   node tools/cdp_eval.mjs "__xyDbg.emo()"
//   node tools/cdp_eval.mjs "window.__xyMorphOverride={'怒り':1}; true"
//   node tools/cdp_eval.mjs "__xyDbg.morphAll()"
//
// 前置：桌面壳需带 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9223 启动。
import process from 'node:process';

const expr = process.argv[2];
const BREAK = process.argv[3] || 'http://127.0.0.1:9223';
if (!expr) { console.error('用法: node tools/cdp_eval.mjs "<js>" [http://127.0.0.1:9223]'); process.exit(2); }

const list = await (await fetch(BREAK + '/json/list')).json();
const page = list.find((t) => t.type === 'page' && /PMX|VRM/i.test(t.title || ''))
          || list.find((t) => t.type === 'page');
if (!page) { console.error('没有可用页面'); process.exit(1); }

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

const reply = await new Promise((res) => {
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id === 1) res(m);
  };
  ws.send(JSON.stringify({
    id: 1,
    method: 'Runtime.evaluate',
    params: { expression: expr, returnByValue: true, awaitPromise: true },
  }));
});

const d = reply.result || {};
if (d.exceptionDetails) {
  console.error('EXCEPTION:', d.exceptionDetails.exception?.description || d.exceptionDetails.text);
  ws.close(); process.exit(1);
}
console.log(JSON.stringify(d.result ? d.result.value : null));
ws.close();
process.exit(0);
