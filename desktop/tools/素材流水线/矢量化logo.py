# -*- coding: utf-8 -*-
"""把 logo 位图描成矢量 SVG —— 纯 numpy + PIL，不依赖 cv2 / potrace / skimage。

════════════════════════════════════════════════════════════════════
为什么要有这个脚本
════════════════════════════════════════════════════════════════════
项目 logo（鲸鱼 + 书）**只有位图**，没有矢量源：`media/DeepProf.jpg` 实际是
PNG 数据挂了个 .jpg 后缀，1254×1254、零元数据，是 AI 出图/在线工具导出的产物。
后果有两个：

1. 应用图标在 16px 那档糊 —— 细缝被像素网格吃掉（见 生成应用图标.py 头部）。
2. 以后要印物料、做门户、放到任意尺寸都会糊。

有了矢量，第 2 个问题直接解决；第 1 个问题也有了正经解法：
**按轮廓面积筛**，小尺寸只保留鲸鱼外轮廓，主动丢掉书页细碎轮廓
（试过在位图上直接加粗，会把负空间填死、变成一坨，见脚本尾部「试过的死路」）。

════════════════════════════════════════════════════════════════════
怎么描的
════════════════════════════════════════════════════════════════════
marching squares 取等值线 → 把线段接成闭环 → Douglas-Peucker 抽稀 → 合成 SVG。

几个关键点：
1. **用偶数倍坐标**（点坐标 ×2）。marching squares 的等值点落在像素**中点**，
   是半整数；乘 2 之后全是整数，接环时可以直接用元组当字典键，
   不用处理浮点比较。
2. **孔洞不用特殊处理**。书页、眼白是鲸鱼内部的白色区域，marching squares
   会自然产出反向的闭环；SVG 用 `fill-rule="evenodd"` 就自动挖空，
   不需要判断谁套着谁。
3. **抽稀容差按母图尺寸给**（默认 1.0px）。太大形状会走样，太小 SVG 巨大
   （1.0px → 244 个顶点；0px → 2922 个，IoU 只差 0.05%）。
   脚本末尾会用 **IoU 自校验**：把 SVG 的多边形按 even-odd 栅格化回位图，
   与原始掩码比交并比，低于阈值就报错退出。
   ⚠️ 这个 IoU 的**上限约 0.991**，到不了 1.0（半像素边界歧义，见 IOU_MIN 注释）。
4. **只输出两个颜色**（蓝 + 白底）。logo 本身就是双色，不做多色量化。

跑法：
    python desktop/tools/素材流水线/矢量化logo.py
产物：
    media/DeepProf-logo.svg         ← 矢量源（双色，evenodd 挖孔）
    同时打印 IoU 与路径数量，供验收
"""

import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(os.path.dirname(HERE))  # …/desktop
ROOT = os.path.dirname(DESKTOP)  # 仓库根

SRC = os.path.join(ROOT, "media", "DeepProf.jpg")
OUT_SVG = os.path.join(ROOT, "media", "DeepProf-logo.svg")

BLUE = (22, 90, 238)
BG = (255, 255, 255)
BG_TOL = 40  # 与背景色的最大通道差超过它才算 logo 像素
EPSILON = 1.0  # Douglas-Peucker 容差（母图像素）
MIN_AREA = 8.0  # 小于这个面积的轮廓直接丢（噪点）
#: 自校验阈值。**上限大约是 0.991，不是 1.0** —— 实测：容差给到 0（完全不抽稀、
#: 2922 个顶点）时 IoU 也只有 0.9907。残差来自 marching squares 在像素网格上的
#: **半像素边界歧义**（边界上「多出 1586 / 少了 1313」沿周长对冲，约 0.94%），
#: 是这类像素级描线的固有精度，不是接环写错了。
#: 容差 1.0px 的 IoU 是 0.9902，与 0 容差只差 0.05%，但顶点数从 2922 降到 244。
IOU_MIN = 0.99


