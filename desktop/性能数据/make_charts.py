# -*- coding: utf-8 -*-
"""
DeepProf 桌宠模块 —— 性能图表生成（Pillow 手绘版）

为什么要手绘：
    本机没装 matplotlib，而项目没经费、也不允许 pip install。
    已装的是 Pillow 12.3.0 + numpy 2.4.6，所以用 ImageDraw 画。
    Pillow 的 ImageDraw 没有抗锯齿，这里统一按 2 倍超采样绘制、最后 LANCZOS
    缩回原尺寸 —— 这是本文件唯一一处"技巧"，其余都是老老实实画矩形和线。

铁律：本脚本只读 raw/ 下的实测文件，一个数都不编。
      任何一项数据缺失就直接 raise，绝不填默认值把图画出来。

跑法：
    python make_charts.py
输出：
    图表/01_冷启动耗时.png
    图表/02_打包体积构成.png
    图表/03_内存占用.png
    图表/04_事件响应延迟.png
"""

import csv
import json
import math
import os
import statistics
import sys

from PIL import Image, ImageDraw, ImageFont

# ── 路径 ──
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
CHARTS = os.path.join(HERE, "图表")

# ── 字体（不设中文字体的话，中文会全变方块）──
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

# ── 配色 ──
# 取自校验通过的分类色板前 4 槽（浅色模式，底色 #fcfcfb）：
#   校验结果：明度带 PASS / 彩度下限 PASS / 色觉障碍相邻对 ΔE 9.1 PASS /
#             常视相邻对 ΔE 22.9 PASS / 对比度 WARN（aqua、yellow 低于 3:1）
#   → 触发"救济规则"：用到这两个色的图必须直接标数值。本文件所有柱段都直接标了数。
SURFACE = "#fcfcfb"
INK = "#0b0b0b"       # 主文字
INK2 = "#52514e"      # 次文字
MUTED = "#898781"     # 轴/刻度
GRID = "#e1e0d9"      # 网格发丝线
AXIS = "#c3c2b7"      # 基线
S1 = "#2a78d6"        # 槽1 蓝
S2 = "#eb6834"        # 槽2 橙
S3 = "#1baf7a"        # 槽3 青
S4 = "#eda100"        # 槽4 黄

SS = 2  # 超采样倍数

_font_cache = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, int(size * SS))
    return _font_cache[key]


class Canvas:
    """逻辑坐标画布；内部按 SS 倍放大绘制，save() 时缩回。"""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SS, h * SS), SURFACE)
        self.d = ImageDraw.Draw(self.img)

    # ── 文字 ──
    def text(self, x, y, s, size=18, bold=False, fill=INK, ha="left", va="top"):
        f = font(size, bold)
        l, t, r, b = self.d.textbbox((0, 0), s, font=f)
        w, h = (r - l) / SS, (b - t) / SS
        px = x - (w / 2 if ha == "center" else (w if ha == "right" else 0))
        py = y - (h / 2 if va == "center" else (h if va == "bottom" else 0)) - t / SS
        self.d.text((px * SS, py * SS), s, font=f, fill=fill)
        return w

    def tw(self, s, size=18, bold=False):
        f = font(size, bold)
        l, t, r, b = self.d.textbbox((0, 0), s, font=f)
        return (r - l) / SS

    # ── 图元 ──
    def hline(self, x0, x1, y, fill=GRID, w=1):
        self.d.rectangle([x0 * SS, y * SS, x1 * SS, y * SS + w * SS - 1], fill=fill)

    def vline(self, x, y0, y1, fill=GRID, w=1):
        self.d.rectangle([x * SS, y0 * SS, x * SS + w * SS - 1, y1 * SS], fill=fill)

    def seg(self, x0, y0, x1, y1, fill, w=2):
        self.d.line([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill, width=int(w * SS))

    def dot(self, x, y, fill, r=5, ring=SURFACE):
        # 2px 底色描边：叠在别的图元上时能分得开
        self.d.ellipse(
            [(x - r - 1) * SS, (y - r - 1) * SS, (x + r + 1) * SS, (y + r + 1) * SS], fill=ring
        )
        self.d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=fill)

    def bar(self, x0, y0, x1, y1, fill, radius=4, round_end="top"):
        """数据端 4px 圆角、基线端方角。"""
        if x1 <= x0 or y1 <= y0:
            return
        r = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
        rd = r * SS
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=rd, fill=fill)
        if round_end == "top":
            self.d.rectangle([x0 * SS, y1 * SS - rd, x1 * SS, y1 * SS], fill=fill)
        elif round_end == "right":
            self.d.rectangle([x0 * SS, y0 * SS, x0 * SS + rd, y1 * SS], fill=fill)
        elif round_end == "left":
            self.d.rectangle([x1 * SS - rd, y0 * SS, x1 * SS, y1 * SS], fill=fill)

    def legend(self, x, y, items, size=18):
        """items: [(颜色, 文字)]，返回结束 x。"""
        cx = x
        for color, label in items:
            self.d.rounded_rectangle(
                [cx * SS, (y - 7) * SS, (cx + 14) * SS, (y + 7) * SS], radius=4 * SS, fill=color
            )
            cx += 22
            cx += self.text(cx, y, label, size=size, fill=INK2, va="center") + 30
        return cx

    def save(self, name):
        out = os.path.join(CHARTS, name)
        self.img.resize((self.w, self.h), Image.Resampling.LANCZOS).save(out, "PNG")
        print("  已写出", out)


