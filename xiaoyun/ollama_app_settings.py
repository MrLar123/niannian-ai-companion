# -*- coding: utf-8 -*-
"""读写 Ollama 桌面端（托盘 ollama app.exe）的私有设置库 db.sqlite。

只暴露三个安全操作：
  show                      查看当前设置
  set-models <目录>         修改「模型存放目录」（Ollama 桌面端会用它覆盖 OLLAMA_MODELS 环境变量）
  set-auto-update 0|1       开关「自动更新」（开启时后台更新可能导致服务重启）
  set-selected-model <模型名> 修改桌面端「当前选中模型」（带本地存在性校验，修『模型不支持』专用）

说明：
  * 桌面端设置库位于 %LOCALAPPDATA%\\Ollama\\db.sqlite
  * 它会以该库中的 models 字段为准，忽略用户环境变量 OLLAMA_MODELS，
    这正是「模型明明在 D:\\OllamaModels，托盘服务却报模型库为空」的原因。
  * 修改前请先退出 Ollama（托盘右键退出），否则内存中的设置可能回写覆盖。
"""
import os
import sys
import json
import shutil
import sqlite3
import datetime

sys.stdout.reconfigure(encoding="utf-8")

DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Ollama", "db.sqlite")


def _connect():
    if not os.path.exists(DB):
        raise SystemExit("找不到 Ollama 设置库: %s" % DB)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _backup():
    d = os.path.join(os.path.dirname(DB), "backup")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(d, "db_%s.sqlite" % stamp)
    shutil.copy2(DB, dst)
    return dst


def cmd_show():
    con = _connect()
    row = con.execute("select * from settings").fetchone()
    con.close()
    if not row:
        print("settings 表为空")
        return
    keys = [k for k in row.keys() if k in ("models", "auto_update_enabled", "context_length",
                                           "expose", "selected_model", "browser")]
    for k in keys:
        print("  %-22s = %s" % (k, row[k]))
    print("  db 路径                = %s" % DB)


def cmd_set(key, value):
    con = _connect()
    cur = con.execute("update settings set %s = ? where id = 1" % key, (value,))
    con.commit()
    row = con.execute("select %s as v from settings where id = 1" % key).fetchone()
    con.close()
    print("已更新 %s -> %s （影响 %d 行）" % (key, row["v"], cur.rowcount))


def _local_models():
    """返回本地 `ollama list` 中的模型名列表；ollama 不可用/为空时返回 None。

    用于写入 selected_model 前校验，防止再次写入本地不存在的模型名。
    """
    import subprocess
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True,
                             text=True, encoding="utf-8", timeout=20)
        if out.returncode != 0:
            return None
        names = [ln.split()[0] for ln in out.stdout.splitlines()[1:] if ln.strip()]
        return names or None
    except Exception:
        return None


def main():
    args = sys.argv[1:]
    if not args or args[0] == "show":
        cmd_show()
        return
    if args[0] == "set-models":
        if len(args) < 2:
            raise SystemExit("用法: set-models <目录>")
        target = args[1]
        if not os.path.isdir(target):
            raise SystemExit("目录不存在: %s" % target)
        print("备份:", _backup())
        cmd_set("models", target)
        return
    if args[0] == "set-auto-update":
        val = int(args[1]) if len(args) > 1 else 0
        print("备份:", _backup())
        cmd_set("auto_update_enabled", val)
        return
    if args[0] == "set-selected-model":
        # 修复「模型不支持」的核心命令：把桌面端当前选中模型改成本地真实存在的模型。
        # 历史坑：selected_model 曾被写成 gemma4:31b-cloud（云端模型，本地未安装），
        # 导致调用方一律报「模型不支持」。故此处强制校验本地 ollama list。
        if len(args) < 2:
            raise SystemExit("用法: set-selected-model <模型名>   例如: set-selected-model qwen3.5:9b")
        target, force = args[1], ("--force" in args)
        local = _local_models()
        if local is not None:
            if target in local:
                print("校验通过: %s 存在于本地模型库" % target)
            else:
                print("警告: 本地 ollama list 中找不到 '%s'" % target)
                print("  当前本地可用:", ", ".join(local))
                if not force:
                    raise SystemExit(
                        "已中止。写入本地不存在的模型会导致调用方报『模型不支持』。\n"
                        "   若确要写入云端/远端模型，请追加 --force。")
                print("  --force 已指定，强制写入。")
        print("备份:", _backup())
        cmd_set("selected_model", target)
        print("完成。请完全退出 Ollama 托盘后再启动，改动才不会被内存中的设置回写覆盖。")
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
