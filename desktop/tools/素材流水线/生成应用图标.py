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
    python desktop/tools/素材流水线/生成应用图标.py logo   # ← 当前交付用的
    python desktop/tools/素材流水线/生成应用图标.py face   # 角色脸（默认）
产物：
    desktop/build/icon.ico                ← 交给 electron-builder，托盘图标也读它
    desktop/build/icon_sizes/*.png        ← 各档原样落盘，用来肉眼验收
    desktop/build/icon_sizes/_预览.png    ← 浅底/深底对照（棋盘格 = 透明）

════════════════════════════════════════════════════════════════════
两个模式（2026-09-20 加 logo 模式）
════════════════════════════════════════════════════════════════════
face —— 从角色立绘裁脸（上面那 4 条实测规则都是为它定的）
logo —— 从项目 logo（鲸鱼 + 书）出图标。**当前 exe / 托盘用的是这个。**

两者只有「怎么得到每一档的图」不同，出 ICO 的容器逻辑（`write_ico` / `to_dib`）
完全共用 —— 那部分是实测过的（小档位 BMP、256 走 PNG），别为 logo 另写一份。

logo 模式的四条差异：
1. **不描边**。logo 是白底上的实心蓝块，描边只会给正方形加一圈可见边框。
2. **先裁到「非背景」外接框再补白**。原图 1254×1254 里 logo 只占宽 72.6%、
   高 55.9%，四周留白极大；不裁的话缩到 16px 只剩一小团蓝。
   ⚠️ 不能用 `getbbox()` —— 它按「非 0 像素」算，白底 255 也非 0，会返回整张图。
3. **从矢量轮廓渲染，不是缩位图**。`load_tracer()` 加载同目录的
   `矢量化logo.py` 把 logo 描成轮廓，逐档按 even-odd 画（超采样 4 倍再缩回）。
   同时按「**目标尺寸下的实际像素面积**」丢掉亚像素细节（`MIN_DETAIL_PX2`）：
   16px 时眼睛只剩 0.73px²，丢掉后明显更干净；24px 起白月牙会保留。
   ⚠️ 位图缩放做不到这件事 —— 16px 下细缝和眼睛会被像素网格吃掉、糊成一团。
4. **保留白底**（不抠透明）。代价是深色任务栏下会看到一个白方块，这是有意接受的：
   白底在浅色任务栏 / 桌面 / 开始菜单都最稳。
"""

import os
import struct
import sys
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

# ⚠️ scipy 只在 face 模式的 add_outline() 里用（binary_dilation）。
#    放在函数内导入，好让 logo 模式在没装 scipy 的机器上也能跑。

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

# ── logo 模式（鲸鱼 + 书，项目 logo）────────────────────────────────
# 用仓库根 media/ 下那张（与门户配图同一份，SHA256 一致，不必另存副本）。
# 1254×1254、白底；logo 主色约 RGB(22,90,238)。
LOGO_SRC = os.path.join(os.path.dirname(DESKTOP), "media", "DeepProf.jpg")
LOGO_BG = (255, 255, 255)
LOGO_BG_TOL = 40  # 与白色的最大通道差超过它才算 logo 像素（JPEG 噪点 ≤2，留足余量）
LOGO_PAD = 0.06  # 四周留白，占正方形边长的比例
#: 轮廓在**目标尺寸**下的面积小于它（px²）就丢掉 —— 即「画出来不足 1 个像素的细节」。
#: 实测效果：16px 时眼睛的白月牙 0.73px²、瞳孔 0.06px² 被丢，只剩鲸鱼+书 3 条轮廓；
#: 24px 起白月牙（1.64px²）保留，瞳孔（0.14px²）仍丢。不丢的话那点细节只会糊成灰块。
MIN_DETAIL_PX2 = 1.0
#: 矢量渲染的超采样倍数：先按 size×4 画再缩回去，得到干净的抗锯齿边。
SUPERSAMPLE = 4


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


def logo_master(src, tol=LOGO_BG_TOL, pad=LOGO_PAD, bg=LOGO_BG):
    """把 logo 裁到「非背景」外接框，再补成白底正方形。

    同时返回**源坐标 → 母图坐标**的平移量，供矢量轮廓跟着一起搬。

    ⚠️ 不用 ``getbbox()``：它按「非 0 像素」算，白底 255 也非 0，
       整张图会被当成内容，等于没裁。这里按「与背景色的偏差」判。
    ⚠️ logo 内部也有白（书的页面、眼白），但它们在**外接框之内**，
       所以先算外接框、再整体裁，不会把这些白挖掉。
    """
    rgb = src.convert("RGB")
    a = np.array(rgb).astype(int)
    dev = np.abs(a - np.array(bg)).max(axis=2)
    ys, xs = np.where(dev > tol)
    if len(xs) == 0:
        raise ValueError("整张图都是背景色，找不到 logo（tol=%d）" % tol)

    x0, y0 = int(xs.min()), int(ys.min())
    img = rgb.crop((x0, y0, int(xs.max()) + 1, int(ys.max()) + 1))
    w, h = img.size
    side = int(max(w, h) * (1 + pad * 2))
    sq = Image.new("RGB", (side, side), bg)
    ox, oy = (side - w) // 2, (side - h) // 2
    sq.paste(img, (ox, oy))
    return sq, (-x0 + ox, -y0 + oy)


def logo_square(src, tol=LOGO_BG_TOL, pad=LOGO_PAD, bg=LOGO_BG):
    """只要正方形母图（logo_master 的薄包装）。"""
    return logo_master(src, tol, pad, bg)[0]


def load_tracer():
    """加载同目录的 `矢量化logo.py`。

    文件名含中文，所以按路径用 importlib 加载，不能直接 ``import``。
    """
    import importlib.util

    path = os.path.join(HERE, "矢量化logo.py")
    spec = importlib.util.spec_from_file_location("deepprof_logo_vector", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("加载不了矢量化脚本：%s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_polys(polys, master_side, size, min_px2=MIN_DETAIL_PX2, ss=SUPERSAMPLE, bg=LOGO_BG, fg=None):
    """按 even-odd 把矢量轮廓渲染成 size×size 的图。

    - 超采样 ss 倍再缩回，边缘比位图缩放的干净；
    - 轮廓在目标尺寸下面积 < min_px2 的丢掉（亚像素细节，留着只会糊成灰块）。
    """
    if fg is None:
        fg = (22, 90, 238)  # logo 主色
    scale = (master_side / size) ** 2  # 母图 1px² 折合目标尺寸多少 px²
    big = size * ss
    k = big / master_side
    acc = np.zeros((big, big), bool)
    for poly in polys:
        if _shoelace(poly) / scale < min_px2:
            continue
        layer = Image.new("1", (big, big), 0)
        ImageDraw.Draw(layer).polygon([(x * k, y * k) for x, y in poly], fill=1)
        acc ^= np.array(layer, dtype=bool)  # 逐条 XOR = even-odd，孔洞自动挖空
    out = np.full((big, big, 3), bg, np.uint8)
    out[acc] = fg
    return Image.fromarray(out).resize((size, size), Image.LANCZOS)


def _shoelace(poly):
    """多边形面积（鞋带公式，取绝对值）。"""
    pts = np.asarray(poly, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0


def add_outline(img, px, color=OUTLINE_COLOR):
    """沿 alpha 轮廓往外描 px 像素的边（颜色在角色【下面】）"""
    if px <= 0:
        return img
    from scipy import ndimage  # 只有本函数需要 scipy，延迟到这里导入

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
    """浅底 / 深底对照图，供肉眼验收（棋盘格 = 透明背景）。

    ⚠️ 两个模式的图模式不同：face 是 RGBA（描边靠 alpha），logo 是不透明 RGB。
       所以只有带 alpha 的图才能当蒙版用，否则 PIL 会抛 bad transparency mask。
    """
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
            mask = shown if shown.mode in ("RGBA", "LA") else None
            tile.paste(shown, (0, 0), mask)
            sheet.paste(tile, (gap + col * (cell + gap), gap + row * (cell + gap)))
    return sheet


def build_face(src):
    """face 模式：裁脸 → 逐档描边（≤32px 额外提色）"""
    master = square_crop(src.convert("RGBA"))
    print("裁切框 %s → 母图 %dx%d（留白 %.0f%%）" % (HEAD_BOX, master.size[0], master.size[1], PAD * 100))

    images = {}
    for size in SIZES:
        im = master.resize((size, size), Image.LANCZOS)
        if size <= 32:
            im = boost_small(im, *SMALL_BOOST)
        im = add_outline(im, outline_px(size))
        images[size] = im
        print("  %3dpx  描边 %dpx%s" % (size, outline_px(size), "  + 色彩增强" if size <= 32 else ""))
    return images


def build_logo(src):
    """logo 模式：用矢量轮廓逐档渲染（不描边、不动色）。

    ⚠️ 为什么不是直接缩位图：位图缩到 16px 时，书页细缝和眼睛会被像素网格
       吃掉、糊成一团。改用轮廓渲染 + 丢掉亚像素细节后，16px 明显更清楚，
       大尺寸边缘也比位图缩放干净。详见文件头「logo 模式」。
    """
    master, (dx, dy) = logo_master(src)
    side = master.size[0]
    polys = [
        [(x + dx, y + dy) for x, y in poly] for poly in load_tracer().trace(src)[0]
    ]
    print("外接框 → 母图 %dx%d（白底留白 %.0f%%），矢量轮廓 %d 条" % (side, side, LOGO_PAD * 100, len(polys)))

    images = {}
    for size in SIZES:
        kept = sum(1 for p in polys if _shoelace(p) / (side / size) ** 2 >= MIN_DETAIL_PX2)
        images[size] = render_polys(polys, side, size)
        print("  %3dpx  轮廓 %d/%d（丢掉 %d 条亚像素细节）" % (size, kept, len(polys), len(polys) - kept))
    return images


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "face"
    if mode not in ("face", "logo"):
        print("用法：python 生成应用图标.py [face|logo]（默认 face）")
        return 2

    src_path = SRC if mode == "face" else LOGO_SRC
    if not os.path.exists(src_path):
        print("❌ 找不到源图（模式 %s）：%s" % (mode, src_path))
        return 1

    src = Image.open(src_path)
    print("模式 %s ｜ 源图 %s  %dx%d" % (mode, os.path.basename(src_path), src.size[0], src.size[1]))

    images = build_face(src) if mode == "face" else build_logo(src)

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
