# -*- coding: utf-8 -*-
"""
对帧.py —— 出图端的画幅差异，在进归一化之前先抹平。

用法：
    python 对帧.py blink          # 用 blink_0 当基准
    python 对帧.py talk --基准 0
    python 对帧.py blink --试运行   # 只报告，不写文件

━━━ 为什么需要这一步 ━━━

即梦出图时画布尺寸会飘：idle 是 1728x2304（竖幅），眨眼/张嘴帧却是 2048x2048（方图）。
麻烦的不是画布形状，是**角色在画布里的相对大小也一起变了**：

    角色高 / 画布宽     idle  2190/1728 = 1.267
                        blink 1970/2048 = 0.962   ← 小了 24%

`归一化帧.py` 的逐帧模式有一条【阻尼校正】（DAMP=0.7，为走路的一胀一缩设计的）：
它把各帧高度往中位数拉 70%。于是 10.5% 的真实尺寸差被压成 3.2% —— **压住不等于消除**，
眨眼时脸还是会缩一下。阻尼是给"动作起伏"用的，不该拿来兜"出图画小了"。

所以正确做法是：**先把每帧的角色缩放到和基准帧一样大，再进流水线。**
这样各帧高度天然一致，阻尼系数接近 1，什么都不用兜。

━━━ 做了什么 ━━━

1. 定一个**尺寸基准**：各帧画布里**最小的那个**（见下）
2. 把所有帧**整体缩放**到该基准下的同一个「角色高/画布宽」比值
3. 写回 `原始/<动作>/`

只动缩放，不碰几何 —— 对齐和摆位仍然交给 `归一化帧.py`，两件事分开。

━━━ 为什么基准要取「最小画布」，而不是「第一帧」━━━

如果拿 idle 那帧（1728x2304，角色高/宽=1.2674）当基准，方图帧（0.9619）就得**放大 1.32 倍**：
角色从 1970px 拉到 2596px，**超出自己的 2048 画布，会当场被裁掉**。
而且放大只会糊，不会多出细节。

所以基准取各帧里**最小的画布**：大的缩下来、小的基本不动，两边都只缩不放。
"""

import argparse
import os
import sys

from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_ROOT = os.path.join(HERE, "动作帧", "原始")


def measure_char(img, thr=225, min_px=3):
    """量角色外接框。阈值 225 是踩出来的：即梦的'白底'不是纯白，用 240 会把背景噪点当角色。"""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(int)
    fg = a.min(axis=2) < thr
    rows = fg.sum(axis=1)
    cols = fg.sum(axis=0)
    ys = np.nonzero(rows > min_px)[0]
    xs = np.nonzero(cols > min_px)[0]
    if len(ys) == 0 or len(xs) == 0:
        return None
    return {"h": int(ys.max() - ys.min() + 1), "w": int(xs.max() - xs.min() + 1)}


