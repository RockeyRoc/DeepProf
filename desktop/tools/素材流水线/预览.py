# -*- coding: utf-8 -*-
"""动作帧预览 —— 接进播放器之前的最后一道关。

为什么要单独一步：
    归一化脚本只能保证"数值上对齐了"，保不了"看起来像在走路"。
    步态对不对、有没有跳帧、边缘有没有白边、循环接不接得上 ——
    这些只能靠眼睛。先看预览，再决定接不接进桌宠，省得改半天 JS 发现素材本身不行。

产出两样东西（都在 动作帧/预览/ 下）：
    <动作>_拼版.png      横向铺开所有帧，画上基线与中心线 —— 查对齐、查步态连续性
    <动作>_<帧率>fps.gif  按目标帧率循环播放 —— 查"动起来像不像走路"
                                           棋盘格底，顺便暴露白边/透明没抠干净

用法：
    python 预览.py walk
    python 预览.py                # 所有已归一化的动作
    python 预览.py walk --fps 6   # 临时换个帧率看
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
FRAME_ROOT = os.path.join(HERE, "动作帧")
OUT_DIR = os.path.join(FRAME_ROOT, "归一化")
PREVIEW_DIR = os.path.join(FRAME_ROOT, "预览")
SPEC_FILE = os.path.join(OUT_DIR, "_规格.json")

# 帧率要跟桌宠里的实际播放帧率一致，否则预览会骗你。
# 这里的值对应 desktop/src/renderer/src/petAnimations.js —— 改那边记得改这边。
FPS = {
    "idle": 2,
    "blink": 1,
    "walk": 8,
    "talk": 6,
    "think": 2,
    "sad": 2,
    "tap": 12,
    "happy": 8,
    "quiz": 2,
}
DEFAULT_FPS = 8

THUMB_H = 340     # 拼版里每帧的高度
GAP = 14
LABEL_H = 30
CHECKER = 16


def natural_key(name):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def load_font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def checkerboard(w, h, size=CHECKER):
    """棋盘格底 —— 透明区域在这种底上一眼能看出来，白边也藏不住"""
    yy, xx = np.mgrid[0:h, 0:w]
    tile = ((yy // size) + (xx // size)) % 2
    val = np.where(tile == 0, 236, 206).astype(np.uint8)
    return Image.fromarray(np.dstack([val, val, val]), "RGB")


def on_checker(frame, w, h):
    board = checkerboard(w, h)
    board.paste(frame, (0, 0), frame)
    return board


# ════════════════════════════════════════════════════════════
# 拼版
# ════════════════════════════════════════════════════════════

def make_sheet(action, frames, cw, ch, baseline_frac, fps, dst):
    scale = THUMB_H / float(ch)
    tw, th = int(round(cw * scale)), THUMB_H
    n = len(frames)

    W = GAP + n * (tw + GAP)
    H = LABEL_H + GAP + th + GAP + 24
    sheet = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(sheet)
    font = load_font(15)
    small = load_font(13)

    base_y = LABEL_H + GAP + int(round(th * baseline_frac))

    for i, f in enumerate(frames):
        x = GAP + i * (tw + GAP)
        y = LABEL_H + GAP

        # ⚠️ 必须先缩放到格子大小再贴 —— 直接把整张画布贴进小格子，
        #    等于只显示了画布左上角一块，看着像对齐错了，其实是对齐好的
        thumb = on_checker(f.resize((tw, th), Image.LANCZOS), tw, th)
        sheet.paste(thumb, (x, y))
        d.rectangle([x, y, x + tw - 1, y + th - 1], outline=(190, 190, 195))

        # 脚底线（红）——所有帧应该踩在同一条线上
        d.line([x, base_y, x + tw, base_y], fill=(230, 40, 40), width=2)
        # 中心线（蓝虚线）——头心应该都落在这条线上
        cx = x + int(round(tw * 0.5))
        for yy in range(y, y + th, 10):
            d.line([cx, yy, cx, min(yy + 5, y + th)], fill=(40, 110, 230), width=1)

        d.text((x + 4, LABEL_H + 6), "%s  #%d" % (action, i), fill=(40, 40, 45), font=font)

    d.text((GAP, H - 20),
           "红线=脚底线（该重合）  蓝虚线=中心线（头心该重合）  %d 帧 @ %gfps"
           % (n, fps),
           fill=(90, 90, 95), font=small)

    sheet.save(dst)
    return dst


# ════════════════════════════════════════════════════════════
# 动图
# ════════════════════════════════════════════════════════════

def make_gif(frames, cw, ch, fps, dst, gif_h=360):
    scale = gif_h / float(ch)
    tw, th = int(round(cw * scale)), gif_h
    delay = max(20, int(round(1000.0 / fps)))

    out = []
    for f in frames:
        thumb = f.resize((tw, th), Image.LANCZOS)
        out.append(on_checker(thumb, tw, th).convert("P", palette=Image.ADAPTIVE, colors=255))

    out[0].save(dst, save_all=True, append_images=out[1:],
                duration=delay, loop=0, disposal=2, optimize=False)
    return dst


# ════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════

def preview_action(action, spec, fps_override=None):
    d = os.path.join(OUT_DIR, action)
    if not os.path.isdir(d):
        return False

    files = [os.path.join(d, f) for f in sorted(os.listdir(d), key=natural_key)
             if f.lower().endswith(".png")]
    if not files:
        print("  [%s] 没有归一化结果" % action)
        return False

    frames = [Image.open(p).convert("RGBA") for p in files]
    cw, ch = frames[0].size
    bad = [os.path.basename(files[i]) for i, f in enumerate(frames) if f.size != (cw, ch)]
    if bad:
        print("  ⚠️ 这些帧尺寸和第一帧不一致，归一化没跑干净：%s" % "、".join(bad))

    fps = fps_override or FPS.get(action, DEFAULT_FPS)
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    sheet = make_sheet(action, frames, cw, ch, spec.get("baseline_frac", 0.92), fps,
                       os.path.join(PREVIEW_DIR, "%s_拼版.png" % action))
    gif = make_gif(frames, cw, ch, fps,
                   os.path.join(PREVIEW_DIR, "%s_%dfps.gif" % (action, int(round(fps)))))

    print("  [%s] %d 帧 @ %gfps" % (action, len(frames), fps))
    print("        拼版 → %s" % sheet)
    print("        动图 → %s" % gif)
    if len(frames) < 2:
        print("        ⚠️ 只有 1 帧，动图看不出动画效果")
    return True


def main():
    ap = argparse.ArgumentParser(description="动作帧预览")
    ap.add_argument("action", nargs="?", help="动作名，不填=全部")
    ap.add_argument("--fps", type=float, help="覆盖帧率")
    args = ap.parse_args()

    spec = {}
    if os.path.exists(SPEC_FILE):
        import json
        with open(SPEC_FILE, "r", encoding="utf-8") as f:
            spec = json.load(f)

    if args.action:
        actions = [args.action]
    else:
        actions = sorted([x for x in os.listdir(OUT_DIR)
                          if os.path.isdir(os.path.join(OUT_DIR, x))]) if os.path.isdir(OUT_DIR) else []

    if not actions:
        print("还没有任何归一化结果，先跑：python 归一化帧.py")
        return 1

    for a in actions:
        if not preview_action(a, spec, args.fps):
            print("  [%s] 跳过" % a)

    print("\n预览目录：%s" % PREVIEW_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
