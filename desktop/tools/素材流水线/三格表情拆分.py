# -*- coding: utf-8 -*-
"""三格表情图 → hint / correct / quiz 三张单独的立绘。

═══ 为什么要拆 ═══

组长给的是一张图里**并排三个表情**的参考图，每格还带两样"排版标注"：
  · 底部的英文标签牌（Hint / Correct / Quiz 胶囊）+ 两侧小装饰
  · 头旁边的语义气泡（灯泡 / 对勾 / 问号）

直接塞进流水线的话，桌宠脚下会顶着一个英文牌子、旁边飘着气泡，
而且气泡位置是**左 / 左 / 右**，归一化按头心对齐后三个表情会横向错位。

═══ 判据（别用肉眼 —— 上一轮"指标选错得出相反结论"的教训）═══

裁完的角色框**宽高比应接近 0.77**（站姿基准 blink_0 是 0.769）。
气泡只要残留一点，宽高比就会往上跳。脚本会把这个数打出来，
并落一张调试图 —— **必须看一眼再往下走**。

═══ 两处气泡的连法不一样，所以用了两种办法 ═══

1. **Quiz 的问号气泡是独立连通域** —— 直接按"只留含鞋底的那一块"就能滤掉。

2. **Hint 的灯泡 / Correct 的对勾是【贴着头发】的**（不是拖一条细颈，
   实测腐蚀到 14px 都断不开），所以连通域分不了，得先按矩形抹掉。
   ⚠️ 这里**不能**用"腐蚀后再按原掩膜重建"（`binary_propagation`）——
      重建会顺着连接处原路长回气泡里去，等于白做。
   ⚠️ `BUBBLE_BOX` 那几个数是**放大 4 倍、打 20px 网格**逐个量出来的
      （调试图见 `动作帧/预览/_诊断_*_气泡区.png`），**换图必须重新量**。
      取值原则是【只到气泡外缘、不越界进头发】。

═══ 用法 ═══

在 `_素材工作区/` 下运行（那儿才有 `动作帧/`）：

    python 三格表情拆分.py                用默认图
    python 三格表情拆分.py --图 <路径>

产出：`动作帧/原始/表情/{hint,correct,quiz}.png` + `动作帧/预览/_三格拆分_检测.png`
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ALPHA_TH = 8

#: 三格的分栏（原图坐标）。由"整列都是背景"的空列段取中点得到 ——
#: 实测空列段是 x 624~679 与 x 1282~1392，取中点 651 / 1337。换图要重新量。
PANELS = [("hint", 0, 651), ("correct", 651, 1337), ("quiz", 1337, None)]

#: 每格要抹掉的气泡区域（**面板内**坐标 x0,y0,x1,y1）。None = 不用抹（独立连通域）。
#: 量法见模块文档；右边界**故意卡在气泡外缘**，宁可留一两个像素的尾巴残片
#: （残片会变成独立小块，被连通域那步顺手滤掉），也不要啃到头发。
BUBBLE_BOX = {
    "hint": (0, 200, 167, 345),      # 灯泡气泡：x 25~166, y 208~338
    "correct": (0, 183, 141, 307),   # 对勾气泡：x 20~140, y 190~305（含右下的尾巴）
    "quiz": None                     # 问号气泡：独立连通域，不用抹
}

#: 角色站姿的轮廓宽高比基准（= 现在用的 blink_0）。明显超过就说明气泡没裁干净。
REF_ASPECT = 0.769
ASPECT_WARN = 0.92

DEFAULT_IMG = os.path.join(
    "_新角色_官方设定", "_第四批_20260920", "01_三格表情HintCorrectQuiz_5da1973d.png"
)
RAW_OUT = os.path.join("动作帧", "原始", "表情")
PREVIEW_DIR = os.path.join("动作帧", "预览")


def mask_of(img):
    return np.array(img.convert("RGBA"))[:, :, 3] > ALPHA_TH


def bbox(m):
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def find_label_cut(m):
    """找"角色脚底"与"底部标签牌"之间的那条空行带，返回该切在哪。

    ⚠️ 不能用写死的 y —— 换图就废。做法是从下往上找**第一段"下面有前景、
    上面也有前景"的空行带**：图片下边缘的留白下面没有东西，要跳过；
    再往上那段才是角色与标签牌之间的缝。
    """
    rows = m.sum(axis=1)
    h = len(rows)
    runs = []
    y = 0
    while y < h:
        if rows[y] == 0:
            y0 = y
            while y < h and rows[y] == 0:
                y += 1
            runs.append((y0, y))
        else:
            y += 1

    for y0, y1 in reversed(runs):
        if m[y1:].any() and m[:y0].any():
            return y0
    return h


def bubble_tint_mask(rgb):
    """气泡专属的**绿 / 青绿**像素。**角色配色是白 / 蓝 / 粉，没有绿**，
    所以这一族颜色一定是 Correct 那格的绿色气泡 / 星星装饰，可以放心抹。

    ⚠️ 为什么单靠矩形抹不干净：绿色圆圈的**右缘描边**落在矩形外面
    （实测 x 141~148），而且那一小条**和头发是连着的** ——
    连通域那步也滤不掉它（它已经在"含鞋底的那一块"里了）。两个办法都漏，所以按颜色收尾。

    ⚠️ 判据为什么是 `b - g` 而不是"g 是不是最大通道"（这里返工过一次）：
    圆圈描边的**抗锯齿外缘是青绿色（teal）**，实测 `rgb=(112,174,189)` ——
    `b` 和 `g` 差不多，所以 `g > b` 这类判据**恰好把蓝白色的角色放过去、把它也放过去**，
    于是"绿色已经清零"的计数是对的、肉眼却照样看得见一道青绿月牙。
    换个判据就分开了：
      · 角色的蓝（丝带）  `( 90,163,237)` → `b-g = 74`，蓝远压过绿
      · 气泡的描边边    `(112,174,189)` → `b-g = 15`，绿蓝相当
    所以用 `g 明显高于 r`（是绿系不是蓝系）**且** `b 不比 g 高多少`（是青绿不是纯蓝）。

    ⚠️ 调用处**只在气泡周边那一圈**应用，不全身撒网 —— 换了有绿色的角色也不会误伤。
    """
    r, g, b = rgb[:, :, 0].astype(int), rgb[:, :, 1].astype(int), rgb[:, :, 2].astype(int)
    return (g > r + 15) & (b - g < 40)


def keep_body(m):
    """只保留"含鞋底那一块"的连通域。

    ⚠️ 用**最下面那个前景像素**当锚，而不是"最大的块" ——
    最大的块未必是角色（气泡偶尔比瘦小的角色还大），但鞋底一定是角色。
    """
    lab, n = ndimage.label(m)
    if n == 0:
        return None
    ys, xs = np.nonzero(m)
    i = int(np.argmax(ys))          # 最靠下的前景像素
    return lab == lab[ys[i], xs[i]]


def main():
    ap = argparse.ArgumentParser(description="三格表情图拆分（去标签牌 + 去气泡）")
    ap.add_argument("--图", dest="img", default=DEFAULT_IMG, help="三格原图。默认 %s" % DEFAULT_IMG)
    args = ap.parse_args()

    if not os.path.exists(args.img):
        print("找不到原图：%s" % args.img, file=sys.stderr)
        print("（这个脚本要在 `_素材工作区/` 下跑，那儿才有 动作帧/ 和 _新角色_官方设定/）",
              file=sys.stderr)
        return 1

    src = Image.open(args.img).convert("RGBA")
    print("原图 %s  %dx%d" % (args.img, src.width, src.height))

    results = {}
    for name, x0, x1 in PANELS:
        panel = src.crop((x0, 0, x1 if x1 is not None else src.width, src.height))
        m = mask_of(panel)

        cut = find_label_cut(m)
        print("\n[%s] 面板 %dx%d；标签牌切在 y=%d（脚底 %d 之上）"
              % (name, panel.width, panel.height, cut, cut))
        panel = panel.crop((0, 0, panel.width, cut))
        m = mask_of(panel)

        lab0, n0 = ndimage.label(m)
        sizes0 = np.bincount(lab0.ravel())
        sizes0[0] = 0
        big0 = [i for i in range(1, n0 + 1) if sizes0[i] > m.sum() * 0.01]
        print("      裁掉标签牌后：连通域 %d 个，>1%% 的有 %d 个" % (n0, len(big0)))

        box = BUBBLE_BOX.get(name)
        if box:
            bx0, by0, bx1, by1 = box
            bx1 = min(bx1, panel.width)
            by1 = min(by1, panel.height)
            before = int(m[by0:by1, bx0:bx1].sum())
            m = m.copy()
            m[by0:by1, bx0:bx1] = False
            print("      抹掉气泡区 x[%d,%d] y[%d,%d] → 去掉 %d 像素" % (bx0, bx1, by0, by1, before))
        else:
            print("      这张不用抹气泡（独立连通域）")

        body = keep_body(m)
        if body is None or not body.any():
            print("      ✗ 什么都没剩下，跳过", file=sys.stderr)
            continue

        # 按颜色收尾：抹掉气泡右缘外那一小条绿/青绿描边。
        #
        # ⚠️ 作用范围**必须卡紧**（右缘外 12px、上下各 12px）。第一版放到 +25，
        #    结果把角色身上**花边的暗部**也当成了青绿抹掉 —— 放大看就是几个透明小洞。
        #    青弧最远到气泡右缘外 7px，12px 够用，而花边在更右边，天然被排除。
        if box:
            gy0 = max(0, by0 - 12)
            gy1 = min(panel.height, by1 + 12)
            gx1 = min(panel.width, bx1 + 12)
            gr = bubble_tint_mask(np.array(panel))[gy0:gy1, 0:gx1]
            hit = body[gy0:gy1, 0:gx1] & gr
            n_tint = int(hit.sum())
            if n_tint:
                yy, xx = np.nonzero(hit)
                print("      抹掉气泡右缘残留的青绿描边 %d 像素（x[%d,%d] y[%d,%d]）"
                      % (n_tint, xx.min(), xx.max(), gy0 + yy.min(), gy0 + yy.max()))
                body[gy0:gy1, 0:gx1] &= ~gr
            else:
                print("      气泡右缘没有青绿残留")

        bb = bbox(body)
        asp = (bb[2] - bb[0]) / float(bb[3] - bb[1])
        print("      → 角色框 %dx%d，宽高比 %.3f（站姿基准 %.3f）%s"
              % (bb[2] - bb[0], bb[3] - bb[1], asp, REF_ASPECT,
                 "" if asp < ASPECT_WARN else "  ⚠️ 偏宽，气泡可能没裁干净 —— 看调试图"))

        arr = np.array(panel).copy()
        arr[~body] = (0, 0, 0, 0)
        out = Image.fromarray(arr, "RGBA")
        pad = 20
        out = out.crop((max(0, bb[0] - pad), max(0, bb[1] - pad),
                        min(out.width, bb[2] + pad), min(out.height, bb[3] + pad)))
        results[name] = out

    if not results:
        return 1

    os.makedirs(RAW_OUT, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    for name, out in results.items():
        dst = os.path.join(RAW_OUT, "%s.png" % name)
        out.save(dst)
        print("\n✅ %-8s → %s  (%dx%d)" % (name, dst, out.width, out.height))

    # ---- 调试图：透明处铺淡红，看得见边界 ----
    tiles = []
    for name, out in results.items():
        a = np.array(out)
        vis = a[:, :, :3].copy()
        vis[a[:, :, 3] <= ALPHA_TH] = (255, 215, 215)
        im = Image.fromarray(vis, "RGB")
        bg = Image.new("RGB", (im.width, im.height + 28), (255, 255, 255))
        bg.paste(im, (0, 28))
        ImageDraw.Draw(bg).text((6, 8), "%s  %dx%d" % (name, out.width, out.height), fill=(0, 0, 0))
        tiles.append(bg)

    H = max(t.height for t in tiles)
    W = sum(t.width for t in tiles) + 14 * (len(tiles) - 1)
    board = Image.new("RGB", (W, H), (255, 255, 255))
    x = 0
    for t in tiles:
        board.paste(t, (x, 0))
        x += t.width + 14
    dst = os.path.join(PREVIEW_DIR, "_三格拆分_检测.png")
    board.save(dst)
    print("\n✅ 调试图 → %s" % dst)
    print("   **看一眼：脚下不该有英文牌、头旁不该有气泡、头发不该被啃掉一块**")
    print("\n接着把这些丢进流水线：")
    print("  python 归一化帧.py 表情     （源目录就是 %s）" % RAW_OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
