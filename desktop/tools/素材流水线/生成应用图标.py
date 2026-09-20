# -*- coding: utf-8 -*-
"""从角色立绘裁一张脸，生成 Windows 应用图标 `desktop/build/icon.ico`（多档尺寸）。

════════════════════════════════════════════════════════════════════
为什么要有这个脚本
════════════════════════════════════════════════════════════════════
以前 `npm run dist` 每次都会打印
    • default Electron icon is used  reason=application icon is not set
任务栏、快捷方式、exe 全是 Electron 默认标。这个脚本生成 `build/icon.ico`
（electron-builder 的 `directories.buildResources = "build"`，默认就找这里）。

════════════════════════════════════════════════════════════════════
几件不能想当然的事（都是实测定下来的，改参数前先读）
════════════════════════════════════════════════════════════════════
1. **裁头部，不是全身**。角色是 2.5 头身，全身缩到 16px 只剩一个竖条色块。
   裁到「脸 + 刘海 + 头顶鲸鱼发夹」，让脸占满画布。

2. **描边必须按【输出尺寸】算像素，不能先描边再缩放**。
   第一版是在高分辨率母图上描边（2.5% 画布宽 ≈ 12px），再缩到 16px ——
   那 12px 被缩成 **0.38px**，等于没有，图标在浅色任务栏上直接糊掉。
   现在是每档尺寸**单独**描 1~7px 的边（`outline_px()`）。

3. **16px 单独加了饱和度/对比度**（`SMALL_BOOST`）。这一档的细节只剩
   「两只蓝眼睛 + 一撮头发」，原画的浅色调（白发+白脸）拉开不够，
   缩完五官是灰的。只对 ≤32px 生效 —— 大档位不需要，也不该动。

4. **ICO 里小档位写 BMP(DIB)、256 写 PNG**。这是 Windows 图标最通行的存法：
   256 用 BMP 会到 260KB+（贴着单张 256KB 的上限），小档位用 PNG 则有些
   老代码路径不认。Pillow 的 `save(sizes=...)` 只能整份统一一种格式，
   所以这里自己拼 ICO 容器（`write_ico()`）。

════════════════════════════════════════════════════════════════════
⚠️ 交付注意：icon.ico 会被仓库根的 .gitignore 挡掉
════════════════════════════════════════════════════════════════════
仓库根 `.gitignore` 第 13 行是 Python 模板留下的 `build/`，它**也匹配
`desktop/build/`**。后果：`git status` 里看不见 `build/icon.ico`，
不提就进不了仓库 —— 别人 clone 下来一打包，「没有应用图标」那个警告又回来了。
（试过在 `desktop/.gitignore` 里写 `!build/icon.ico`，**无效** ——
 git 的规则是「父目录被排除的文件无法再包含」。）

所以提交时**必须显式加**：
    git add -f desktop/build/icon.ico
加过一次之后它就进了版本控制，以后再改不用再加 -f。

跑法（在哪跑都行，路径按本文件推）：
    python desktop/tools/素材流水线/生成应用图标.py
产物：
    desktop/build/icon.ico                ← 交给 electron-builder
    desktop/build/icon_sizes/*.png        ← 各档原样落盘，用来肉眼验收
    desktop/build/icon_sizes/_预览.png    ← 浅底/深底对照（棋盘格 = 透明）
"""

import os
import struct
import sys
from io import BytesIO

import numpy as np
from PIL import Image, ImageEnhance
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8")  # GBK 控制台下打印 ⚠️ 会 UnicodeEncodeError

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(os.path.dirname(HERE))  # …/desktop

# 当前角色「睁眼闭嘴」的站姿立绘（2026-09-20 晚换上去的那张）。
# 换角色 / 换姿势后**必须重跑本脚本**，否则图标还是旧形象。
SRC = os.path.join(DESKTOP, "src", "renderer", "src", "assets", "pet", "frames", "blink", "blink_0.png")
OUT_ICO = os.path.join(DESKTOP, "build", "icon.ico")
OUT_DIR = os.path.join(DESKTOP, "build", "icon_sizes")

