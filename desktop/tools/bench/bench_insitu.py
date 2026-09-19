# -*- coding: utf-8 -*-
"""
稳态实测 —— 内存 / 渲染帧率 / 事件延迟。

怎么测的：
    跑**真实的打包产物**（node_modules/electron/dist/electron.exe .），
    主进程里的性能探针（src/main/index.js 第四节）在被测进程内部读数，
    跑完把原始 JSON 落到 raw/insitu_run<N>.json。

为什么不用外部 Get-Process 当主口径：
    Electron 是多进程，外部只能看到一堆同名的 electron.exe，
    分不清哪个是主进程、哪个是渲染进程、哪个是 GPU。
    Electron 自己的 app.getAppMetrics() 带角色标注（Browser / Tab / GPU / Utility），
    正好对上"主进程和渲染进程分别测"这个要求。
    ——但外部读数仍然跑一份做交叉验证（raw/memory_external.csv），
    两边的数能对上才说明内存口径没搞错。

为什么内存要采四次：
    启动瞬间 / 8 秒稳态 / 15 秒 / 70 秒，四个点。
    只报一个数没意义 —— 内存是随时间长的，报告里必须说清是哪个时刻的数。
"""

import os
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
    read_json,
    run_utf8,
    sweep_electron,
    write_csv,
    write_json,
)

RUNS = 3          # 想要几次**完整**跑完的样本
MAX_ATTEMPTS = 6  # 但最多试几次
# ⚠️ 为什么要有 MAX_ATTEMPTS：
#    实测期间被测机器上还有人在改渲染层代码（App.jsx / RigPet.jsx / tts.js 都在动），
#    应用有时候跑一半就退出了（窗口被关掉 / 新代码崩了，日志里看不出是谁干的）。
#    与其把这当成"测量失败"，不如重试到拿够完整样本 —— 但要设上限，
#    免得环境一直不稳的时候无限重试。
TIMEOUT = 180

# 外部内存交叉验证用的 PowerShell。
# 按可执行文件路径过滤，避免把用户开着的 VS Code / Discord 也算进来
PS_MEM = (
    "Get-Process electron -ErrorAction SilentlyContinue | "
    "Where-Object { $_.Path -like '*DeepProf*' } | "
    "ForEach-Object { '{0},{1}' -f $_.Id, $_.WorkingSet64 }"
)


def sample_memory(stop_evt, out_rows):
    """后台线程：每 3 秒从外面量一次所有 electron 进程的内存。"""
    while not stop_evt.is_set():
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_MEM],
                capture_output=True,
                text=True,
                timeout=25,
            )
            procs, total = [], 0
            for line in r.stdout.splitlines():
                line = line.strip()
                if "," not in line:
                    continue
                pid, ws = line.split(",", 1)
                mb = round(int(ws) / 1024 / 1024, 1)
                procs.append({"pid": int(pid), "working_set_mb": mb})
                total += mb
            if procs:
                out_rows.append(
                    {
                        "t": round(time.time(), 1),
                        "process_count": len(procs),
                        "total_working_set_mb": round(total, 1),
                        "per_process": "; ".join(f"{p['pid']}={p['working_set_mb']}MB" for p in procs),
                    }
                )
        except Exception:
            pass
        stop_evt.wait(3)


