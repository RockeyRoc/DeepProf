# -*- coding: utf-8 -*-
"""
性能实测公共部分 —— 路径、进程工具、CSV/JSON 落盘。

为什么单独一个文件：
    冷启动、稳态、体积三个脚本都要「跑应用 / 杀进程树 / 写结果」这三件事，
    各写一遍的话，Windows 上杀进程树那种坑要踩三遍。
"""

import os
import csv
import json
import subprocess
import sys
import time

# ── 路径 ──
# 本文件在 <项目>/desktop/tools/bench/ 下
BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
DESKTOP_DIR = os.path.abspath(os.path.join(BENCH_DIR, "..", ".."))
PROJECT_DIR = os.path.abspath(os.path.join(DESKTOP_DIR, ".."))
OUT_DIR = os.path.join(PROJECT_DIR, "_交付物", "性能数据")
RAW_DIR = os.path.join(OUT_DIR, "raw")
CHART_DIR = os.path.join(OUT_DIR, "图表")

ELECTRON_EXE = os.path.join(DESKTOP_DIR, "node_modules", "electron", "dist", "electron.exe")


def ensure_dirs():
    for d in (OUT_DIR, RAW_DIR, CHART_DIR):
        os.makedirs(d, exist_ok=True)


def kill_electron_tree(pid):
    """杀掉整个进程树。

    ⚠️ 必须用 /T 杀树：Electron 是多进程，只杀父进程的话
    GPU / 渲染 / Utility 子进程会变成孤儿留在后台 —— 用户下次启动就会
    发现屏幕右下角有两只桌宠，而且都关不掉。
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        pass


def sweep_electron():
    """兜底清扫：把还活着的 electron.exe 全收掉（只清本项目 dist 下的那个）。

    按可执行文件路径过滤，避免误杀用户自己开着的其他 Electron 应用
    （VS Code、Discord 这些也都是 electron.exe）。
    """
    try:
        ps = (
            "Get-Process electron -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Path -like '*DeepProf*electron*' } | "
            "Stop-Process -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        pass


def write_csv(path, rows, fieldnames=None):
    if not rows:
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    # utf-8-sig：Excel 双击打开中文表头不会变乱码，这是给评审老师看的，不能花
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def percentile(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_utf8():
    """让 Windows 控制台的 print 也走 UTF-8。

    ⚠️ 不设的话中文在 GBK 控制台里会 print 成乱码 —— 数据本身是对的，
    但看日志的人会以为哪里坏了。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
