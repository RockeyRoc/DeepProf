# -*- coding: utf-8 -*-
"""把「四格连续动作图」自动切成 4 张单帧。

═══ 为什么推荐一次出四格，而不是逐张出 ═══

AI 逐张生成时，四张之间的角色大小、比例、画风都会飘。
同一张画布内生成的四个姿势，比例和步态连续性是**天然对齐**的 ——
这是参考项目 dsh-dfy 的 SOURCE.md 记下来的做法，我们照搬。

但切完的四格仍然是"四个等距排列的小人"，位置和大小还是对不齐，
所以切完**仍然要跑归一化**。

═══ 用法 ═══

    python 裁四格.py walk           # 切开 原始/walk/ 里唯一那张四格图
    python 裁四格.py walk --n 2     # 两格图

切开后原图会被移到 原始/walk/_原图备份/，避免下次重复切。
"""

import argparse
import os
import shutil
import sys

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from 归一化帧 import IMG_EXT, natural_key, strip_background  # noqa: E402

RAW_DIR = os.path.join(HERE, "动作帧", "原始")

ALPHA_TH = 8
MIN_RUN_W = 20   # 窄于这个宽度的"内容列"当成噪点丢掉


def content_runs(mask):
    """找出「有内容的列」的连续区间"""
    cols = mask.any(axis=0)
    runs, start = [], None
    for i, v in enumerate(cols):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= MIN_RUN_W:
                runs.append((start, i))
            start = None
    if start is not None and len(cols) - start >= MIN_RUN_W:
        runs.append((start, len(cols)))
    return runs