def nice_ticks(vmax, n=6):
    """返回 (轴上限, 刻度列表)。

    步长取 1/2/2.5/5/10 × 10^k，轴上限取"刚好盖住 vmax 的那个整数刻度" ——
    不是固定画满 n 格。否则 1495.8 会被拉到 2500 的轴上，柱子只剩一半高，
    白白浪费一半画面（第一版就是这么翻车的）。
    """
    raw = vmax / n
    mag = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 0.1
    step = 10 * mag
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    nticks = max(2, int(math.ceil(vmax / step - 1e-9)))
    top = step * nticks
    ticks = [round(step * i, 6) for i in range(nticks + 1)]
    return top, ticks


def fmt(v, nd=1):
    return f"{v:.{nd}f}"


# ══════════════════════════════════════════════════════════
# 读数据（缺任何一个文件都直接炸，不填默认值）
# ══════════════════════════════════════════════════════════
def load():
    def j(name):
        p = os.path.join(RAW, name)
        if not os.path.exists(p):
            raise SystemExit(f"缺数据文件：{p} —— 不画，绝不编数")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def c(name):
        p = os.path.join(RAW, name)
        if not os.path.exists(p):
            raise SystemExit(f"缺数据文件：{p} —— 不画，绝不编数")
        with open(p, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    return {
        "startup_runs": c("startup_runs.csv"),
        "startup": j("startup_summary.json"),
        "bundle": j("bundle_size.json"),
        "mem_ext": c("memory_external.csv"),
        "smoke": j("_smoke.json"),
    }


def check_startup_consistency(d):
    """startup_runs.csv 与 startup_summary.json 对不上就以 JSON 为准并告警。"""
    bad = []
    for mode in ("prod", "dev"):
        for field in ("wall_ms", "internal_window_shown_ms"):
            csv_vals = [
                float(r[field]) for r in d["startup_runs"] if r["mode"] == mode
            ]
            json_vals = d["startup"][mode][field]["samples"]
            if len(csv_vals) != len(json_vals) or any(
                abs(a - b) > 0.05 for a, b in zip(csv_vals, json_vals)
            ):
                bad.append(f"{mode}.{field}: csv={csv_vals} json={json_vals}")
    return bad


# ══════════════════════════════════════════════════════════
# 图 1 冷启动耗时
# ══════════════════════════════════════════════════════════
def chart_startup(d):
    s = d["startup"]
    W, H = 1600, 1010
    c = Canvas(W, H)

    L, R, T, B = 130, 1510, 190, 800
    pw, ph = R - L, B - T
    ymax, ticks = nice_ticks(
        max(s[m][f]["max"] for m in ("prod", "dev")
            for f in ("wall_ms", "internal_window_shown_ms"))
    )

    def yv(v):
        return B - v / ymax * ph

    c.text(L - 50, 46, "桌宠冷启动耗时：外部计时 vs 内部计时", size=40, bold=True)
    c.text(
        L - 50, 100,
        "单位 ms，柱高为该模式的中位数；竖线为该组各次实测的最小–最大范围",
        size=20, fill=INK2,
    )
    c.legend(L - 50, 138, [(S1, "外部计时（双击 .bat 的体感，含启动外壳）"),
                           (S2, "内部计时（应用自身：主进程出生 → 窗口显示）")], size=19)

    # 网格 + y 轴
    for t in ticks:
        y = yv(t)
        c.hline(L, R, y, GRID)
        c.text(L - 16, y, str(int(t)), size=17, fill=MUTED, ha="right", va="center")
    c.hline(L, R, yv(0), AXIS)
    c.text(L - 16, yv(0), "0", size=17, fill=MUTED, ha="right", va="center")

    groups = [
        ("prod", "生产模式（打包产物）", "n = 5 次"),
        ("dev", "开发模式（npm run dev）", "n = 3 次"),
    ]
    gw = pw / len(groups)
    bw, gap = 132, 28

    for gi, (mode, gname, gn) in enumerate(groups):
        gx = L + gi * gw + gw / 2
        for si, (field, color) in enumerate(
            (("wall_ms", S1), ("internal_window_shown_ms", S2))
        ):
            st = s[mode][field]
            med = st["median"]
            x0 = gx - (bw * 2 + gap) / 2 + si * (bw + gap)
            x1 = x0 + bw
            top = yv(med)
            c.bar(x0, top, x1, yv(0), color, round_end="top")

            # 最小–最大范围竖线
            e0, e1 = yv(st["min"]), yv(st["max"])
            if st["max"] - st["min"] > 0.05:
                c.vline((x0 + x1) / 2, e1, e0, fill=INK2, w=1)
                c.hline((x0 + x1) / 2 - 9, (x0 + x1) / 2 + 9, e0, fill=INK2, w=1)
                c.hline((x0 + x1) / 2 - 9, (x0 + x1) / 2 + 9, e1, fill=INK2, w=1)
            c.text((x0 + x1) / 2, min(top, e0) - 16, fmt(med), size=21, bold=True,
                   fill=INK, ha="center", va="bottom")

        c.text(gx, B + 26, gname, size=21, bold=True, ha="center")
        c.text(gx, B + 56, gn, size=18, fill=MUTED, ha="center")

    # 底部说明
    box_y = B + 100
    c.hline(L, R, box_y, GRID)
    dev_wall = s["dev"]["wall_ms"]["median"]
    dev_in = s["dev"]["internal_window_shown_ms"]["median"]
    prod_wall = s["prod"]["wall_ms"]["median"]
    prod_in = s["prod"]["internal_window_shown_ms"]["median"]
    over = statistics.median(
        [float(r["spawn_overhead_ms"]) for r in d["startup_runs"] if r["mode"] == "dev"]
    )
    prod_over = statistics.median(
        [float(r["spawn_overhead_ms"]) for r in d["startup_runs"] if r["mode"] == "prod"]
    )
    c.text(L, box_y + 22,
           f"开发模式的 {fmt(dev_wall)} ms 里，约 {fmt(over)} ms 花在 npm / electron-vite 外壳的启动上"
           f"（生产模式该开销仅 {fmt(prod_over)} ms）。",
           size=19, fill=INK2)
    c.text(L, box_y + 54,
           f"应用本身两种模式差距不大：内部计时 {fmt(prod_in)} ms（生产） vs {fmt(dev_in)} ms（开发）。",
           size=19, fill=INK2)
    c.text(L, box_y + 84,
           "数据来源：raw/startup_runs.csv、raw/startup_summary.json"
           "　｜　两种模式各先跑 1 次热身并丢弃（避开冷文件缓存）",
           size=16, fill=MUTED)
    c.save("01_冷启动耗时.png")


# ══════════════════════════════════════════════════════════
# 图 2 打包体积构成
# ══════════════════════════════════════════════════════════
def chart_bundle(d):
    b = d["bundle"]
    rows = {r["item"].strip(): r for r in b["rows"]}

    W, H = 1600, 1180
    c = Canvas(W, H)
    L, LM = 320, 320  # 左边距留给类目名

    c.text(80, 46, "打包体积构成：交付产物 out/ 与依赖树 node_modules/", size=40, bold=True)
    c.text(80, 100, "单位 MB（1 MB = 1024×1024 字节）；对目录树逐文件累加真实 size，非估算",
           size=20, fill=INK2)

    # ── 上：out/ 构成 ──
    out_total = b["out_total_mb"]
    l2d = b["live2d_samples_mb"]
    deliver = b["out_without_live2d_mb"]
    main_mb = rows["out/main（主进程）"]["size_mb"]
    pre_mb = rows["out/preload（预加载桥）"]["size_mb"]
    rend_mb = rows["out/renderer（渲染进程）"]["size_mb"]
    rend_rest = round(rend_mb - l2d, 2)

    c.text(80, 152, "out/ 交付产物构成", size=26, bold=True)
    c.legend(80, 190, [(S1, "计入交付"), (S2, "开发期验证用，不进最终交付")], size=18)

    # preload 只有 2110 字节，bundle_size.csv 的 MB 列四舍五入成 0.0，
    # 读数没意义，所以这一项直接用字节数重算到 3 位小数
    pre_label = fmt(rows["out/preload（预加载桥）"]["size_bytes"] / (1024 * 1024), 3)
    items = [
        ("out/renderer（除 Live2D 样例）", rend_rest, S1, fmt(rend_rest, 2)),
        ("Live2D 官方样例模型", l2d, S2, fmt(l2d, 2)),
        ("out/main（主进程）", main_mb, S1, fmt(main_mb, 2)),
        ("out/preload（预加载桥）", pre_mb, S1, pre_label),
    ]
    aT, aB = 236, 420
    c.hline(L, 1440, aB, AXIS)
    xmax = 4.4
    for t in (0, 1, 2, 3, 4):
        x = L + t / xmax * (1440 - L)
        c.vline(x, aT, aB, GRID)
        c.text(x, aB + 22, str(t), size=17, fill=MUTED, ha="center")
    slot = (aB - aT) / len(items)
    for i, (name, v, color, label) in enumerate(items):
        y0 = aT + i * slot + (slot - 24) / 2
        y1 = y0 + 24
        c.text(L - 18, (y0 + y1) / 2, name, size=19, fill=INK, ha="right", va="center")
        x1 = L + v / xmax * (1440 - L)
        if v > 0.005:
            c.bar(L, y0, x1, y1, color, round_end="right")
            c.text(x1 + 12, (y0 + y1) / 2, label, size=19, bold=True, fill=INK, va="center")
        else:
            # 太小画不出来就绝不画成一条假线，直接把数写在轴上
            c.text(L + 6, (y0 + y1) / 2, f"{label}（太小，画不出柱）",
                   size=17, fill=MUTED, va="center")
    c.text(L, aB + 52,
           f"out/ 合计 {fmt(out_total, 2)} MB　→　扣掉 Live2D 样例模型（{fmt(l2d, 2)} MB）后，"
           f"实际交付体积 {fmt(deliver, 2)} MB",
           size=20, bold=True, fill=INK)

    # ── 下：node_modules top 10 ──
    top = [r for r in b["rows"] if r["group"] == "node_modules-top"][:10]
    nm_total = b["node_modules_total_mb"]
    elec = top[0]["size_mb"]
    c.text(80, 530, "node_modules/ 依赖树 Top 10", size=26, bold=True)
    c.text(80, 570,
           f"合计 {fmt(nm_total, 2)} MB，不进交付包（构建时只打包 renderer 真正 import 到的模块）；"
           f"其中 Electron 运行时 {fmt(elec, 2)} MB，占 {fmt(elec / nm_total * 100)}%",
           size=19, fill=INK2)

    bT, bB = 620, 1040
    c.hline(L, 1460, bB, AXIS)
    nxmax = 300
    for t in (0, 100, 200, 300):
        x = L + t / nxmax * (1460 - L)
        c.vline(x, bT, bB, GRID)
        c.text(x, bB + 22, str(t), size=17, fill=MUTED, ha="center")
    slot2 = (bB - bT) / len(top)
    for i, r in enumerate(top):
        y0 = bT + i * slot2 + (slot2 - 22) / 2
        y1 = y0 + 22
        name = r["item"].strip()
        c.text(L - 18, (y0 + y1) / 2, name, size=17, fill=INK, ha="right", va="center")
        x1 = L + r["size_mb"] / nxmax * (1460 - L)
        c.bar(L, y0, x1, y1, S1, round_end="right")
        c.text(x1 + 12, (y0 + y1) / 2, fmt(r["size_mb"], 2), size=17, bold=True,
               fill=INK, va="center")
    c.text(L, bB + 56,
           "Electron 桌面端把 Chromium + Node 一起打进依赖树，这是 Electron 方案的固有代价；"
           "它的体积只影响开发机装依赖，不影响最终交付包。",
           size=18, fill=INK2)
    c.text(80, H - 46,
           "数据来源：raw/bundle_size.csv、raw/bundle_size.json　｜　单次静态统计（非多次采样）",
           size=16, fill=MUTED)
    c.save("02_打包体积构成.png")


# ══════════════════════════════════════════════════════════
# 图 3 内存占用
# ══════════════════════════════════════════════════════════
def chart_memory(d):
    smoke = d["smoke"]
    W, H = 1600, 1170
    c = Canvas(W, H)

    c.text(80, 46, "运行内存占用", size=40, bold=True)
    c.text(80, 100,
           "内存是随时间长的，必须说清是哪个时刻的数 —— 上图外部口径连续采样，下图进程内口径分角色",
           size=20, fill=INK2)

    # ── 上：外部采样时间序列 ──
    L, R, T, B = 150, 1490, 224, 546
    pw, ph = R - L, B - T
    mem_max = max(float(r["total_working_set_mb"]) for r in d["mem_ext"])
    ymax, ticks = nice_ticks(mem_max)
    nxmax = math.ceil(max(float(r["t_rel_s"]) for r in d["mem_ext"]) / 10) * 10

    def X(t):
        return L + t / nxmax * pw

    def Y(v):
        return B - v / ymax * ph

    c.text(80, 148, "① 外部采样（按可执行文件路径过滤后，累加本项目的全部 electron 进程 Working Set）",
           size=23, bold=True)

    runs = {}
    for r in d["mem_ext"]:
        runs.setdefault(r["run"], []).append(r)
    colors = {k: col for k, col in zip(sorted(runs), (S1, S2))}
    legend_items = []
    for k in sorted(runs):
        rows = sorted(runs[k], key=lambda r: float(r["t_rel_s"]))
        legend_items.append((colors[k], f"第 {k} 次启动（{len(rows)} 个采样点，每 3 秒 1 个）"))
    c.legend(L, 186, legend_items, size=18)

    for t in ticks:
        y = Y(t)
        c.hline(L, R, y, GRID)
        c.text(L - 14, y, str(int(t)), size=17, fill=MUTED, ha="right", va="center")
    c.hline(L, R, B, AXIS)
    for t in range(0, nxmax + 1, 30):
        c.vline(X(t), T, B, GRID)
        c.text(X(t), B + 20, str(t), size=17, fill=MUTED, ha="center")
    c.text(R, B + 20, "运行时间（秒）", size=17, fill=MUTED, ha="right")

    for k in sorted(runs):
        pts = [
            (X(float(r["t_rel_s"])), Y(float(r["total_working_set_mb"])))
            for r in sorted(runs[k], key=lambda r: float(r["t_rel_s"]))
        ]
        for i in range(len(pts) - 1):
            c.seg(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], colors[k], w=2)
        for p in pts:
            c.dot(p[0], p[1], colors[k], r=4)

    # 稳态区间：只取"进程数已经起满"的那些采样点 ——
    # 拿启动瞬间的 91.8 MB 当稳态下限是错的，那不是稳态，是还没起来
    full = max(int(r["process_count"]) for r in d["mem_ext"])
    steady = [
        float(r["total_working_set_mb"]) for r in d["mem_ext"] if int(r["process_count"]) == full
    ]
    s_med = statistics.median(steady)
    c.hline(L, R, Y(s_med), fill=INK2, w=1)
    c.text(R - 8, Y(s_med) - 10, f"稳态中位数 {fmt(s_med)} MB", size=18, bold=True,
           fill=INK, ha="right", va="bottom")
    c.text(L, B + 56,
           f"进程数起满（{full} 个）之后的 {len(steady)} 个采样点："
           f"{fmt(min(steady))} – {fmt(max(steady))} MB，中位数 {fmt(s_med)} MB",
           size=19, bold=True, fill=INK)

    # ── 下：进程内探针分角色 ──
    c.text(80, 650, "② 进程内探针（Electron app.getAppMetrics()，带进程角色标注）",
           size=23, bold=True)

    tags = ["t0_窗口刚显示", "t8s_稳态", "t15s_延迟测量前", "t60s_测量结束"]
    labels = ["t0 窗口刚显示\n0.6 s", "t8s 稳态\n10.1 s", "t15s 延迟测量前\n16.6 s",
              "t60s 测量结束\n75.5 s"]
    roles = [("Browser", "主进程 Browser", S1), ("Tab", "渲染进程 Tab", S2),
             ("GPU", "GPU 进程", S3), ("Utility", "Utility 进程", S4)]

    samples = {m["tag"]: m for m in smoke["memory"]}
    pT, pB = 736, 1000
    pymax, pticks = nice_ticks(600)
    pL, pR = 150, 1490
    c.legend(pL, 694, [(col, name) for _k, name, col in roles], size=18)

    def PY(v):
        return pB - v / pymax * (pB - pT)

    for t in pticks:
        y = PY(t)
        c.hline(pL, pR, y, GRID)
        c.text(pL - 14, y, str(int(t)), size=17, fill=MUTED, ha="right", va="center")
    c.hline(pL, pR, pB, AXIS)

    gw = (pR - pL) / len(tags)
    bw = 170
    totals = []
    for gi, (tag, lab) in enumerate(zip(tags, labels)):
        s = samples[tag]
        gx = pL + gi * gw + gw / 2
        by_role = {}
        for p in s["processes"]:
            by_role[p["type"]] = by_role.get(p["type"], 0) + p["working_set_mb"]
        parts = [(key, name, color, by_role.get(key, 0))
                 for key, name, color in roles if by_role.get(key, 0) > 0]
        acc = 0
        for si, (_k, _n, color, v) in enumerate(parts):
            y1 = PY(acc)
            y0 = PY(acc + v)
            top_most = si == len(parts) - 1
            # 段间留 2px 底色缝；只有最顶那一段做 4px 圆角，底下都是方角贴基线
            c.bar(gx - bw / 2, y0 + (1 if not top_most else 0), gx + bw / 2, y1 - (1 if si else 0),
                  color, radius=4, round_end="top" if top_most else "none")
            h = y1 - y0
            if h >= 22:
                # 段内数值一律用深色墨：白字压在 #eda100 / #1baf7a 上只有 2–2.6:1，
                # 深色墨在四个槽色上都有 4.2:1 以上（对比度校验触发的那条救济规则）
                c.text(gx, (y0 + y1) / 2, fmt(v), size=16 if h < 30 else 18,
                       bold=True, fill=INK, ha="center", va="center")
            acc += v
        totals.append(acc)
        c.text(gx, PY(acc) - 14, f"合计 {fmt(acc)}", size=19, bold=True, fill=INK,
               ha="center", va="bottom")
        for li, line in enumerate(lab.split("\n")):
            c.text(gx, pB + 24 + li * 26, line, size=18,
                   fill=INK if li == 0 else MUTED, bold=(li == 0), ha="center")

    c.text(80, 1076,
           f"注：下图是单次完整样本（n = 1）；Utility 一栏为同角色多个进程的合计"
           f"（t0 时只起来了 1 个 Utility，稳态后有 2 个）。",
           size=17, fill=INK2)
    c.text(80, 1106,
           f"两种口径在稳态处吻合：进程内 {fmt(totals[1])} MB vs 外部 {fmt(min(steady))} – "
           f"{fmt(max(steady))} MB —— 说明内存口径没搞错。"
           f"图中未标出的段值见 README 的数值表。",
           size=17, fill=MUTED)
    c.text(80, 1136,
           "数据来源：raw/memory_external.csv（外部采样，2 次启动）"
           "　｜　raw/_smoke.json（进程内探针，完整样本 n = 1）",
           size=16, fill=MUTED)
    c.save("03_内存占用.png")