def blue_mask(src, tol=BG_TOL, bg=BG):
    """logo 像素掩码：偏离背景色超过 tol 的算前景。"""
    a = np.array(src.convert("RGB")).astype(int)
    return np.abs(a - np.array(bg)).max(axis=2) > tol


# ══════════════════════════════════════════════════════════════════
# marching squares：从二值掩码取等值线段，再接成闭环
# ══════════════════════════════════════════════════════════════════
def _segments(mask):
    """返回线段列表，端点是 2 倍坐标（整数）。"""
    m = mask.astype(np.uint8)
    # 每个 2×2 单元的四角：左上 a、右上 b、右下 c、左下 d
    a = m[:-1, :-1]
    b = m[:-1, 1:]
    c = m[1:, 1:]
    d = m[1:, :-1]
    case = (a << 3) | (b << 2) | (c << 1) | d

    ys, xs = np.where((case != 0) & (case != 15))
    segs = []
    for y, x in zip(ys.tolist(), xs.tolist()):
        s = int(case[y, x])
        # 四条边的中点，2 倍坐标
        top = (2 * y, 2 * x + 1)
        right = (2 * y + 1, 2 * x + 2)
        bottom = (2 * y + 2, 2 * x + 1)
        left = (2 * y + 1, 2 * x)
        if s in (1, 14):
            segs.append((left, bottom))
        elif s in (2, 13):
            segs.append((bottom, right))
        elif s in (3, 12):
            segs.append((left, right))
        elif s in (4, 11):
            segs.append((top, right))
        elif s in (6, 9):
            segs.append((top, bottom))
        elif s in (7, 8):
            segs.append((left, top))
        elif s in (5, 10):
            # 鞍点：两条互不相连的线段。方向选择影响极小（对双色块状图）
            segs.append((left, top))
            segs.append((bottom, right))
    return segs


def _loops(segs):
    """把线段接成闭环。每个端点最多连两条线段。"""
    adj = defaultdict(list)
    for i, (p, q) in enumerate(segs):
        adj[p].append((q, i))
        adj[q].append((p, i))
    used = [False] * len(segs)
    out = []
    for i0 in range(len(segs)):
        if used[i0]:
            continue
        p, q = segs[i0]
        used[i0] = True
        loop = [p, q]
        cur, prev = q, p
        while True:
            nxt = None
            for nb, idx in adj[cur]:
                if not used[idx] and nb != prev:
                    nxt = (nb, idx)
                    break
            if nxt is None:
                # 回到起点就闭合；否则是断头（理论上不该发生）
                for nb, idx in adj[cur]:
                    if not used[idx]:
                        nxt = (nb, idx)
                        break
                if nxt is None:
                    break
            nb, idx = nxt
            used[idx] = True
            if nb == loop[0]:
                break
            loop.append(nb)
            prev, cur = cur, nb
        if len(loop) >= 4:
            out.append(loop)
    return out


def _simplify(points, eps):
    """Douglas-Peucker 抽稀（迭代版，避免深递归爆栈）。"""
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n < 3:
        return points
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        p, q = pts[i], pts[j]
        d = q - p
        norm = np.hypot(*d)
        seg = pts[i + 1 : j]
        if norm == 0:
            dist = np.hypot(*(seg - p).T)
        else:
            v = seg - p
            # 二维叉积手算：numpy 2.x 起 np.cross 不再接受二维向量
            dist = np.abs(d[0] * v[:, 1] - d[1] * v[:, 0]) / norm
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return [points[i] for i in range(n) if keep[i]]


def _poly_area(poly):
    """鞋带公式，取绝对值（不关心绕向）。"""
    pts = np.asarray(poly, dtype=float)
    x, y = pts[:, 1], pts[:, 0]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0


def trace(src, eps=EPSILON, min_area=MIN_AREA):
    """位图 → 轮廓列表（每个是 (x, y) 像素坐标的闭环，已抽稀）。"""
    mask = blue_mask(src)
    segs = _segments(mask)
    polys = []
    for loop in _loops(segs):
        # 2 倍坐标 → 像素坐标
        pts = [(px / 2.0, py / 2.0) for py, px in loop]
        if _poly_area(pts) < min_area:
            continue
        polys.append(_simplify(pts, eps))
    polys.sort(key=_poly_area, reverse=True)
    return polys, mask


