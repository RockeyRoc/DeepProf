# -*- coding: utf-8 -*-
"""眨眼帧合成 —— 把「闭眼」图的**眼部区域**贴到「睁眼」图上。

═══ 为什么需要这一步 ═══

即梦/组长给的「睁眼」「闭眼」两张，是**两次独立生成**，不是同一张图改眼睛。
实测：全身 **21.5%** 的像素不同、整体平移解释不了（挪 4px 只消掉 14% 的差异）、
两张的轮廓框底部还差 **9px**。

后果：如果直接把两张丢进流水线当 blink_0 / blink_1，**切帧时整个角色会抖一下**
（头发丝、裙褶、描边、脚全都在动）—— 那不是眨眼，是"整个人抖了一下"。
这正是本项目最怕的那类**静默失败**：不报错、不崩溃、只是表现不对。

解决办法：**只取闭眼图的眼部区域**贴到睁眼图上。这样 blink_1 的身体像素与
blink_0 **完全一致**，抖动为零 —— 与"同姿势两张"的效果等价。

═══ 用法 ═══

在素材工作区目录下（含 `动作帧/` 那一层）运行：

    python 眨眼合成.py --睁眼 睁眼.png --闭眼 闭眼.png
    python 眨眼合成.py --睁眼 a.png --闭眼 b.png --输出 动作帧/预览/

参数都可省。默认会去 `_新角色_官方设定/_第三批_20260920/` 下找那两张标准命名的图。

输出：
- `动作帧/预览/眨眼_合成结果.png` —— 合成出的闭眼整图（拿去当 blink_1）
- `动作帧/预览/眨眼_对位对比.png` —— 脸部放大三联图（左=睁眼 中=合成 右=原生闭眼），肉眼验收用

═══ 踩过的坑（改动前先读）═══

1. **眼部定位必须卡死范围**。第一版放到 y25%~52% / 全宽去找深色睫毛线，
   结果把左右两个蓝蝴蝶结的深色描边也当成了眼睛，bbox 直接撑满整张图
   （x 92~1065，而图宽才 1086），算出来的"偏移 dx=1"完全是假的。
   现在卡在 y32%~44% / x26%~70% —— 这个范围是打 ASCII 点阵探出来的，换角色要重探。

2. **残差偏高是正常的**。一张有蓝眼珠、一张没有，同一块地方本来就应该差很多。
   别拿残差大小判断"对没对上"，要看合成出来的脸有没有接缝。
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

DEFAULT_DIR = os.path.join("_新角色_官方设定", "_第三批_20260920")
DEFAULT_OPEN = os.path.join(DEFAULT_DIR, "01_睁眼张嘴_a5adb8ee.png")
DEFAULT_CLOSE = os.path.join(DEFAULT_DIR, "02_闭眼张嘴_1073bd8b.png")
DEFAULT_OUT = os.path.join("动作帧", "预览")


def load(p):
    im = Image.open(p).convert("RGBA")
    a = np.array(im).astype(np.int16)
    return a[:, :, :3], a[:, :, 3]


def find_eyes(rgb, alpha):
    """靠【深色睫毛线】定位眼部。范围卡死，理由见模块文档「踩过的坑 1」。"""
    h, w = alpha.shape
    dark = (alpha > 128) & (rgb.mean(axis=2) < 110)
    band = np.zeros_like(dark)
    band[int(h * 0.32):int(h * 0.44), int(w * 0.26):int(w * 0.70)] = True
    ys, xs = np.nonzero(dark & band)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())


def best_offset(rgb_a, rgb_b, box, rng=8):
    """在脸部窗口内搜最佳平移，让 B 对齐 A。"""
    x0, x1, y0, y1 = box
    pad = rng + 2
    ref = rgb_a[y0 - pad:y1 + pad, x0 - pad:x1 + pad].astype(np.float32)
    best = (1e18, 0, 0)
    for dy in range(-rng, rng + 1):
        for dx in range(-rng, rng + 1):
            cand = rgb_b[y0 - pad + dy:y1 + pad + dy,
                         x0 - pad + dx:x1 + pad + dx].astype(np.float32)
            if cand.shape != ref.shape:
                continue
            v = float(np.abs(ref - cand).mean())
            if v < best[0]:
                best = (v, dx, dy)
    return best[1], best[2], best[0]


def feather_mask(w, h, pad):
    """矩形掩膜，四周 pad 像素线性羽化 —— 避免贴图出现硬边接缝。

    ⚠️ 形状是 (h, w) **不是** (w, h) —— 这里抄反过一次，直接报
    `operands could not be broadcast together with shapes (499,225) (225,499)`。
    眼部矩形是【宽而扁】的（约 499×225），所以抄反了必炸。
    """
    m = np.ones((h, w), np.float32)
    ramp = np.linspace(0, 1, pad, endpoint=False)
    m[:pad, :] *= ramp[:, None]
    m[-pad:, :] *= ramp[::-1, None]
    m[:, :pad] *= ramp[None, :]
    m[:, -pad:] *= ramp[::-1][None, :]
    return m


def shift(img, dx, dy):
    return np.array(Image.fromarray(img.astype(np.uint8)).transform(
        img.shape[:2][::-1], Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
        resample=Image.BICUBIC)).astype(np.int16)


def main():
    ap = argparse.ArgumentParser(description="眨眼帧合成（眼部区域局部替换）")
    ap.add_argument("--睁眼", dest="open_img", default=DEFAULT_OPEN,
                    help="基准图（身体取它）。默认 %s" % DEFAULT_OPEN)
    ap.add_argument("--闭眼", dest="close_img", default=DEFAULT_CLOSE,
                    help="只取它的眼部区域。默认 %s" % DEFAULT_CLOSE)
    ap.add_argument("--输出", dest="outdir", default=DEFAULT_OUT,
                    help="输出目录。默认 %s" % DEFAULT_OUT)
    ap.add_argument("--边距", dest="pad", type=int, default=26,
                    help="眼部矩形往外扩多少像素（盖住上下眼睑）")
    ap.add_argument("--羽化", dest="fpad", type=int, default=10,
                    help="羽化宽度，防止接缝")
    args = ap.parse_args()

    for p in (args.open_img, args.close_img):
        if not os.path.exists(p):
            print("找不到输入图：%s" % p, file=sys.stderr)
            return 1

    ra, aa = load(args.open_img)    # 睁眼 = 基准
    rb, ab = load(args.close_img)   # 闭眼 = 只取眼睛

    print("睁眼基准 %s" % args.open_img)
    print("闭眼取材 %s" % args.close_img)

    ea, eb = find_eyes(ra, aa), find_eyes(rb, ab)
    if ea is None or eb is None:
        print("眼部没找到 —— 换角色后要重探范围（见模块文档「踩过的坑 1」）", file=sys.stderr)
        return 2

    print("\n眼部深色区 bbox:")
    print("  睁眼图: x %d~%d  y %d~%d" % ea)
    print("  闭眼图: x %d~%d  y %d~%d" % eb)
    print("  ⚠️ 闭眼图深色区【天然更小】——闭眼没了眼珠，这是正常的，不是没对上")

    dx, dy, res = best_offset(ra, rb, ea)
    print("\n最佳对齐: 闭眼图平移 dx=%d dy=%d（残差 %.2f —— 偏高正常，见模块文档）"
          % (dx, dy, res))

    # ---- 合成 ----
    x0, x1, y0, y1 = eb
    X0, Y0 = max(0, x0 - args.pad), max(0, y0 - args.pad)
    X1, Y1 = min(rb.shape[1], x1 + args.pad), min(rb.shape[0], y1 + args.pad)

    patch = shift(rb, dx, dy)
    patch_a = shift(ab, dx, dy)

    out = ra.copy()
    sub, sub_a = patch[Y0:Y1, X0:X1], patch_a[Y0:Y1, X0:X1]
    h, w = sub.shape[:2]
    m = feather_mask(w, h, args.fpad)
    # 只在两张图都是角色实心的地方贴，避免把透明背景糊进脸
    m = m * ((aa[Y0:Y1, X0:X1] > 128) & (sub_a > 128))
    for c in range(3):
        out[Y0:Y1, X0:X1, c] = (ra[Y0:Y1, X0:X1, c] * (1 - m) + sub[:, :, c] * m).astype(np.int16)

    os.makedirs(args.outdir, exist_ok=True)
    dst = os.path.join(args.outdir, "眨眼_合成结果.png")
    Image.fromarray(np.dstack([out.astype(np.uint8), aa.astype(np.uint8)]), "RGBA").save(dst)
    print("\n✅ 合成图 → %s" % dst)
    print("   把它当 blink_1 用（身体与 blink_0 完全一致，抖动为零）")

    # ---- 验收三联图 ----
    zy0, zy1 = max(0, y0 - 90), min(ra.shape[0], y1 + 90)
    zx0, zx1 = max(0, x0 - 60), min(ra.shape[1], x1 + 60)
    tiles = [ra[zy0:zy1, zx0:zx1], out[zy0:zy1, zx0:zx1], patch[zy0:zy1, zx0:zx1]]
    gap = np.full((zy1 - zy0, 12, 3), 255, np.int16)
    strip = np.hstack([t if i == len(tiles) - 1 else np.hstack([t, gap])
                       for i, t in enumerate(tiles)])
    dst2 = os.path.join(args.outdir, "眨眼_对位对比.png")
    Image.fromarray(strip.astype(np.uint8)).convert("RGB").save(dst2)
    print("✅ 验收三联图 → %s" % dst2)
    print("   （左=睁眼原图  中=合成闭眼  右=原生闭眼）**看一眼有没有接缝再往下走**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
