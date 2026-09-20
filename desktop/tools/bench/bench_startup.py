# -*- coding: utf-8 -*-
"""
冷启动耗时实测 —— 外部 harness。

怎么测的（这是本脚本唯一重要的事）：
    外部计时：Popen 之前的 perf_counter → 读到窗口显示那一行的 perf_counter。
              这包含了 npm / electron-vite 外壳的开销，是**用户双击 .bat 的体感**。
    内部计时：主进程自己用 process.uptime() 反推出的出生时刻 → did-finish-load。
              不含外壳开销，是**应用本身**的冷启动。

为什么要两个数：
    评审时"启动要多久"这句话是有歧义的。给两个口径并说清各自包含什么，
    比给一个含糊的数强 —— 而且内部数能证明应用本身并不慢，慢的是开发外壳。

两种模式都测：
    prod  直接跑 node_modules/electron/dist/electron.exe .
          这就是打包后双击 exe 的路径，是交付形态的真实数字
    dev   npm run dev（= 启动桌宠.bat 干的事）
          开发期天天用的路径，必须知道它有多慢，否则会误以为程序有问题

为什么要先跑一次热身：
    第一次运行要读几百 MB 的 dll / JS 文件，全是冷文件缓存。
    把这一次丢掉，剩下的才是可复现的数；否则第一个数永远是个离群值。
"""

import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_common import (  # noqa: E402
    DESKTOP_DIR,
    ELECTRON_EXE,
    RAW_DIR,
    ensure_dirs,
    kill_electron_tree,
    median,
    now_iso,
    percentile,
    run_utf8,
    sweep_electron,
    write_csv,
    write_json,
)

# 窗口显示的判据，用两个：
#   1. [bench] window_shown_ms —— 主进程自己报的，毫秒级、无解析歧义
#   2. [桌宠] 窗口已显示        —— 项目本来就有的日志，作为兜底
RE_BENCH_SHOWN = re.compile(r"\[bench\] window_shown_ms = ([\d.]+)\s+process_start_epoch = ([\d.]+)")
RE_APP_SHOWN = re.compile(r"\[桌宠\] 窗口已显示")

RUNS = {"prod": 5, "dev": 3}
TIMEOUT = {"prod": 90, "dev": 240}


def run_once(mode, tag, tmp_json):
    """跑一次，返回这一次的读数。"""
    if os.path.exists(tmp_json):
        os.remove(tmp_json)

    env = dict(os.environ)
    env["DEEPPROF_BENCH_OUT"] = tmp_json
    env["PYTHONIOENCODING"] = "utf-8"

    if mode == "prod":
        cmd = [ELECTRON_EXE, "."]
    else:
        # Windows 上 npm 是 npm.cmd（批处理），直接交给 CreateProcess 起
        cmd = ["npm.cmd", "run", "dev"]

    t_spawn = time.perf_counter()
    t_spawn_epoch_ms = time.time() * 1000
    proc = subprocess.Popen(
        cmd,
        cwd=DESKTOP_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    rec = {
        "mode": mode,
        "run": tag,
        "wall_ms": None,
        "internal_window_shown_ms": None,
        "process_start_epoch": None,
        "spawn_overhead_ms": None,
        "marker": None,
        "exit": None,
    }
    lines = []

    def reader():
        try:
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                if rec["wall_ms"] is None and RE_BENCH_SHOWN.search(line):
                    rec["wall_ms"] = (time.perf_counter() - t_spawn) * 1000
                    rec["marker"] = "bench"
                elif rec["wall_ms"] is None and RE_APP_SHOWN.search(line):
                    rec["wall_ms"] = (time.perf_counter() - t_spawn) * 1000
                    rec["marker"] = "app-log"
        except Exception:
            pass

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    deadline = time.time() + TIMEOUT[mode]
    while time.time() < deadline and rec["wall_ms"] is None and proc.poll() is None:
        time.sleep(0.02)

    # 抓到窗口显示就够了 —— 后面的稳态测量跟冷启动无关，
    # 继续等只是白等一分钟，还会让主进程自检的 TTS 真的出声吵人
    kill_electron_tree(proc.pid)
    try:
        proc.wait(timeout=15)
    except Exception:
        pass
    rec["exit"] = proc.returncode

    # 从输出里捞出主进程自己报的内部时间
    for line in lines:
        m = RE_BENCH_SHOWN.search(line)
        if m:
            rec["internal_window_shown_ms"] = float(m.group(1))
            rec["process_start_epoch"] = float(m.group(2))
            break

    if rec["process_start_epoch"] is not None:
        # harness 调 Popen → Electron 主进程真正出生。
        # dev 模式下这段就是 npm / electron-vite 外壳的开销 ——
        # 把它单独量出来，才能解释"外部计时 - 内部计时"的差值到底花在哪。
        rec["spawn_overhead_ms"] = round(rec["process_start_epoch"] - t_spawn_epoch_ms, 1)
    if rec["wall_ms"] is not None:
        rec["wall_ms"] = round(rec["wall_ms"], 1)

    if os.path.exists(tmp_json):
        os.remove(tmp_json)

    return rec


def main():
    run_utf8()
    ensure_dirs()
    sweep_electron()

    all_rows = []
    summary = {"generated_at": now_iso(), "host_note": "见 raw/insitu_*.json 的 meta 段"}

    for mode in ("prod", "dev"):
        tmp_json = os.path.join(RAW_DIR, "_startup_tmp.json")
        print(f"\n=== 冷启动实测 · {mode} 模式（先热身 1 次，不计入）===")
        warm = run_once(mode, "warmup", tmp_json)
        print(f"  热身: wall={warm['wall_ms']} ms  (丢弃)")

        rows = []
        for i in range(1, RUNS[mode] + 1):
            r = run_once(mode, i, tmp_json)
            rows.append(r)
            all_rows.append(r)
            print(
                f"  第 {i} 次: 外部 {r['wall_ms']} ms | 内部(主进程自出生) "
                f"{r['internal_window_shown_ms']} ms | 判据={r['marker']}"
            )
            time.sleep(1.5)

        walls = [r["wall_ms"] for r in rows]
        inners = [r["internal_window_shown_ms"] for r in rows]
        summary[mode] = {
            "runs": RUNS[mode],
            "wall_ms": {
                "samples": walls,
                "min": min(walls),
                "median": round(median(walls), 1),
                "max": max(walls),
                "p95": round(percentile(walls, 95), 1),
            },
            "internal_window_shown_ms": {
                "samples": inners,
                "min": min(inners) if None not in inners else None,
                "median": round(median(inners), 1) if None not in inners else None,
                "max": max(inners) if None not in inners else None,
            },
        }
        print(
            f"  → {mode} 中位数: 外部 {summary[mode]['wall_ms']['median']} ms | "
            f"内部 {summary[mode]['internal_window_shown_ms']['median']} ms"
        )

    sweep_electron()

    write_csv(os.path.join(RAW_DIR, "startup_runs.csv"), all_rows)
    write_json(os.path.join(RAW_DIR, "startup_summary.json"), summary)
    print("\n已写出 raw/startup_runs.csv 与 raw/startup_summary.json")


if __name__ == "__main__":
    main()