def main():
    ap = argparse.ArgumentParser(description="抹平出图端的画幅差异")
    ap.add_argument("action", help="动作名（blink / talk / walk …）")
    ap.add_argument("--横缩", dest="squeeze", type=float, default=None,
                    help="把每一帧的宽度乘上该系数后再对齐（只改宽、不改高）。"
                         "用于修「AI 把角色整体画宽了」——等比缩放修不了，越修越高")
    ap.add_argument("--横缩_", dest="squeeze_each", default=None,
                    help="按帧指定压缩比，形如 0:1.0,1:0.894（下标从 0 起）。"
                         "给了它就忽略 --横缩。各帧宽度差不同时必须用它。")
    ap.add_argument("--试运行", dest="dry", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    d = os.path.join(RAW_ROOT, args.action)
    if not os.path.isdir(d):
        print("✗ 找不到目录：%s" % d)
        return 1

    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
    if len(files) < 2:
        print("✗ %s 下只有 %d 张图，没得对" % (args.action, len(files)))
        return 1

    # ── 第一遍：先全量一次，才知道最小画布和基准比值 ──
    loaded = []
    for f in files:
        p = os.path.join(d, f)
        im = Image.open(p)
        m = measure_char(im)
        if m is None:
            print("  ✗ %-16s 量不出角色，跳过" % f)
            continue
        loaded.append((f, p, im, m))

    if len(loaded) < 2:
        print("✗ 能量出角色的图不足 2 张")
        return 1

    # ── 定目标尺寸 ──
    #
    # 要对齐的是【角色的绝对高度】，不是"角色高/画布宽"那个比值 ——
    # 比值对齐是个陷阱：画布和角色一起变的时候，比值能对上、高度反而差更多。
    #
    # 目标高度取【角色最矮的那一帧】。因为缩放有个硬约束：
    # **只缩不放**——放大只会糊，而且角色一旦占满画布高度，再放大就顶出去被裁。
    # 取最矮的那帧当目标，其余各帧都是往下缩，天然满足约束。
    #
    #   blink：idle 高 2190，方图高 1970 → 目标 1970，idle 缩 0.90 倍。
    base_h = min(m["h"] for _, _, _, m in loaded)
    ref = min(loaded, key=lambda t: t[3]["h"])

    print("角色最矮的一帧是 %s（高 %d px）—— 以它为对齐目标" % (ref[0], base_h))
    print("（只缩不放：放大只会糊，且角色顶满画布高度后再放大会被裁）")
    print()

    # 解析按帧压缩比
    per_frame = {}
    if args.squeeze_each:
        for part in args.squeeze_each.split(","):
            k_, v_ = part.split(":")
            per_frame[int(k_)] = float(v_)

    changed = 0
    for idx, (f, p, im, m) in enumerate(loaded):
        ratio = m["h"] / im.width
        k = base_h / float(m["h"])       # 让这张的角色高变成 base_h
        nw = int(round(im.width * k))
        nh = int(round(im.height * k))

        # 横向压缩：只改宽、不改高。
        # ⚠️ 这是给「AI 把整个角色画宽了」用的补救 —— 等比缩放会把角色改高，
        #    换一个问题而已。压缩比要按【归一化之后】量到的宽度差来定，
        #    不能按原图外接框算（归一化按头心对齐时已经吸收了一部分差异）。
        sq = per_frame.get(idx, args.squeeze if args.squeeze else 1.0)
        if sq and abs(sq - 1.0) > 1e-6:
            nw = max(1, int(round(nw * sq)))

        over = nw > im.width or nh > im.height

        # ⚠️ 判断"要不要动"必须同时看高度和压缩 ——
        #    只判 k 的话，k≈1（高度已经对齐）会走"已对齐不动"提前返回，
        #    把 --横缩 整个跳过（踩过：两条命令都报"改了 0 张"）。
        need = abs(nw - im.width) > 1 or abs(nh - im.height) > 1

        flag = ""
        if not need:
            flag = "  ← 已经对齐，不动"
        elif over:
            flag = "  ⚠️ 缩放后 %dx%d 超出原画布 %dx%d，会裁切 —— 跳过不写" % (nw, nh, im.width, im.height)
        elif args.dry:
            flag = "  ← 试运行，未写（会缩到 %dx%d，角色高 %d）" % (nw, nh, base_h)
        else:
            im.resize((nw, nh), Image.LANCZOS).save(p)
            m2 = measure_char(Image.open(p))
            flag = "  ← 已缩放到 %dx%d（角色高 %4d）" % (nw, nh, m2["h"])
            changed += 1

        print("  %-16s %dx%-5d 角色高 %4d  高/宽=%.4f  k=%.4f%s"
              % (f, im.width, im.height, m["h"], ratio, k, flag))

    print()
    if args.dry:
        print("试运行结束，没有写任何文件。去掉 --试运行 才会真的改。")
    else:
        print("改了 %d 张。接着跑：" % changed)
        print("  python 归一化帧.py %s --模式 逐帧" % args.action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