def rasterize(polys, shape):
    """把多边形栅格化回掩码，用来算 IoU 自校验。

    ⚠️ 必须逐条 XOR，不能用 ImageDraw.polygon 一次填完 ——
       polygon() 是**实心**填充，会把书页/眼白这些孔洞也填成蓝色，
       于是 IoU 只有 0.74（踩过）。逐条 XOR 得到的正是 even-odd 的语义，
       与 SVG 的 fill-rule="evenodd" 一致。
    """
    h, w = shape
    acc = np.zeros((h, w), bool)
    for poly in polys:
        img = Image.new("1", (w, h), 0)
        ImageDraw.Draw(img).polygon(poly, fill=1)
        acc ^= np.array(img, dtype=bool)
    return acc


def to_svg(polys, w, h):
    """合成双色 SVG：白底 + 蓝色 evenodd 路径（孔洞自动挖空）。"""
    def fmt(v):
        return ("%.1f" % v).rstrip("0").rstrip(".")

    parts = []
    for poly in polys:
        pts = list(poly)
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        parts.append("M" + " ".join("%s %s" % (fmt(x), fmt(y)) for x, y in pts) + "Z")
    d = "".join(parts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">\n'
        '  <title>DeepProf logo（鲸鱼 + 书）</title>\n'
        "  <!-- 由 desktop/tools/素材流水线/矢量化logo.py 从 media/DeepProf.jpg 自动描出，别手改 -->\n"
        '  <rect width="%d" height="%d" fill="#%02x%02x%02x"/>\n'
        '  <path fill="#%02x%02x%02x" fill-rule="evenodd" d="%s"/>\n'
        "</svg>\n"
    ) % (w, h, w, h, w, h, *BG, *BLUE, d)


def main():
    if not os.path.exists(SRC):
        print("❌ 找不到源图：%s" % SRC)
        return 1

    src = Image.open(SRC)
    w, h = src.size
    print("源图 %s  %dx%d（实际格式 %s）" % (os.path.basename(SRC), w, h, src.format))

    polys, mask = trace(src)
    print("轮廓 %d 条（已抽稀，容差 %.1fpx）" % (len(polys), EPSILON))
    for i, p in enumerate(polys[:6]):
        print("   #%d 面积 %.0f  顶点 %d" % (i + 1, _poly_area(p), len(p)))

    back = rasterize(polys, mask.shape)
    inter = np.logical_and(back, mask).sum()
    union = np.logical_or(back, mask).sum()
    iou = inter / union if union else 0.0
    print("自校验 IoU = %.5f（阈值 %.3f）" % (iou, IOU_MIN))
    if iou < IOU_MIN:
        print("❌ 还原度不足，说明描错了（多半是接环或鞍点方向有问题），未写出 SVG")
        return 1

    svg = to_svg(polys, w, h)
    with open(OUT_SVG, "w", encoding="utf-8") as f:
        f.write(svg)
    print("\n✅ %s（%.1f KB）" % (OUT_SVG, len(svg.encode("utf-8")) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ══════════════════════════════════════════════════════════════════
# 试过的死路（别再走一遍）
# ══════════════════════════════════════════════════════════════════
# 1. **在位图上把小尺寸加粗**（向外长 1~2px）—— 更糟。
#    16px 下书页的白色缝只有 1~2px，向外长 1px 就把缝全闭合、尾鳍与身体的分界
#    也没了，直接变成实心蓝块。鲸鱼 logo 靠**白色负空间**表达细节，
#    加粗等于把负空间填掉。这和「给单色角色脸描边」是两回事。
# 2. **收留白**（6% → 2% → 0）—— 16px 三档都还是糊，形状没救回来；
#    pad=0 反而让大尺寸顶到画布边缘。
