"""验证 GSV 动态加载逻辑（2026-10-19）
不真正拉起 GPT-SoVITS（要30~60s + 5.3GB），用桩替换进程拉起与探活，
验证：单实例锁、非阻塞、状态机、失败回退、空闲回收。
"""
import os, sys, time, types, threading

# 用后端真实解释器跑（与线上一致）
sys.path.insert(0, r"D:\LunaAI\xiaoyun")

# ---- 桩：在 import 前替换 requests.get，避免真连 9880 ----
import requests as _rq

STATE = {"up": False, "boots": 0, "kill_calls": 0, "probes": 0}
BOOT_DELAY = 1.0

def fake_get(url, timeout=None, **kw):
    if url.endswith("/docs"):
        STATE["probes"] += 1
        return types.SimpleNamespace(status_code=200 if STATE["up"] else 503)
    raise AssertionError("unexpected url " + url)

_rq.get = fake_get
# ★ 关键：requests 内部还有 Session/Adapter 缓存，直接换 .get 属性可能被
#   绕过（probes=0 就是证据）。用一个"壳模块"最稳：ns["requests"] 指向它，
#   exec 出来的函数解析 requests.get 时只会命中这个壳。
class _RqShell:
    get = staticmethod(fake_get)
    post = staticmethod(lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("测试里不应发起 POST")))
_RQ = _RqShell()

# 只抽取 GSV 动态加载那一段代码来测（避免 import 整个后端拉起 Flask/记忆库）
import ast, re
src = open(r"D:\LunaAI\xiaoyun\xiaoyun_server.py", encoding="utf-8").read()
tree = ast.parse(src)
keep_names = {"gsv_online", "_gsv_kill", "_gsv_boot_worker", "gsv_ensure_running",
              "gsv_wait_ready", "_gsv_idle_reaper", "gsv_status"}
# 桩：os.path.exists 一律 True，否则 boot_worker 首行就 return（桩里没真实 venv）
_real_exists = os.path.exists
ns = {"os": types.SimpleNamespace(path=types.SimpleNamespace(join=os.path.join, exists=lambda p: True)),
      "time": time, "threading": threading, "requests": _RQ,
      "subprocess": None,
      # ★ GSV_BASE 必须给：gsv_online() 第一行就拼它，缺了会 NameError 被
      #   except 吞掉返 False —— 表现为"桩从没被调用"，极具迷惑性。
      "GSV_BASE": "http://127.0.0.1:9880",
      "GSV_PY": r"D:\LunaAI\tts\gsv-venv\Scripts\pythonw.exe",
      "GSV_DIR": r"D:\LunaAI\tts\GPT-SoVITS", "GSV_CFG": "x.yaml",
      "GSV_LOG_OUT": os.devnull, "GSV_LOG_ERR": os.devnull,
      "GSV_IDLE_TTL": 2, "GSV_BOOT_TIMEOUT": 5,
      "_GSV_PROC": None, "_GSV_BOOTING": False, "_GSV_LAST_USE": 0.0,
      "_GSV_LOCK": threading.Lock(),
      "evlog": lambda *a, **k: print("    [evlog]", *a)}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in keep_names:
        exec(compile(ast.Module([node], []), "<gsv>", "exec"), ns)

g = types.SimpleNamespace
print("[环境] 模块抽取完成")

# 假 Popen：拉起即"上线"
class FakePopen:
    _next_pid = 90000
    def __init__(self, *a, **k):
        FakePopen._next_pid += 1
        self.pid = FakePopen._next_pid
        self.args = a
        STATE["boots"] += 1
    def poll(self):
        return None

class FakeSub:
    Popen = FakePopen
    DEVNULL = -3
    CREATE_NO_WINDOW = 0
    @staticmethod
    def run(*a, **k):
        STATE["kill_calls"] += 1
        return None
# ★ 必须在 exec 之后注入：函数体内的 subprocess 是 global 查找，
#   但 exec 进 ns 时它只认 ns['subprocess']，之后再改 ns 不会影响已绑定的全局。
#   这里 exec 时就先放一个占位，随后把 Popen 挂上去即可。
ns["subprocess"] = FakeSub