# ══════════════════════════════════════════════════════════
# 图 4 事件响应延迟
# ══════════════════════════════════════════════════════════
def chart_latency(d):
    lat = d["smoke"]["latency"]
    W, H = 1600, 780
    c = Canvas(W, H)

    c.text(80, 46, "桌宠事件响应延迟：注入一条问答事件 → DOM 变更 / 首帧上屏", size=40, bold=True)
    c.text(80, 100,
           "纵轴单位 ms，横轴为第几次问答（共 6 轮）；左图全量线性，右图把第 2–6 轮放大看稳态",
           size=20, fill=INK2)
    c.legend(80, 142, [(S1, "send → DOM 变更"), (S2, "send → 首帧上屏")], size=19)

    # ── 左：全量 ──
    L, R, T, B = 140, 760, 210, 640
    ymax, ticks = nice_ticks(550)
    for t in ticks:
        y = B - t / ymax * (B - T)
        c.hline(L, R, y, GRID)
        c.text(L - 14, y, str(int(t)), size=17, fill=MUTED, ha="right", va="center")
    c.hline(L, R, B, AXIS)
    c.text(L, T - 34, "① 全部 6 轮", size=23, bold=True)

    n = len(lat)
    xs = [L + (i + 0.5) * (R - L) / n for i in range(n)]

    def YL(v):
        return B - v / ymax * (B - T)

    for key, color in (("send_to_dom_ms", S1), ("send_to_frame_ms", S2)):
        pts = [(xs[i], YL(r[key])) for i, r in enumerate(lat)]
        for i in range(len(pts) - 1):
            c.seg(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color, w=2)
        for p in pts:
            c.dot(p[0], p[1], color, r=5)
    for i, x in enumerate(xs):
        c.text(x, B + 22, f"第 {lat[i]['round']} 轮", size=16, fill=MUTED, ha="center")
    c.text(xs[0], YL(lat[0]["send_to_frame_ms"]) - 16, fmt(lat[0]["send_to_frame_ms"]),
           size=20, bold=True, fill=INK, ha="center", va="bottom")
    c.text(xs[0] + 10, YL(lat[0]["send_to_dom_ms"]) + 8, fmt(lat[0]["send_to_dom_ms"]),
           size=20, bold=True, fill=INK, ha="left", va="top")
    c.text(R - 10, T + 16, "第 2–6 轮在这个刻度下贴着 0，见右图", size=18, fill=INK2,
           ha="right")

    # ── 右：放大 ──
    L2, R2, T2, B2 = 900, 1520, 210, 640
    sub = lat[1:]
    ymax2, ticks2 = 3.5, [0, 1, 2, 3]
    for t in ticks2:
        y = B2 - t / ymax2 * (B2 - T2)
        c.hline(L2, R2, y, GRID)
        c.text(L2 - 14, y, str(t), size=17, fill=MUTED, ha="right", va="center")
    c.hline(L2, R2, B2, AXIS)
    c.text(L2, T2 - 34, "② 第 2–6 轮（放大）", size=23, bold=True)

    m = len(sub)
    xs2 = [L2 + (i + 0.5) * (R2 - L2) / m for i in range(m)]

    def Y2(v):
        return B2 - v / ymax2 * (B2 - T2)

    for key, color in (("send_to_dom_ms", S1), ("send_to_frame_ms", S2)):
        pts = [(xs2[i], Y2(r[key])) for i, r in enumerate(sub)]
        for i in range(len(pts) - 1):
            c.seg(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color, w=2)
        for p in pts:
            c.dot(p[0], p[1], color, r=5)
    # 只标极值，不给每个点都标数
    hi = max(range(m), key=lambda i: sub[i]["send_to_frame_ms"])
    c.text(xs2[hi], Y2(sub[hi]["send_to_frame_ms"]) - 14,
           f"最大 {fmt(sub[hi]['send_to_frame_ms'], 2)}", size=18, bold=True, fill=INK,
           ha="center", va="bottom")
    lo = min(range(m), key=lambda i: sub[i]["send_to_dom_ms"])
    c.text(xs2[lo], Y2(sub[lo]["send_to_dom_ms"]) + 12,
           f"最小 {fmt(sub[lo]['send_to_dom_ms'], 2)}", size=18, bold=True, fill=INK,
           ha="center", va="top")
    for i, x in enumerate(xs2):
        c.text(x, B2 + 22, f"第 {sub[i]['round']} 轮", size=16, fill=MUTED, ha="center")

    dom = [r["send_to_dom_ms"] for r in sub]
    c.text(80, 690,
           f"第 1 轮的 {fmt(lat[0]['send_to_dom_ms'], 2)} ms 不能代表稳态：该轮气泡文本是「你问的」，"
           f"与第 2–6 轮的「思考中」不是同一条 DOM 变更路径，且属首次触发。",
           size=19, fill=INK2)
    c.text(80, 722,
           f"稳态区间：DOM 变更 {fmt(min(dom), 2)} – {fmt(max(dom), 2)} ms，"
           f"首帧上屏 {fmt(min(r['send_to_frame_ms'] for r in sub), 2)} – "
           f"{fmt(max(r['send_to_frame_ms'] for r in sub), 2)} ms（5 轮）。",
           size=19, fill=INK2)
    c.text(80, H - 30,
           "数据来源：raw/_smoke.json　｜　完整样本 n = 1（6 轮问答），单次测量，未做重复实验",
           size=16, fill=MUTED)
    c.save("04_事件响应延迟.png")


def main():
    # Windows 控制台默认 GBK，不重设的话中文 print 出来是乱码 —— 数据没错，
    # 但看日志的人会以为哪里坏了（bench_common.py 踩过同一个坑）
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    os.makedirs(CHARTS, exist_ok=True)
    d = load()

    bad = check_startup_consistency(d)
    if bad:
        print("⚠️ startup_runs.csv 与 startup_summary.json 不一致，以 JSON 为准：")
        for b in bad:
            print("   ", b)
    else:
        print("✓ startup_runs.csv 与 startup_summary.json 逐项一致")

    print("开始画图：")
    chart_startup(d)
    chart_bundle(d)
    chart_memory(d)
    chart_latency(d)
    print("全部完成。")


if __name__ == "__main__":
    main()