# ── 裁切框（源图画布坐标，1536×1536）────────────────────────────────
# 怎么定出来的：先用 alpha 通道量出角色外接框 x[317,1240] y[212,1412]，
# 再用肤色/蓝眼珠定位五官 —— 眼睛在 y 640~740、脸的横向中心 x≈790。
# 这个框取「脸 + 刘海 + 头顶鲸鱼发夹」，下沿切在胸口（藏青领子那里，
# 正好收住一张脸的底座），右上角把鲸鱼发夹整只框进来。
HEAD_BOX = (572, 468, 1002, 932)
PAD = 0.06  # 四周留白，占画布边长的比例
OUTLINE_COLOR = (23, 56, 120)  # 深藏青，和白发/浅色任务栏都能拉开
SMALL_BOOST = (1.30, 1.15)  # (饱和度, 对比度) —— 只给 ≤32px 用
SIZES = [16, 24, 32, 48, 64, 128, 256]


def outline_px(size):
    """这一档该描几个像素的边。

    ⚠️ 是**输出尺寸**的像素，不是母图的。16px 下 1px 的边很关键：
    没有它，浅色任务栏上的白发图标就是一坨白。2px 又会把脸吃掉，所以别贪。
    """
    return max(1, int(round(size * 0.028)))


def square_crop(src, box=HEAD_BOX, pad=PAD):
    """裁框 → 去掉透明边 → 补成正方形（角色居中，四周留 pad 的边）"""
    img = src.crop(box)
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    side = int(max(w, h) * (1 + pad * 2))
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return sq


def add_outline(img, px, color=OUTLINE_COLOR):
    """沿 alpha 轮廓往外描 px 像素的边（颜色在角色【下面】）"""
    if px <= 0:
        return img
    a = np.array(img)
    solid = a[:, :, 3] > 40
    grown = ndimage.binary_dilation(solid, iterations=px)
    ring = np.zeros_like(a)
    ring[..., 0], ring[..., 1], ring[..., 2] = color
    ring[..., 3] = np.where(grown, 255, 0).astype(np.uint8)
    base = Image.fromarray(ring, "RGBA")
    base.alpha_composite(img)
    return base


def boost_small(img, sat, con):
    """只提颜色，不动 alpha —— 免得把描边的边缘也改掉"""
    rgb = img.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(sat)
    rgb = ImageEnhance.Contrast(rgb).enhance(con)
    return Image.merge("RGBA", (*rgb.split(), img.split()[3]))


# ══════════════════════════════════════════════════════════════════
# ICO 容器：小档位 BMP(DIB) + 256 用 PNG
#   ⚠️ 不用 Pillow 的 save(sizes=...) —— 它整份只能统一一种格式，
#      而且给的尺寸会被它自己 thumbnail() 再缩一遍（不受控）。
#      这里每档都是我们自己渲染好的图，原样塞进去。
# ══════════════════════════════════════════════════════════════════
def to_dib(img):
    """32 位 BMP(DIB)：BITMAPINFOHEADER + BGRA 自下而上 + AND 掩码"""
    w, h = img.size
    a = np.array(img.convert("RGBA"))
    alpha = a[:, :, 3]
    # XOR 位图：BGRA、自下而上
    bgra = np.dstack([a[:, :, 2], a[:, :, 1], a[:, :, 0], a[:, :, 3]])
    xor = bgra[::-1].tobytes()
    # AND 掩码：1bpp，每行补齐到 4 字节；位=1 表示透明（老代码路径用）
    transparent = (alpha < 128).astype(np.uint8)[::-1]
    stride = ((w + 31) // 32) * 4
    and_mask = np.zeros((h, stride), np.uint8)
    for y in range(h):
        bits = transparent[y]
        for x in range(w):
            if bits[x]:
                and_mask[y, x // 8] |= 0x80 >> (x % 8)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,        # biSize
        w,         # biWidth
        h * 2,     # biHeight —— ICO 里是 XOR+AND 两半，写 2 倍
        1,         # biPlanes
        32,        # biBitCount
        0,         # biCompression = BI_RGB
        len(xor) + len(and_mask),  # biSizeImage
        0, 0, 0, 0
    )
    return header + xor + and_mask.tobytes()


def write_ico(path, images):
    """images: {边长: RGBA 图}"""
    entries = []
    for size in sorted(images):
        im = images[size]
        if size >= 256:
            buf = BytesIO()
            im.save(buf, "PNG", optimize=True)
            entries.append((size, buf.getvalue()))
        else:
            entries.append((size, to_dib(im)))

    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(entries)))  # ICONDIR
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        out.write(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,  # 0 表示 256
            size if size < 256 else 0,
            0, 0, 1, 32, len(data), offset
        ))
        offset += len(data)
    for _, data in entries:
        out.write(data)
    with open(path, "wb") as f:
        f.write(out.getvalue())
    return len(out.getvalue())