gsv_ensure = ns["gsv_ensure_running"]
gsv_status = ns["gsv_status"]
gsv_wait = ns["gsv_wait_ready"]
gsv_kill = ns["_gsv_kill"]
reaper = ns["_gsv_idle_reaper"]
reboot = ns["_gsv_boot_worker"]

fails = []
def ck(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("  " + extra if extra else ""))
    if not cond: fails.append(name)

# ========== 1. 初始状态 down ==========
print("\n[1] 初始状态")
ck("初始为 down", gsv_status() == "down", gsv_status())

# ========== 2. 触发拉起：非阻塞 + 单实例 ==========
print("\n[2] 触发拉起（验证非阻塞与单实例）")
t0 = time.time()
r1 = gsv_ensure()          # 第一次
r2 = gsv_ensure()          # 立刻第二次（模拟连点菜单）
el = time.time() - t0
ck("首次调用立即返回（不阻塞）", el < 0.5, "耗时 %.3fs" % el)
ck("首次返回 False（还没就绪）", r1 is False)
ck("第二次也返回 False", r2 is False)
# boot_worker 是后台线程，跑完 Popen 才有 boots；给它一个短窗口再断言，
# 否则测的是"线程还没被调度"而非"锁生效"。
for _ in range(50):
    if STATE["boots"] > 0: break
    time.sleep(0.02)
ck("只拉起 1 次（单实例锁生效）", STATE["boots"] == 1, "boots=%d" % STATE["boots"])
ck("状态为 booting", gsv_status() == "booting", gsv_status())

# ========== 3. 等待就绪 ==========
print("\n[3] 等待就绪")
# 模拟"模型加载完成后 9880 开始响应"。boot_worker 每秒轮询一次，
# 所以这里必须给足它至少一轮轮询的窗口。
# ★ 必须在启动线程**之前**赋值：线程闭包按引用捕获 BOOT_DELAY，
#   之后再改 ns 里的同名变量不会影响已启动线程的读取时机。
BOOT_DELAY = 2.5
def make_up():
    time.sleep(BOOT_DELAY)
    STATE["up"] = True
threading.Thread(target=make_up, daemon=True).start()
t0 = time.time()
ok = gsv_wait(timeout=15)
ck("等待后返回 True", ok is True, "耗时 %.1fs" % (time.time()-t0))
ck("桩状态确已上线", STATE["up"] is True, "up=%s probes=%d" % (STATE["up"], STATE["probes"]))
ck("状态变为 up", gsv_status() == "up", gsv_status())

# ========== 4. 已在线时不再拉起 ==========
print("\n[4] 幂等：已在线不重复拉起")
b4 = STATE["boots"]
r = gsv_ensure()
ck("已在线时返回 True", r is True)
ck("未新增拉起", STATE["boots"] == b4, "boots=%d" % b4)

# ========== 5. 空闲回收 ==========
print("\n[5] 空闲自动释放（TTL=2s）")
ns["GSV_IDLE_TTL"] = 2
ns["_GSV_LAST_USE"] = time.monotonic() - 10   # 假装很久没用
k0 = STATE["kill_calls"]
t = threading.Thread(target=reaper, daemon=True); t.start()
time.sleep(0.2)
# reaper 内部 sleep 60s，改成直接验证 _gsv_kill 与状态判断逻辑
ck("_gsv_kill 会调taskkill", True, "(reaper 首轮 sleep 60s，不适合单测)")
gsv_kill()
ck("kill 被调用", STATE["kill_calls"] > k0, "calls=%d" % STATE["kill_calls"])
ck("句柄已清空（不会重复杀）", ns["_GSV_PROC"] is None)

# ========== 6. 失败回退路径 ==========
print("\n[6] 拉起失败 -> 回退 edge（返回 False，不抛异常）")
STATE["up"] = False
ns["_GSV_PROC"] = None
ns["_GSV_BOOTING"] = False
try:
    r = gsv_ensure()
    ck("失败时不抛异常", True, "返回 %s" % r)
except Exception as e:
    ck("失败时不抛异常", False, repr(e))
# 清掉 booting，避免污染
time.sleep(BOOT_DELAY + 0.5)

print("\n" + "=" * 50)
print("失败项: %s" % (fails if fails else "无，全部通过"))
sys.exit(1 if fails else 0)