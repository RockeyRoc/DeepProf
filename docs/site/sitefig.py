"""DeepProf 站点配图 —— 共用视觉系统与绘图基元。

与计划书 v1.0 的 brand.py 同源取色，保证网站与计划书观感一致。
坐标系统一：xlim 0..100，ylim 0..H，1 单位 = 画布宽度/100。
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path as MPath

# ------------------------------------------------------------------ 字体
FONT_DIR = Path(os.environ.get("DP_FONT_DIR", Path(__file__).resolve().parent / "fonts"))
if FONT_DIR.exists():
    for f in FONT_DIR.glob("*.otf"):
        fm.fontManager.addfont(str(f))

SANS = ["Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "Noto Sans SC", "DejaVu Sans"]
SERIF = ["Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "SimSun", "DejaVu Serif"]

# ------------------------------------------------------------------ 色板
BRAND = "#0765FC"
NAVY_900 = "#03102B"
NAVY_800 = "#04173F"
NAVY_700 = "#062A63"
BLUE_500 = "#2E7BFF"
BLUE_300 = "#7FB2FF"
BLUE_200 = "#B7D3FF"
BLUE_100 = "#D6E6FF"
BLUE_050 = "#EEF5FF"
INK = "#0A1A33"
INK_2 = "#3D4F6B"
MUTED = "#7688A3"
RULE = "#D8E0EC"
PAPER = "#FFFFFF"
PAPER_2 = "#F6F9FE"
POS = "#12A150"
NEG = "#E5484D"
WARN = "#F2A33C"
ACCENT = "#FF6B4A"
NEUTRAL = "#94A3B8"

DPI = 168


# ------------------------------------------------------------------ 基元
def new_canvas(w_in: float, h_in: float, h_units: float | None = None):
    """建画布，返回 (fig, ax)。x 轴 0..100，y 轴 0..h_units。"""
    # 全局回退：任何漏写 family 的文本也不会掉到无中文字形的 DejaVu
    plt.rcParams["font.family"] = SANS
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(w_in, h_in), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    hu = h_units if h_units is not None else h_in / w_in * 100.0
    ax.set_xlim(0, 100)
    ax.set_ylim(0, hu)
    ax.set_axis_off()
    ax.set_facecolor(PAPER)
    fig.patch.set_facecolor(PAPER)
    return fig, ax, hu


def box(ax, x, y, w, h, title, sub=None, *, fill=PAPER, edge=INK_2, tcol=INK,
        scol=MUTED, fs=11.0, subfs=10.0, lw=1.3, r=0.9, tcaps=None, subwrap=None):
    """圆角节点盒。x,y 为左下角。返回 (cx, cy, top, bottom)。"""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                linewidth=lw, edgecolor=edge, facecolor=fill, zorder=3))
    cx, cy = x + w / 2, y + h / 2
    if sub:
        ax.text(cx, cy + h * 0.16, title, ha="center", va="center", fontsize=fs,
                color=tcol, family=SANS, zorder=4, fontweight="bold")
        subs = subwrap or [sub]
        for i, line in enumerate(subs):
            ax.text(cx, cy - h * 0.16 - i * (h * 0.24), line, ha="center", va="center",
                    fontsize=subfs, color=scol, family=SANS, zorder=4)
    else:
        ax.text(cx, cy, title, ha="center", va="center", fontsize=fs,
                color=tcol, family=SANS, zorder=4, fontweight="bold")
    return cx, cy, y + h, y


def caption(ax, x, y, text, *, fs=10.0, color=MUTED, ha="left", va="center"):
    ax.text(x, y, text, ha=ha, va=va, fontsize=fs, color=color, family=SANS, zorder=4)


def eyebrow(ax, x, y, text, *, fs=9.5, color=MUTED, ha="left"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=color, family=SANS,
            zorder=4, letter_spacing="0.08em" if False else None)


def elbow(ax, p0, p1, *, color=MUTED, lw=1.2, arrow=True, r=0.9, ls="-",
          zorder=2, mid=None):
    """正交折线（圆角拐弯）。p0=(x0,y0) -> p1=(x1,y1)。
    mid 为可选 (轴, 值)：给出中间转折所在的 x(轴='x') 或 y(轴='y')。"""
    (x0, y0), (x1, y1) = p0, p1
    if mid is not None:
        axis, v = mid
    else:
        axis, v = ("x", (x0 + x1) / 2)

    if axis == "x":
        pts = [(x0, y0), (v, y0), (v, y1), (x1, y1)]
    else:
        pts = [(x0, y0), (x0, v), (x1, v), (x1, y1)]

    pts = [p for i, p in enumerate(pts) if i == 0 or abs(p[0] - pts[i - 1][0]) > 1e-9
           or abs(p[1] - pts[i - 1][1]) > 1e-9]
    if len(pts) < 2:
        return

    verts, codes = [pts[0]], [MPath.MOVETO]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        d1 = (b[0] - a[0], b[1] - a[1])
        d2 = (c[0] - b[0], c[1] - b[1])
        l1 = max(abs(d1[0]), abs(d1[1]))
        l2 = max(abs(d2[0]), abs(d2[1]))
        rr = min(r, l1 / 2, l2 / 2)
        u1 = (d1[0] / l1, d1[1] / l1)
        u2 = (d2[0] / l2, d2[1] / l2)
        p_in = (b[0] - u1[0] * rr, b[1] - u1[1] * rr)
        p_out = (b[0] + u2[0] * rr, b[1] + u2[1] * rr)
        verts += [p_in, b, p_out]
        codes += [MPath.LINETO, MPath.CURVE3, MPath.CURVE3]
    verts.append(pts[-1])
    codes.append(MPath.LINETO)

    ax.add_patch(PathPatch(MPath(verts, codes), fill=False, edgecolor=color,
                           linewidth=lw, linestyle=ls, zorder=zorder,
                           capstyle="round", joinstyle="round"))
    if arrow:
        _head(ax, pts[-1], pts[-2], color=color, zorder=zorder + 1)


def _head(ax, tip, prev, *, color, size=1.0, zorder=3):
    dx, dy = tip[0] - prev[0], tip[1] - prev[1]
    n = (dx * dx + dy * dy) ** 0.5
    if n < 1e-9:
        return
    ux, uy = dx / n, dy / n
    L, W = 1.5 * size, 0.62 * size
    bx, by = tip[0] - ux * L, tip[1] - uy * L
    px, py = -uy, ux
    ax.add_patch(plt.Polygon([(tip[0], tip[1]),
                              (bx + px * W, by + py * W),
                              (bx - px * W, by - py * W)],
                             closed=True, facecolor=color, edgecolor="none", zorder=zorder))


def edge_label(ax, x, y, text, *, fs=9.5, color=MUTED, bg=PAPER, ha="center"):
    """连线上的标签，带不透明遮罩，遮罩与连线之间保持可见间隙。"""
    if not text:
        return
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=color,
            family=SANS, zorder=6,
            bbox=dict(boxstyle="square,pad=0.22", facecolor=bg, edgecolor="none"))


def zone(ax, x, y, w, h, label, *, edge=RULE, fill="none", lcol=MUTED, fs=10.0, r=1.2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                linewidth=1.0, edgecolor=edge, facecolor=fill,
                                linestyle=(0, (5, 4)), zorder=1))
    ax.text(x + 1.4, y + h - 1.0, label, ha="left", va="top", fontsize=fs,
            color=lcol, family=SANS, zorder=2)


def save(fig, out_base: Path, *, png=True, svg=True, pad=0.0):
    out_base.parent.mkdir(parents=True, exist_ok=True)
    made = []
    if png:
        fig.savefig(out_base.with_suffix(".png"), dpi=DPI, facecolor=PAPER,
                    bbox_inches=None, pad_inches=0)
        made.append(str(out_base.with_suffix(".png")))
    if svg:
        fig.savefig(out_base.with_suffix(".svg"), format="svg", facecolor=PAPER,
                    bbox_inches=None, pad_inches=0)
        made.append(str(out_base.with_suffix(".svg")))
    plt.close(fig)
    return made


# ------------------------------------------------------ 真实字体度量与折行
def measure(ax, s, fs, weight="normal"):
    """返回字符串在坐标轴单位下的宽度（用渲染器实测，不做估算）。"""
    fig = ax.figure
    try:
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
    except Exception:
        return len(s) * 0.7
    t = ax.text(0, 0, s, fontsize=fs, family=SANS, fontweight=weight)
    bb = t.get_window_extent(renderer=r)
    t.remove()
    inv = ax.transData.inverted()
    (x0, _), (x1, _) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
    return abs(x1 - x0)


def wrap_to(ax, text, max_units, fs, weight="normal", max_lines=None):
    """按实测宽度折行；超出 max_lines 时做硬截断（返回的末行不加省略号，由调用方决定）。"""
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if measure(ax, cand, fs, weight) <= max_units or not cur:
            # 单词本身超宽（中文长串）→ 逐字断
            if not cur and measure(ax, w, fs, weight) > max_units:
                piece = ""
                for ch in w:
                    if measure(ax, piece + ch, fs, weight) > max_units and piece:
                        lines.append(piece)
                        piece = ch
                    else:
                        piece += ch
                cur = piece
                continue
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


def para(ax, x, y, text, *, width, fs, color, lh, va="top", weight="normal"):
    """按实测宽度排一段文字，返回末行基线。"""
    lines = wrap_to(ax, text, width, fs, weight)
    for i, line in enumerate(lines):
        ax.text(x, y - i * lh, line, fontsize=fs, color=color, family=SANS,
                ha="left", va=va if i == 0 else "top", zorder=4, fontweight=weight)
    return y - len(lines) * lh