def main():
    ap = argparse.ArgumentParser(description="四格连续动作图 → 单帧")
    ap.add_argument("action", help="动作名，例如 walk")
    ap.add_argument("--n", type=int, default=4, help="格数，默认 4")
    ap.add_argument("--格", dest="grid", default="auto",
                    help='排版：auto（自动判断）/ 1x4（一排四个）/ 2x2（田字格）')
    args = ap.parse_args()

    d = os.path.join(RAW_DIR, args.action)
    if not os.path.isdir(d):
        print("✗ 没有这个目录：%s" % d)
        return 1

    files = [f for f in os.listdir(d) if f.lower().endswith(IMG_EXT)]
    files.sort(key=natural_key)
    if not files:
        print("✗ %s 里没有图片" % d)
        return 1
    if len(files) > 1:
        print("⚠️ %s 里有 %d 张图，本脚本只处理「一张多格图」的情况。" % (d, len(files)))
        print("   如果已经是切好的单帧了，直接跑：python 归一化帧.py %s" % args.action)
        return 1

    src = os.path.join(d, files[0])
    img, note = strip_background(Image.open(src))
    print("源图: %s" % files[0])
    print("  去背景: %s" % note)

    a = np.array(img)[:, :, 3]
    runs = content_runs(a > ALPHA_TH)
    print("  检测到 %d 段内容（期望 %d 段）" % (len(runs), args.n))

    W, H = img.size

    # ── 田字格（2×2）排版 ──
    # ⚠️ AI 有时出一排四个、有时出田字格，**排版不是固定的**。
    #    只按横向切的话，田字格会被从中间竖着劈开（实测小人的头被切掉）。
    #    判断办法：看【行】方向有没有明显的空白带 —— 有就是田字格。
    grid = args.grid
    if grid == "auto":
        rowcol = (a > ALPHA_TH).sum(axis=1)
        mid = rowcol[int(H*0.42):int(H*0.58)]
        # 中间横带里几乎没有内容 → 上下两排是分开的
        grid = "2x2" if (len(mid) and mid.min() < rowcol.max() * 0.06) else "1x4"

    if grid == "2x2":
        print("  排版判断: 田字格（2×2）")
        colprof = (a > ALPHA_TH).sum(axis=0)
        rowprof = (a > ALPHA_TH).sum(axis=1)

        def split_at(prof, total):
            expect = total // 2
            win = max(8, int(total * 0.18))
            lo, hi = max(1, expect - win), min(total - 1, expect + win)
            return lo + int(np.argmin(prof[lo:hi]))

        cx = split_at(colprof, W)
        cy = split_at(rowprof, H)
        print("     横切 x=%d，纵切 y=%d" % (cx, cy))
        # 阅读顺序：左上 → 右上 → 左下 → 右下
        order = [(0, 0, cx, cy), (cx, 0, W, cy), (0, cy, cx, H), (cx, cy, W, H)]
        written = []
        for i, (x0, y0, x1, y1) in enumerate(order):
            panel = img.crop((x0, y0, x1, y1))
            out = os.path.join(d, "%s_%d.png" % (args.action, i))
            panel.save(out)
            written.append((os.path.basename(out), panel.size))
            print("  ✓ %s  %dx%d" % (os.path.basename(out), panel.width, panel.height))
        backup = os.path.join(d, "_原图备份")
        os.makedirs(backup, exist_ok=True)
        shutil.move(src, os.path.join(backup, files[0]))
        print("  原图已移到 %s" % backup)
        print("\n切出 %d 张。接着跑：" % len(written))
        print("  python 归一化帧.py %s" % args.action)
        print("  python 预览.py %s" % args.action)
        return 0

    if len(runs) != args.n:
        # ⚠️ 不要退回"按宽度等分" —— 角色往往不是等距排的，等分会把小人的半个身子切掉
        #    （实测第 4 个被切掉 110px）。
        #
        # 改成找"内容量的局部最小"当切点：角色之间的缝隙列通常不是【全空】的
        # （脚下有软阴影，缝隙列还剩几十个像素），所以严格判空会把四个人连成一片。
        # 看每列有多少不透明像素，在期望位置附近找最小值，就稳得多。
        print("  ⚠️ 严格判空只找到 %d 段（期望 %d）—— 角色之间的缝隙列还有阴影像素。" % (len(runs), args.n))
        print("     改用「内容量局部最小」定位切点。")

        col = (a > ALPHA_TH).sum(axis=0)
        cuts = []
        for i in range(1, args.n):
            expect = int(W * i / args.n)
            win = max(8, int(W / args.n * 0.45))
            lo, hi = max(1, expect - win), min(W - 1, expect + win)
            cuts.append(lo + int(np.argmin(col[lo:hi])))
        edges = [0] + cuts + [W]
        runs = [(edges[i], edges[i + 1]) for i in range(args.n)]
        print("     切点: %s" % cuts)

    backup = os.path.join(d, "_原图备份")
    os.makedirs(backup, exist_ok=True)

    written = []
    for i, (x0, x1) in enumerate(runs):
        panel = img.crop((x0, 0, x1, H))
        # ⚠️ **纵向【不要】裁到内容边界。**
        #
        #    踩过的坑：原来每格各自裁到自己的内容边界，于是
        #    "腿分开"那两格（角色整体本来就矮）和"腿并拢"那两格
        #    都被拉成同样的高度 —— **相对比例就丢了**。
        #    归一化再统一缩放，会把这个差异放大：
        #    实测 walk_0/walk_2 比 walk_1/walk_3 明显大一圈。
        #
        #    四格本来就是同一张画布生成的，**共用同一条纵坐标**才是对的 ——
        #    脚底的相对高低、角色的大小关系都在里面。
        #    横向裁是必须的（要切开），纵向一裁就毁掉比例。

        out = os.path.join(d, "%s_%d.png" % (args.action, i))
        panel.save(out)
        written.append((os.path.basename(out), panel.size))
        print("  ✓ %s  %dx%d" % (os.path.basename(out), panel.width, panel.height))

    shutil.move(src, os.path.join(backup, files[0]))
    print("  原图已移到 %s" % backup)

    print("\n切出 %d 张。接着跑：" % len(written))
    print("  python 归一化帧.py %s" % args.action)
    print("  python 预览.py %s" % args.action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