def run_once(idx):
    out_json = os.path.join(RAW_DIR, f"insitu_run{idx}.json")
    if os.path.exists(out_json):
        os.remove(out_json)

    env = dict(os.environ)
    env["DEEPPROF_BENCH_OUT"] = out_json
    env["DEEPPROF_BENCH_EXIT"] = "1"

    print(f"\n=== 稳态实测 第 {idx} 次（约 75 秒，期间桌宠窗口会真的出现并说话）===")
    t0 = time.time()
    proc = subprocess.Popen(
        [ELECTRON_EXE, "."],
        cwd=DESKTOP_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    stop_evt = threading.Event()
    ext_rows = []
    mem_th = threading.Thread(target=sample_memory, args=(stop_evt, ext_rows), daemon=True)
    mem_th.start()

    log_lines = []

    def reader():
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                log_lines.append(line)
                if "[bench]" in line or "窗口已显示" in line or "渲染进程]" in line:
                    print("   " + line)
        except Exception:
            pass

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    try:
        proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print("   ⚠️ 超时，强制结束")
        kill_electron_tree(proc.pid)

    stop_evt.set()
    time.sleep(0.3)
    kill_electron_tree(proc.pid)
    time.sleep(1.0)
    sweep_electron()

    wall = time.time() - t0
    print(f"   用时 {wall:.1f}s，退出码 {proc.returncode}")

    with open(os.path.join(RAW_DIR, f"insitu_run{idx}.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    return out_json, ext_rows


def main():
    run_utf8()
    ensure_dirs()
    sweep_electron()

    # 先把上一轮的产物清掉：否则"这次没跑出来"会被上一轮留下的旧文件冒充成结果
    for f in os.listdir(RAW_DIR):
        if f.startswith("insitu_run") or f == "insitu_summary.json":
            try:
                os.remove(os.path.join(RAW_DIR, f))
            except OSError:
                pass

    results, ext_all = [], []
    attempt = 0
    while len(results) < RUNS and attempt < MAX_ATTEMPTS:
        attempt += 1
        p, ext = run_once(attempt)
        if os.path.exists(p):
            results.append(read_json(p))
            for r in ext:
                ext_all.append(dict(r, run=len(results)))
        else:
            print(f"   ⚠️ 第 {attempt} 次没有产出结果（应用中途退出），重试")
        time.sleep(2)

    if len(results) < RUNS:
        print(f"   ⚠️ 只要到 {len(results)} 份完整样本（目标 {RUNS}），报告中会如实说明")

    if ext_all:
        base = ext_all[0]["t"]
        for r in ext_all:
            r["t_rel_s"] = round(r["t"] - base, 1)
        write_csv(
            os.path.join(RAW_DIR, "memory_external.csv"),
            ext_all,
            ["run", "t_rel_s", "process_count", "total_working_set_mb", "per_process"],
        )

    # 汇总
    summary = {"runs": len(results), "per_run": []}
    for i, r in enumerate(results):
        fps = r.get("fps") or {}
        fps_s = r.get("fps_streaming") or {}
        lat = [x["send_to_dom_ms"] for x in r.get("latency", []) if x.get("send_to_dom_ms") is not None]
        latf = [x["send_to_frame_ms"] for x in r.get("latency", []) if x.get("send_to_frame_ms") is not None]
        mem = {m["tag"]: m for m in r.get("memory", [])}

        def role_mb(tag, role):
            s = mem.get(tag)
            if not s:
                return None
            for p in s["processes"]:
                if p["type"] == role:
                    return p["working_set_mb"]
            return None

        lat_long = [
            {"round": x["round"], "e2e_ms": x["send_to_dom_ms"], "long_tasks": x.get("long_tasks")}
            for x in r.get("latency", [])
            if x.get("long_tasks")
        ]
        summary["per_run"].append(
            {
                "run": i + 1,
                "long_tasks": lat_long,
                "mode": r["meta"]["mode"],
                "window_shown_ms": r["timeline"].get("window_shown_after_start_ms"),
                "fps_idle": fps.get("raf_fps"),
                "fps_streaming": fps_s.get("raf_fps"),
                "draws_per_frame_idle": fps.get("draws_per_frame"),
                "draws_per_frame_streaming": fps_s.get("draws_per_frame"),
                "latency_send_to_dom_ms": lat,
                "latency_send_to_frame_ms": latf,
                "mem_main_mb_steady": role_mb("t8s_稳态", "Browser"),
                "mem_renderer_mb_steady": role_mb("t8s_稳态", "Tab"),
                "mem_gpu_mb_steady": role_mb("t8s_稳态", "GPU"),
                "mem_utility_mb_steady": role_mb("t8s_稳态", "Utility"),
                "mem_total_mb_steady": mem.get("t8s_稳态", {}).get("total_working_set_mb"),
                "mem_total_mb_70s": mem.get("t60s_测量结束", {}).get("total_working_set_mb"),
                "notes": r.get("notes", []),
                "stream_mutations": r.get("stream_mutations", []),
            }
        )
    write_json(os.path.join(RAW_DIR, "insitu_summary.json"), summary)
    print("\n已写出 raw/insitu_run*.json、raw/insitu_summary.json、raw/memory_external.csv")


if __name__ == "__main__":
    main()
