"""品牌资产与宣传图：logo 矢量 / 字标 / Hero 大图 / OG 分享卡。

背景沿用计划书封面的解析式配方（深海蓝底 + 品牌蓝辉光 + 点阵 + 暗角），
使站点主视觉与计划书封面同源。
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(os.environ.get("DP_FONT_DIR", Path(__file__).resolve().parent / "fonts"))
# 需要 NotoSerifCJKsc-Bold.otf / NotoSansCJKsc-Regular.otf（见 README 的字体一节）
SERIF_B = FONTS / "NotoSerifCJKsc-Bold.otf"      # 拉丁字面即 Source Serif
SANS_R = FONTS / "NotoSansCJKsc-Regular.otf"
SANS_B = FONTS / "NotoSansCJKsc-Bold.otf"

BRAND = (0x07, 0x65, 0xFC)
NAVY_900 = (0x03, 0x10, 0x2B)
NAVY_800 = (0x04, 0x17, 0x3F)

# 官方标识路径（logo/logo.svg，全为 M/L/Z 折线，可直接解析）
WHALE_D = None


def load_whale_svg(src: Path | None = None) -> str:
    """有 logo.svg 就读它，否则用内联的官方路径（两者内容一致）。"""
    if src is not None and Path(src).exists():
        m = re.search(r'\sd="([^"]+)"', Path(src).read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    from whale_path import WHALE_D
    return WHALE_D


def whale_polys(d: str):
    """把 M/L/Z 路径解析成多边形列表（坐标 0..1254）。"""
    polys, cur = [], []
    for tok in re.findall(r"[MLZ]|-?\d+\.?\d*", d):
        if tok == "M":
            if len(cur) > 2:
                polys.append(cur)
            cur = []
        elif tok == "L":
            continue
        elif tok == "Z":
            if len(cur) > 2:
                polys.append(cur)
            cur = []
        else:
            cur.append(float(tok))
    if len(cur) > 2:
        polys.append(cur)
    return [np.array(p).reshape(-1, 2) for p in polys]


def draw_whale(draw, polys, x, y, w, h, fill):
    sx, sy = w / 1254.0, h / 1254.0
    for p in polys:
        pts = [(x + px * sx, y + py * sy) for px, py in p]
        draw.polygon(pts, fill=fill)


def whale_bbox(polys):
    """标识真实外接框（路径 viewBox 留白很大，直接缩放会显得很小）。"""
    xs = np.concatenate([p[:, 0] for p in polys])
    ys = np.concatenate([p[:, 1] for p in polys])
    return xs.min(), ys.min(), xs.max(), ys.max()


def whale_mask(polys, box_w, box_h, margin=0.0):
    """按真实外接框等比缩放，返回 (box_h, box_w) 的 alpha 蒙版。
    margin 为四周留白占盒子的比例（0 表示正好贴合）。"""
    x0, y0, x1, y1 = whale_bbox(polys)
    bw, bh = x1 - x0, y1 - y0
    k = min((box_w * (1 - margin)) / bw, (box_h * (1 - margin)) / bh)
    ox = (box_w - bw * k) / 2 - x0 * k
    oy = (box_h - bh * k) / 2 - y0 * k
    # 标识用 fill-rule="evenodd"：子路径相互抵消（书页与眼睛是挖空）
    acc = np.zeros((box_h, box_w), dtype=bool)
    for pg in polys:
        tmp = Image.new("L", (box_w, box_h), 0)
        ImageDraw.Draw(tmp).polygon(
            [(ox + px * k, oy + py * k) for px, py in pg], fill=255)
        acc ^= (np.asarray(tmp) > 127)
    return acc.astype(np.float32)


# ------------------------------------------------------------------ 背景配方
def deep_sea(w: int, h: int, seed: int = 7) -> Image.Image:
    """解析式深海蓝背景：主辉光 + 次辉光 + 顶部提亮 + 底部压暗 + 点阵 + 暗角。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    def glow(cx, cy, r, expo, weight, ystretch=1.0):
        dx = (xx - cx * w)
        dy = (yy - cy * h) / ystretch
        dist = np.sqrt(dx * dx + dy * dy) / r
        return weight * np.clip(1.0 - dist, 0.0, None) ** expo

    g = glow(0.47, 0.30, 0.80 * h, 1.7, 0.92, 1.05)
    g += glow(0.95, 0.92, 0.62 * h, 2.2, 0.30)
    g = np.clip(g, 0.0, 1.0)

    t = np.clip(yy / (0.12 * h), 0.0, 1.0)
    g = g * (0.80 + 0.20 * t)
    b = np.clip((yy - 0.72 * h) / (0.28 * h), 0.0, 1.0)
    g = g * (1.0 - 0.45 * b)

    base = np.array(NAVY_900, dtype=np.float32)
    br = np.array(BRAND, dtype=np.float32)
    img = base[None, None, :] * (1 - g[..., None]) + br[None, None, :] * g[..., None]

    # 点阵
    step = max(6, int(round(0.0105 * w)))
    dot = np.zeros((h, w), dtype=np.float32)
    dot[::step, ::step] = 1.0
    dot[step // 2::step, step // 2::step] = 0.5
    yy2, xx2 = np.mgrid[0:h, 0:w]
    alt = ((xx2 // step) + (yy2 // step)) % 2
    dot = dot * np.where(alt == 0, 16.0 / 255.0, 8.0 / 255.0)
    img = img * (1 - dot[..., None]) + np.array([255, 255, 255], np.float32) * dot[..., None]

    # 暗角
    cxn, cyn = (xx / w - 0.5) * 2, (yy / h - 0.5) * 2
    vig = 1.0 - 0.38 * np.clip(np.sqrt(cxn ** 2 * 0.75 + cyn ** 2) - 0.35, 0, None) ** 1.4
    img = img * np.clip(vig, 0, 1)[..., None]

    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def rounded(img: Image.Image, r: int) -> Image.Image:
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], r, fill=255)
    out = img.convert("RGBA")
    out.putalpha(m)
    return out


def blend(rgb: np.ndarray, mask: np.ndarray, color) -> np.ndarray:
    c = np.array(color, dtype=np.float32)[None, None, :]
    return rgb * (1 - mask[..., None]) + c * mask[..., None]


# ------------------------------------------------------------------ 输出
def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    whale_d = load_whale_svg()
    polys = whale_polys(whale_d)

    # ---------- 1. logo.svg / logo-white.svg（矢量，文本文件）
    svg_body = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1254 1254" '
                'width="1254" height="1254" role="img" aria-labelledby="dp-mark-title">\n'
                '  <title id="dp-mark-title">DeepProf mark</title>\n'
                '  <path fill="FILL" fill-rule="evenodd" d="' + whale_d + '"/>\n</svg>\n')
    (out / "logo.svg").write_text(svg_body.replace("FILL", "#0765FC"), encoding="utf-8")
    (out / "logo-white.svg").write_text(svg_body.replace("FILL", "#FFFFFF"), encoding="utf-8")

    # ---------- 2. 字标 wordmark：标识 + 字形轮廓（自包含，不依赖目标机器字体）
    from fontTools.ttLib import TTFont
    from fontTools.pens.svgPathPen import SVGPathPen

    tt = TTFont(str(SERIF_B))
    gs = tt.getGlyphSet()
    upm = tt["head"].unitsPerEm
    cmap = tt.getBestCmap()
    WORD, TRACK_EM = "DeepProf", 0.010
    glyphs, xcur = [], 0.0
    for ch in WORD:
        gname = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[gname].draw(pen)
        glyphs.append((xcur, pen.getCommands()))
        xcur += gs[gname].width / upm + TRACK_EM
    word_em = xcur - TRACK_EM                       # 字串总宽（em）

    # 版面：以字高为基准排版，标识高度取字号的 1.16 倍
    FS = 480.0                                      # 字号（viewBox 单位）
    x0, y0, x1, y1 = whale_bbox(polys)
    bb_h, bb_w = (y1 - y0), (x1 - x0)
    mark_h = FS * 1.16
    mark_w = mark_h * bb_w / bb_h
    txt_w = word_em * FS
    GAP, PAD = FS * 0.46, FS * 0.26
    W_all = PAD * 2 + mark_w + GAP + txt_w
    H_all = FS * 1.50
    mark_s = mark_h / bb_h                          # 标识缩放
    baseline = H_all / 2 + FS * 0.355               # 约等于视觉垂直居中

    def glyph_svg(tx):
        out_p = []
        for gx, cmds in glyphs:
            if cmds:
                out_p.append(
                    f'<path transform="translate({tx + gx * FS:.1f},{baseline:.1f}) '
                    f'scale({FS / upm:.5f},{-FS / upm:.5f})" d="{cmds}"/>')
        return "\n    ".join(out_p)

    tx = PAD + mark_w + GAP
    ty_mark = (H_all - mark_h) / 2 - y0 * mark_s
    for name, mfill, tfill in (("wordmark.svg", "#0765FC", "#0A1A33"),
                               ("wordmark-white.svg", "#FFFFFF", "#FFFFFF")):
        body = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W_all:.0f} {H_all:.0f}" '
                f'width="{W_all:.0f}" height="{H_all:.0f}" role="img" '
                f'aria-labelledby="dp-wm-title">\n'
                f'  <title id="dp-wm-title">DeepProf</title>\n'
                f'  <g transform="translate({PAD:.1f},{ty_mark:.1f}) scale({mark_s:.5f})">'
                f'<path fill="{mfill}" fill-rule="evenodd" d="{whale_d}"/></g>\n'
                f'  <g fill="{tfill}">\n    {glyph_svg(tx)}\n  </g>\n</svg>\n')
        (out / name).write_text(body, encoding="utf-8")

    # ---------- 2b. 字标位图版（PNG，供 OG 卡 / 文档复用；也用于本地核验）
    for name, mfill, tfill in (("wordmark.png", BRAND, (0x0A, 0x1A, 0x33)),
                               ("wordmark-white.png", (255, 255, 255), (255, 255, 255))):
        WW, WH = int(round(W_all)), int(H_all)
        canvas = Image.new("RGBA", (WW, WH), (0, 0, 0, 0))
        mi = Image.fromarray(
            (whale_mask(polys, int(round(mark_w)) + 4, int(round(mark_h)) + 4,
                        margin=0.0) * 255).astype(np.uint8), "L")
        canvas.paste(Image.new("RGB", mi.size, mfill),
                     (int(PAD) - 2, int((WH - mi.size[1]) / 2)), mi)
        ImageDraw.Draw(canvas).text(
            (tx, baseline), WORD, anchor="ls",
            font=ImageFont.truetype(str(SERIF_B), int(FS)), fill=tfill)
        canvas.save(out / name)

    # ---------- 3. logo-512.png（透明底，供 favicon / 浅色页头）
    S = 512
    m = whale_mask(polys, S, S, margin=0.10)
    rgba = np.zeros((S, S, 4), np.float32)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = BRAND
    rgba[..., 3] = m * 255
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(out / "logo-512.png")
    # 白色版（深色底用）
    rgba2 = rgba.copy()
    rgba2[..., 0], rgba2[..., 1], rgba2[..., 2] = 255, 255, 255
    Image.fromarray(rgba2.astype(np.uint8), "RGBA").save(out / "logo-white-512.png")

    # ---------- 4. Hero 大图（纯氛围，文字交给 HTML，保证可缩放的清晰字形）
    HW, HH = 3200, 1400
    hero = deep_sea(HW, HH)
    arr = np.asarray(hero).astype(np.float32)
    # 右侧一枚大尺寸、低对比的水印标识
    wm = whale_mask(polys, int(HW * 0.46), int(HW * 0.40), margin=0.30)
    wm_img = Image.fromarray((np.clip(wm, 0, 1) * 255).astype(np.uint8), "L")
    wm_img = wm_img.filter(ImageFilter.GaussianBlur(1.2))
    canvas = Image.new("L", (HW, HH), 0)
    canvas.paste(wm_img, (int(HW * 0.60), int(HH * 0.14)))
    a = (np.asarray(canvas).astype(np.float32) / 255.0) * 0.085
    arr = blend(arr, a, (255, 255, 255))
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").save(
        out / "hero.png", optimize=True)

    # ---------- 5. OG 分享卡 1200x630
    og = deep_sea(1200, 630).convert("RGBA")
    d = ImageDraw.Draw(og)
    f_wm = ImageFont.truetype(str(SERIF_B), 54)
    f_h1 = ImageFont.truetype(str(SERIF_B), 62)
    f_sub = ImageFont.truetype(str(SANS_R), 26)
    f_mono = ImageFont.truetype(str(SANS_R), 22)

    # 标识
    lm = whale_mask(polys, 92, 74, margin=0.06)
    lm_img = Image.fromarray((lm * 255).astype(np.uint8), "L")
    og.paste(Image.new("RGB", (92, 74), (255, 255, 255)), (72, 56), lm_img)
    d.text((178, 60), "DeepProf", font=f_wm, fill=(255, 255, 255))

    d.text((72, 214), "让每一条教学建议都有出处", font=f_h1, fill=(255, 255, 255))
    d.text((72, 296), "证据可查 · 决策可审 · 失败可追", font=f_h1,
           fill=(0x7F, 0xB2, 0xFF))
    d.text((72, 392), "本机优先的自适应教学原型 · 试点课程：C 语言版数据结构",
           font=f_sub, fill=(0xB7, 0xD3, 0xFF))

    d.line([(72, 462), (1128, 462)], fill=(255, 255, 255, 60), width=1)
    d.text((72, 490), "v0.6.2", font=f_mono, fill=(0x8E, 0xAF, 0xE8))
    d.text((210, 490), "2026-09-24", font=f_mono, fill=(0x8E, 0xAF, 0xE8))
    d.text((392, 490), "github.com/RockeyRoc/DeepProf", font=f_mono,
           fill=(0x8E, 0xAF, 0xE8))
    d.text((72, 546), "M1 A/B 76/80 格 · M3 离线 400/400 格 · BKT AUC 0.296（低于随机，已公开）",
           font=f_mono, fill=(0x6E, 0x8A, 0xC4))

    og.convert("RGB").save(out / "og-card.png", optimize=True)
    print("done")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: make_promo.py <output-dir>")
    main(Path(sys.argv[1]))
