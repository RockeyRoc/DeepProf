# -*- coding: utf-8 -*-
"""分层 rig 预览 —— 不碰桌宠代码，先在 Python 里把走路演出来看。

这一步的价值：拆腿拆得对不对是"数据"问题，转起来像不像走路是"观感"问题。
先在 Python 里 30 秒出一版动图，比改半天 JS 再回滚快得多。

═══ 它会做两件校验 ═══

1. **零角度必须还原原图** —— 腿不转的时候，三层叠起来必须和原始立绘逐像素一致。
   对不上说明层的位置/顺序错了，这是最硬的一条正确性检查。
2. **延长区域必须被身体完全盖住** —— 髋点到裙摆之间那段是"糊"出来的，
   只要有一个像素露在身体外面，转起来就会看到一块凭空冒出来的肉色方块。

═══ 用法 ═══

    python 预览rig.py                # 默认摆幅 9 度，8 帧
    python 预览rig.py --摆幅 12 --帧数 8
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # DeepProf/
RIG = os.path.join(HERE, "动作帧", "rig")
PREVIEW = os.path.join(HERE, "动作帧", "预览")

# ⚠️ 2026-09-19 踩过的坑：这里原来和 拆腿.py 一样硬编码了
#    assets/pet/idle.png（上一版 642×1024 的旧图）。拆腿那边一旦改用别的源，
#    校验1 就会**拿旧图去比对**：尺寸一样时"看起来通过了"，其实是自欺欺人；
#    尺寸不一样时才报"尺寸不一致"，还得人肉回查。
#    现在源图只从 参数.json 的 source_path 读 —— 拆的是谁就校验谁，单一事实源。
DEFAULT_SRC = os.path.join(ROOT, "desktop", "src", "renderer", "src",
                           "assets", "pet", "expressions", "idle.png")


def resolve_src(meta):
    """校验1 要拿来做逐像素比对的源图。取 参数.json 记的 source_path。

    ⚠️ 如果拆腿时用了 `--裁到角色`，源图在原坐标系里是被裁过的 ——
       必须按 参数.json 里的 source_crop **裁同一个框**，否则合成图和源图尺寸
       对不上，校验1 会直接报"尺寸不一致"（而其实拆得没问题）。
       裁切信息跟着 参数.json 走，还是那条原则：单一事实源，
       校验脚本不自己猜。
    """
    img = None
    rel = (meta or {}).get("source_path")
    if rel:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if os.path.exists(p):
            img = Image.open(p).convert("RGBA")
        else:
            print("  ⚠️ 参数.json 里的 source_path 找不到：%s" % p)
    if img is None:
        img = Image.open(DEFAULT_SRC).convert("RGBA")

    crop = (meta or {}).get("source_crop")
    if crop and len(crop) == 4:
        img = img.crop(tuple(int(v) for v in crop))
    return img


ALPHA_TH = 8


def load_font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def checkerboard(w, h, size=16):
    yy, xx = np.mgrid[0:h, 0:w]
    tile = ((yy // size) + (xx // size)) % 2
    val = np.where(tile == 0, 236, 206).astype(np.uint8)
    return Image.fromarray(np.dstack([val, val, val]), "RGB")


# ════════════════════════════════════════════════════════════
# 校验
# ════════════════════════════════════════════════════════════

def check_rest(body, leg_l, leg_r, meta, src):
    """零角度必须逐像素还原原图 —— 这是最硬的一条正确性检查。

    层只要错位一个像素、或者 z 序排反了，这里就炸。
    """
    comp = compose(body, leg_l, leg_r, meta, 0.0, 0.0, 0.0)
    # src 现在是已裁好的图像对象（见 resolve_src），不是路径
    orig = src if isinstance(src, Image.Image) else Image.open(src).convert("RGBA")
    if comp.size != orig.size:
        return False, "尺寸不一致 %s（合成）vs %s（源图，已按 source_crop 裁过）" % (
            comp.size, orig.size)

    # 按【预乘 alpha】比较：alpha=0 的像素无论 RGB 是什么都看不见，
    # 直接比 RGB 会把它们算成"错"，是假警报。预乘之后才是有意义的等价判据。
    ca, oa = np.array(comp).astype(np.float64), np.array(orig).astype(np.float64)
    c_pre = ca[:, :, :3] * (ca[:, :, 3:4] / 255.0)
    o_pre = oa[:, :, :3] * (oa[:, :, 3:4] / 255.0)
    d = np.abs(c_pre - o_pre).max(axis=2)
    d = np.maximum(d, np.abs(ca[:, :, 3] - oa[:, :, 3]))
    bad = d > 4

    # ── 唯一一处有据可依的豁免 ──
    # 为了盖住腿旋转时露出的缝，身体层和腿层在两处是【故意重叠】的：
    #   · 髋点线到裙摆线之间（腿的向上延长段，压在裙子底下）
    #   · 裙摆线往下 INSET 像素（大腿顶部的静止条，压住旋转的腿）
    # 重叠区里，如果一个像素本身是抗锯齿的半透明边缘，两层都会被合成一次，
    # alpha 变成 a + a(1-a) —— 比原图略实一点点，肉眼不可见。
    #
    # 豁免条件直接照问题本质写，而不是框一个几何范围：
    #   两层都对该像素有贡献 且 原图该像素是半透明边缘。
    # 这样两处重叠自动都覆盖到，而且**不可能掩盖真的错位** ——
    # 层一旦挪错位置，差异会落在不透明像素上（那里两层不会同时有贡献）。
    body_a = np.array(body)[:, :, 3].astype(np.int16)
    leg_a = np.maximum(np.array(leg_l)[:, :, 3], np.array(leg_r)[:, :, 3]).astype(np.int16)
    overlap_edge = (body_a > 0) & (leg_a > 0) & (oa[:, :, 3] < 255)
    n_edge = int((bad & overlap_edge).sum())
    real = int((bad & ~overlap_edge).sum())

    if real == 0:
        note = "零角度逐像素还原原图 ✓（最大差 %.1f）" % d.max()
        if n_edge:
            note += "；%d 个抗锯齿边缘像素落在两层故意重叠区，被叠了一次，不可见" % n_edge
        return True, note
    return False, ("零角度和原图差 %d 个像素（最大差 %.1f）—— 层的位置/z 序不对"
                   % (real, d[bad & ~overlap_edge].max()))


def check_extension(body, leg_l, leg_r, meta):
    """延长区域是否被身体完全盖住"""
    hem, hip = meta["hem_y"], meta["hip_y"]
    ba = np.array(body)[:, :, 3]
    naked = 0
    for layer in (leg_l, leg_r):
        la = np.array(layer)[:, :, 3]
        # 延长区 = 有腿像素 且 在裙摆线以上
        ext = np.zeros_like(la, bool)
        ext[hip:hem, :] = la[hip:hem, :] > ALPHA_TH
        naked += int((ext & (ba <= ALPHA_TH)).sum())
    if naked == 0:
        return True, "延长区被身体完全盖住 ✓"
    return False, "延长区有 %d 个像素露在身体外面 —— 转起来会看到凭空冒出的肉色块" % naked


# ════════════════════════════════════════════════════════════
# 合成
# ════════════════════════════════════════════════════════════

def compose(body, leg_l, leg_r, meta, ang_l, ang_r, bob):
    """腿在下、身体在上；腿绕各自的髋点旋转"""
    W, H = body.size
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # 先画两条腿（旋转中心 = 髋点）
    for layer, ang, key in ((leg_l, ang_l, "leg_left"), (leg_r, ang_r, "leg_right")):
        p = meta[key]
        rot = layer.rotate(ang, resample=Image.BICUBIC, center=(p["x"], p["y"]), expand=False)
        canvas = Image.alpha_composite(canvas, rot)

    # 身体在最上，带一点上下起伏
    if bob:
        b = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        b.paste(body, (0, int(round(bob))), body)
        canvas = Image.alpha_composite(canvas, b)
    else:
        canvas = Image.alpha_composite(canvas, body)

    return canvas


def walk_cycle(meta, swing_deg, n, mode="step"):
    """一个完整步态循环的 n 个相位。

    ⚠️ 两条腿的相位关系决定了"看起来像什么"，这里试过两种：

    splay（反向）: ang_l=+s·sin, ang_r=-s·sin
        两条腿朝相反方向转 → 双脚并拢 ↔ 双脚分开。
        读起来是【开合跳】，不是走路。留着是为了对比。

    step（同向轮流）: 每条腿只在半个周期里朝同一个方向迈出去再收回来
        左腿先迈 → 复位 → 右腿再迈。读起来是【原地踏步】。
        窗口本身在横移（主进程干的），所以原地踏步 + 窗口位移 = 走路。

    {n} 帧里身体起伏两次（每迈一步一次）。
    """
    out = []
    for i in range(n):
        ph = i / float(n)
        s = np.sin(2 * np.pi * ph)
        if mode == "splay":
            ang_l, ang_r = swing_deg * s, -swing_deg * s
        else:  # step
            # max(0,·) 让每条腿"迈出去再收回来"，半个周期走完一个来回
            ang_l = swing_deg * max(0.0, s)
            ang_r = swing_deg * max(0.0, -s)
        bob = -abs(s) * meta.get("bob_px", 7)
        out.append((ph, ang_l, ang_r, bob))
    return out


# ════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="分层 rig 走路预览")
    ap.add_argument("--摆幅", dest="swing", type=float, default=9.0, help="腿摆动角度（度）")
    ap.add_argument("--帧数", dest="n", type=int, default=8, help="一个循环几帧")
    ap.add_argument("--起伏", dest="bob", type=float, default=7.0, help="身体上下起伏像素")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--模式", dest="mode", default="step", choices=["step", "splay"],
                    help="步态：step=原地踏步（默认）/ splay=双向开合（像开合跳，用于对比）")
    ap.add_argument("--保持", dest="keep", action="store_true", help="跳过校验直接出图")
    args = ap.parse_args()

    need = ["body.png", "leg_left.png", "leg_right.png", "参数.json"]
    missing = [f for f in need if not os.path.exists(os.path.join(RIG, f))]
    if missing:
        print("✗ 缺少 %s，先跑：python 拆腿.py" % "、".join(missing))
        return 1

    body = Image.open(os.path.join(RIG, "body.png")).convert("RGBA")
    leg_l = Image.open(os.path.join(RIG, "leg_left.png")).convert("RGBA")
    leg_r = Image.open(os.path.join(RIG, "leg_right.png")).convert("RGBA")
    with open(os.path.join(RIG, "参数.json"), "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["bob_px"] = args.bob

    src = resolve_src(meta)
    print("rig: %dx%d  裙摆线 y=%d  髋点线 y=%d"
          % (meta["canvas_w"], meta["canvas_h"], meta["hem_y"], meta["hip_y"]))
    _crop = meta.get("source_crop")
    print("校验1 比对用的源图: %s%s（%dx%d）"
          % (meta.get("source_path") or os.path.basename(DEFAULT_SRC),
             "，已按 source_crop 裁到角色框" if _crop else "",
             src.width, src.height))

    if not args.keep:
        ok1, msg1 = check_rest(body, leg_l, leg_r, meta, src)
        ok2, msg2 = check_extension(body, leg_l, leg_r, meta)
        print("  [校验1] %s" % msg1)
        print("  [校验2] %s" % msg2)
        if not (ok1 and ok2):
            print("\n✗ 校验没过，先别接进桌宠。看上面的提示调整 --摆线/--内缩 后重跑 拆腿.py")
            return 1
        print()

    os.makedirs(PREVIEW, exist_ok=True)
    phases = walk_cycle(meta, args.swing, args.n, args.mode)
    frames = [compose(body, leg_l, leg_r, meta, al, ar, bob) for _, al, ar, bob in phases]

    # ── 拼版 ──
    TH = 300
    scale = TH / float(meta["canvas_h"])
    tw = int(round(meta["canvas_w"] * scale))
    GAP, LAB = 10, 26
    W = GAP + len(frames) * (tw + GAP)
    H = LAB + GAP + TH + GAP
    sheet = Image.new("RGB", (W, H), (250, 250, 252))
    font = load_font(14)
    for i, f in enumerate(frames):
        x = GAP + i * (tw + GAP)
        board = checkerboard(tw, TH)
        board.paste(f.resize((tw, TH), Image.LANCZOS), (0, 0),
                    f.resize((tw, TH), Image.LANCZOS))
        sheet.paste(board, (x, LAB + GAP))
        ImageDraw.Draw(sheet).text((x + 4, LAB + 2), "#%d" % i, fill=(40, 40, 45), font=font)
    sheet_dst = os.path.join(PREVIEW, "rig走路_%s_拼版.png" % args.mode)
    sheet.save(sheet_dst)

    # ── 动图 ──
    gh = 340
    gs = gh / float(meta["canvas_h"])
    gw = int(round(meta["canvas_w"] * gs))
    gif_frames = []
    for f in frames:
        t = f.resize((gw, gh), Image.LANCZOS)
        b = checkerboard(gw, gh)
        b.paste(t, (0, 0), t)
        gif_frames.append(b.convert("P", palette=Image.ADAPTIVE, colors=255))
    gif_dst = os.path.join(PREVIEW, "rig走路_%s_%d度_%dfps.gif" % (args.mode, int(args.swing), int(args.fps)))
    gif_frames[0].save(gif_dst, save_all=True, append_images=gif_frames[1:],
                       duration=int(round(1000.0 / args.fps)), loop=0, disposal=2)

    print("模式 %s，摆幅 %.0f 度，%d 帧，起伏 %.0fpx" % (args.mode, args.swing, args.n, args.bob))
    print("  拼版 → %s" % sheet_dst)
    print("  动图 → %s" % gif_dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