def make_sheet(images):
    """浅底 / 深底对照图，供肉眼验收（棋盘格 = 透明背景）"""
    gap = 10
    cell = 128
    names = sorted(images)
    W = gap + len(names) * (cell + gap)
    H = gap + 2 * (cell + gap)
    sheet = Image.new("RGB", (W, H), (200, 200, 200))
    for col, size in enumerate(names):
        small = images[size]
        for row, dark in enumerate([False, True]):
            if dark:
                tile = Image.new("RGB", (cell, cell), (32, 32, 36))
            else:
                # 棋盘格，一眼看出哪些像素是透明的
                tile = Image.new("RGB", (cell, cell), (255, 255, 255))
                for by in range(0, cell, 16):
                    for bx in range(0, cell, 16):
                        if (bx // 16 + by // 16) % 2:
                            tile.paste((205, 205, 205), (bx, by, bx + 16, by + 16))
            shown = small.resize((cell, cell), Image.NEAREST)  # NEAREST：看的是真实像素
            tile.paste(shown, (0, 0), shown)
            sheet.paste(tile, (gap + col * (cell + gap), gap + row * (cell + gap)))
    return sheet


def main():
    if not os.path.exists(SRC):
        print("❌ 找不到源立绘：%s" % SRC)
        return 1

    src = Image.open(SRC).convert("RGBA")
    print("源图 %s  %dx%d" % (os.path.basename(SRC), src.size[0], src.size[1]))

    master = square_crop(src)
    print("裁切框 %s → 母图 %dx%d（留白 %.0f%%）" % (HEAD_BOX, master.size[0], master.size[1], PAD * 100))

    images = {}
    for size in SIZES:
        im = master.resize((size, size), Image.LANCZOS)
        if size <= 32:
            im = boost_small(im, *SMALL_BOOST)
        im = add_outline(im, outline_px(size))
        images[size] = im
        print("  %3dpx  描边 %dpx%s" % (size, outline_px(size), "  + 色彩增强" if size <= 32 else ""))

    os.makedirs(OUT_DIR, exist_ok=True)
    for size, im in images.items():
        im.save(os.path.join(OUT_DIR, "%d.png" % size))
    make_sheet(images).save(os.path.join(OUT_DIR, "_预览.png"))

    os.makedirs(os.path.dirname(OUT_ICO), exist_ok=True)
    n = write_ico(OUT_ICO, images)
    print("\n✅ %s  （%d 档尺寸，%d 字节）" % (OUT_ICO, len(images), n))
    print("   各档 PNG + 对照图：%s" % OUT_DIR)
    print("   ⚠️ 提交前记得：git add -f desktop/build/icon.ico （详见本文件头部）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
