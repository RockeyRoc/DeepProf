# -*- coding: utf-8 -*-
"""动作帧归一化 —— 把 AI 生成的几张动作图，处理成"能直接播"的一套帧。

═══ 为什么这一步不是可选的 ═══

AI 每次生成，角色的大小、位置、脚底高度都不一样。直接丢进播放器，
角色会一帧一跳 —— 看起来像在抽搐，比静态图还糟。
参考项目 dsh-dfy 就是因为没做归一化、锚点和步态连续性全丢，整批帧作废重生成。

═══ 本脚本做四件事 ═══

  1. 去背景 + 白底去污染（已经是透明 PNG 就跳过）
  2. 所有帧用【同一个】缩放系数 —— 这一点很关键：
     不能逐帧缩放到同一高度，那会把走路时自然的上下起伏抹掉，人就变成纸片了
  3. 水平方向按【头部中心】对齐，不是按外接矩形中心 ——
     走路时腿在前后伸，外接矩形宽度一直在变，按它对齐角色会左右抖
  4. 脚底对齐到同一条基线

═══ 用法 ═══

    python 归一化帧.py walk            处理 walk
    python 归一化帧.py                 处理 原始/ 下所有动作
    python 归一化帧.py walk --接入      处理完顺带拷进桌宠的素材目录
    python 归一化帧.py walk --调试      额外输出一张检测结果调试图

原图放：  _素材工作区/动作帧/原始/<动作名>/*.png
成品出：  _素材工作区/动作帧/归一化/<动作名>/*.png
"""

import argparse
import json
import os
import re
import shutil
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
FRAME_ROOT = os.path.join(HERE, "动作帧")
RAW_DIR = os.path.join(FRAME_ROOT, "原始")
OUT_DIR = os.path.join(FRAME_ROOT, "归一化")
PREVIEW_DIR = os.path.join(FRAME_ROOT, "预览")
SPEC_FILE = os.path.join(OUT_DIR, "_规格.json")

# 桌宠工程里的素材目录（_素材工作区 的上一级就是 DeepProf）
PET_FRAMES = os.path.join(
    os.path.dirname(HERE), "desktop", "src", "renderer", "src", "assets", "pet", "frames"
)

# ── 去背景参数 ──────────────────────────────────────────────
# 即梦出图的背景是纯白。分两档阈值：
#   > CORE : 铁定是背景
#   > SOFT : 可能和背景混在一起（抗锯齿边缘），交给去污染那步处理
BG_CORE = 246
BG_SOFT = 188

ALPHA_TH = 8        # 判定"这里有像素"的 alpha 阈值
HEAD_FRAC = 0.30    # 头部高度占角色高度的比例（取水平锚点用）

# ── 画布规格（所有动作共用一套，否则切动作时角色会忽然变大变小）──
DEFAULT_SPEC = {
    # ⚠️ 这两个值直接决定【观感清晰度】，实测对比过：
    #    目标高 620 → 角色在屏幕上明显发虚（眼睛高光、线条都糊）；
    #    1200 → 锐利得多；再往上到 2190 提升很小，说明 1200 已经到平台。
    #    代价是单张 PNG 从 278KB 涨到约 930KB（角色只占画布约 20% 面积，
    #    PNG 压得很狠，所以涨得没有面积比那么多）。
    "canvas_w": 1536,
    "canvas_h": 1536,
    "target_h": 1200,     # 角色站直时的高度（像素）
    "baseline_frac": 0.92,  # 脚底落在画布高度的百分之几处
    "head_frac": HEAD_FRAC,
}

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ════════════════════════════════════════════════════════════
# 小工具
# ════════════════════════════════════════════════════════════

def natural_key(name):
    """walk_2 排在 walk_10 前面（普通字符串排序会把 10 排到 2 前面）"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def fmt_size(n):
    return "%.1fKB" % (n / 1024.0) if n < 1024 * 1024 else "%.1fMB" % (n / 1048576.0)


# ════════════════════════════════════════════════════════════
# 1. 去背景
# ════════════════════════════════════════════════════════════

def already_transparent(alpha):
    """即梦如果直接导出透明 PNG，就别再抠一遍（会把白衬衫白袜子抠穿）"""
    return alpha.min() == 0 and (alpha == 0).mean() > 0.10


def strip_background(img):
    """白底 → 透明。

    用连通域而不是洪水填充：只有【和画布边缘连通】的浅色区域才算背景。
    这样角色身上的白色（衬衫、过膝袜）不会被误抠成透明。

    :returns: (RGBA 图, 说明字符串)
    """
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    alpha = arr[:, :, 3]

    if already_transparent(alpha):
        return rgba, "本来就是透明图，跳过抠图"

    rgb = arr[:, :, :3].astype(np.int16)
    lightness = rgb.min(axis=2)  # 最暗通道都接近 255 → 是白/浅灰

    # --- 找出和边缘连通的浅色区域 ---
    soft = lightness > BG_SOFT
    labels, n = ndimage_label(soft)

    border_labels = set()
    for edge in (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]):
        border_labels.update(int(v) for v in np.unique(edge) if v != 0)
    if not border_labels:
        return rgba, "⚠️ 没找到白底（背景可能不是纯白），原样保留"

    is_bg = np.isin(labels, list(border_labels))

    # --- alpha：背景 0，其余 255 ---
    out_alpha = np.where(is_bg, 0, 255).astype(np.uint8)

    # --- 边缘羽化：让那把 0/255 的硬边柔一点，不然缩放到桌宠尺寸会有锯齿 ---
    soft_alpha = feather(out_alpha)

    # --- 白底去污染：边缘像素是"角色色 × 白底"混出来的，偏白。
    #     已知观测色 = a·前景 + (1-a)·白，反解出前景色，白边就没了。---
    out_rgb = decontaminate(arr[:, :, :3].astype(np.float32), soft_alpha)

    out = np.dstack([np.clip(out_rgb, 0, 255).astype(np.uint8), soft_alpha])
    return Image.fromarray(out, "RGBA"), "抠图完成（连通域 + 去白边）"


def drop_fragments(img, keep_ratio=0.05):
    """去掉游离的小碎块，只留角色本体。

    ⚠️ 为什么需要：AI 出的图（尤其从四格图切出来的）经常带
    邻格的一小块残片、或者零星的噪点。它们在 8fps 的循环里会一闪一闪，
    比不放还糟。而且归一化是按【外接框】对齐的 —— 一个远处的碎块
    会把外接框撑大，把整帧缩放带偏。

    规则：只保留最大的连通域，以及大于它 keep_ratio 的那些
    （头发和身体偶尔会被细线分开，不能一刀切成"只留一个"）。
    """
    from scipy import ndimage
    a = np.array(img)[:, :, 3]
    lab, n = ndimage.label(a > ALPHA_TH)
    if n <= 1:
        return img, 0
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0  # 背景不算
    biggest = sizes.max()
    drop = np.isin(lab, [i for i in range(1, n + 1) if sizes[i] < biggest * keep_ratio])
    k = int(drop.sum())
    if not k:
        return img, 0
    out = np.array(img).copy()
    out[drop] = (0, 0, 0, 0)
    return Image.fromarray(out, "RGBA"), k


def feather(alpha_u8):
    """对 0/255 的硬掩码做一次极轻的模糊，得到抗锯齿边缘"""
    m = Image.fromarray(alpha_u8, "L").filter(ImageFilter.GaussianBlur(0.6))
    return np.array(m)


def decontaminate(rgb, alpha):
    """白底反解：Fg = (观测 - (1-a)·白) / a

    a=1 的像素原样保留；半透明的边缘像素被推向饱和，白边消失。
    这是抠图的标准收尾步骤，不做的话角色周围会有一圈灰白毛边。
    """
    a = (alpha.astype(np.float32) / 255.0)[:, :, None]
    safe = np.clip(a, 0.15, 1.0)
    fg = (rgb - (1.0 - a) * 255.0) / safe
    # 只动半透明区域，实心区域保持原样（避免把正常颜色改脏）
    blend = ((a > 0.02) & (a < 0.98)).astype(np.float32)
    return rgb * (1 - blend) + fg * blend


def ndimage_label(mask):
    """连通域标记。scipy 是 C 实现，比 PIL 的 floodfill 快几十倍"""
    from scipy import ndimage
    return ndimage.label(mask)


# ════════════════════════════════════════════════════════════
# 2. 量尺寸：脚底、头心
# ════════════════════════════════════════════════════════════

def measure(img):
    """量一张帧的对齐锚点

    :returns: dict(bbox, w, h, feet_y, head_cx) 或 None（整张透明）
    """
    a = np.array(img.convert("RGBA"))[:, :, 3]
    ys, xs = np.nonzero(a > ALPHA_TH)
    if len(xs) == 0:
        return None

    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    h = y1 - y0

    # 头部带 = 外接矩形最上面 30%
    band = a[y0:y0 + max(1, int(h * HEAD_FRAC)), x0:x1]
    cols = np.nonzero((band > ALPHA_TH).any(axis=0))[0]
    head_cx = x0 + (int(cols.min()) + int(cols.max()) + 1) / 2.0 if len(cols) else (x0 + x1) / 2.0

    # 身体高度 = 头部中心那几列里的不透明纵向跨度。
    # ⚠️ 为什么要单独量：外接框把【尾巴】也算进去，而侧视走路时尾巴的伸缩
    #    跟身体不同步 —— 拿框高做帧间校正，校正不到点上（实测框高差压到 1.4%，
    #    身体高差还有 6.2%）。
    band_w = max(20, int((x1 - x0) * 0.35))
    bx0 = max(0, int(head_cx - band_w / 2))
    band = a[:, bx0:min(a.shape[1], bx0 + band_w)]
    bys = np.nonzero((band > ALPHA_TH).any(axis=1))[0]
    body_h = int(bys.max() - bys.min() + 1) if len(bys) else h

    return {
        "bbox": (x0, y0, x1, y1),
        "w": x1 - x0,
        "h": h,
        "body_h": body_h,  # 排除尾巴的身体高度（帧间校正用它）
        "feet_y": y1,      # 脚底 = 外接矩形下沿
        "head_cx": head_cx,
    }


# ════════════════════════════════════════════════════════════
# 3. 归一化一个动作
# ════════════════════════════════════════════════════════════

def union_box(d):
    """量一批成品的【并集】外接框（画布坐标）。

    渲染端必须知道角色在画布里的实际位置，否则会按【整张画布】缩放：
    画布是 768×768 的方形，而桌宠窗口是 220×300 的窄高形 ——
    按画布缩放会变成"宽度受限"，角色白白小掉四成。
    """
    xs0 = ys0 = 10 ** 9
    xs1 = ys1 = -1
    for f in os.listdir(d):
        if not f.lower().endswith(".png"):
            continue
        a = np.array(Image.open(os.path.join(d, f)).convert("RGBA"))[:, :, 3]
        ys, xs = np.nonzero(a > ALPHA_TH)
        if len(xs) == 0:
            continue
        xs0, xs1 = min(xs0, int(xs.min())), max(xs1, int(xs.max()))
        ys0, ys1 = min(ys0, int(ys.min())), max(ys1, int(ys.max()))
    if xs1 < 0:
        return None
    return (xs0, ys0, xs1 + 1, ys1 + 1)


def collect_raw(action):
    d = os.path.join(RAW_DIR, action)
    if not os.path.isdir(d):
        return []
    files = [f for f in os.listdir(d) if f.lower().endswith(IMG_EXT)]
    files.sort(key=natural_key)
    return [os.path.join(d, f) for f in files]


def normalize_action(action, spec, mode="表情", want_debug=False):
    files = collect_raw(action)
    if not files:
        print("  [%s] 原始目录是空的：%s" % (action, os.path.join(RAW_DIR, action)))
        return False

    out_dir = os.path.join(OUT_DIR, action)
    os.makedirs(out_dir, exist_ok=True)

    print("  [%s] 读到 %d 张原图" % (action, len(files)))

    # ── 第一步：全部去背景，并量出锚点 ──
    loaded = []
    for p in files:
        img = Image.open(p)
        img, note = strip_background(img)
        img, dropped = drop_fragments(img)
        if dropped:
            note += "；清掉游离碎块 %d 像素" % dropped
        m = measure(img)
        if m is None:
            print("      ✗ %s 整张都是透明的，跳过（抠图抠穿了？）" % os.path.basename(p))
            continue
        loaded.append((p, img, m))
        print("      %-22s %4dx%-4d  高%4d  脚底y=%4d  头心x=%6.1f   %s"
              % (os.path.basename(p), img.width, img.height, m["h"], m["feet_y"], m["head_cx"], note))

    if not loaded:
        print("      ✗ 没有可用帧")
        return False

    # ── 第二步：算缩放系数 ──
    #
    # ⚠️ 这里有两种【相反】的需求，用 mode 区分，别搞混：
    #
    # 逐帧（walk/idle/blink/talk…）：整批【共用同一个】系数。
    #     因为走路时角色本来就会上下起伏（腿分开时矮一点、并拢时高一点），
    #     逐张缩放到同高会把这起伏抹平 —— 人就变成纸片了。
    #
    # 表情（9 张立绘）：【逐张】缩放到同一高度。
    #     它们是各自单独显示的，不存在起伏这回事；反而必须"张张一样大"，
    #     否则切表情时角色会忽大忽小。
    #
    # 用中位数而不是平均值：万一一两张生成歪了，不会把整批带偏
    heights = np.array([m["h"] for _, _, m in loaded], dtype=np.float64)
    median_h = float(np.median(heights))

    # 帧间高度极差（阻尼校正和警告都要用，所以先算）
    spread = (heights.max() - heights.min()) / median_h

    if mode == "逐帧":
        # ── 阻尼校正 ──
        # 逐帧模式原本是"整批共用一个系数"，保住走路时自然的上下起伏。
        # 但实测走路的四帧高度差到了 **7.9%**（腿分开那帧 1136、并拢那帧 1226）——
        # 8fps 下这就是"一胀一缩"，看起来像在变大变小，而不是在走路。
        # 一般走路的上下起伏在 3~5%，7.9% 太夸张。
        #
        # 折中：把每帧的高度往中位数【拉 70%】，只保留 30% 的起伏。
        #   全拉平 → 走路该有的起伏没了，人变纸片
        #   全不拉 → 一胀一缩
        # 70/30 是折中，实测能把 7.9% 压到约 2.4%。
        DAMP = 0.7
        body_hs = np.array([m.get("body_h", m["h"]) for _, _, m in loaded], dtype=np.float64)
        body_med = float(np.median(body_hs))
        scales = []
        for _, _, m in loaded:
            bh = float(m.get("body_h", m["h"]))
            # 直接算"我想要渲染出来的身体高度是多少"，再除以原始身体高度得到系数：
            #   想要的高度 = target_h × (该帧身体/中位身体) ^ (1-DAMP)
            #   DAMP=0.7 表示"偏离中位的部分只保留 30%"
            want = spec["target_h"] * ((bh / body_med) ** (1.0 - DAMP))
            scales.append(want / bh)
        print("      身体高度 %d~%d（差 %.1f%%）→ 阻尼后 %.1f%%"
              % (body_hs.min(), body_hs.max(),
                 (body_hs.max() - body_hs.min()) / body_med * 100,
                 (body_hs.max() - body_hs.min()) / body_med * (1 - DAMP) * 100))
        print("      逐帧模式（阻尼 %.0f%%）：中位高度 %dpx → 目标 %dpx，帧间起伏已压到约 %.1f%%"
              % (DAMP * 100, median_h, spec["target_h"], spread * (1 - DAMP) * 100))
    else:
        scales = [spec["target_h"] / float(m["h"]) for _, _, m in loaded]
        print("      表情模式：逐张缩放到目标高度 %dpx（每张系数不同）" % spec["target_h"])

    if spread > 0.10:
        print("      ⚠️ 帧间高度差 %.0f%%（%d~%d px）—— 各帧角色大小/姿态差异偏大。" % (spread * 100, heights.min(), heights.max()))
        if mode == "逐帧":
            print("         走路帧的自然起伏正常在 5% 以内。超过 10% 建议重出图，否则播起来会一耸一耸。")
        else:
            print("         表情模式下会被逐张缩放拉平，但头身比修不了 —— 头明显偏大的那张会露馅。")

    head_cxs = np.array([m["head_cx"] for _, _, m in loaded], dtype=np.float64)
    # 头心 x 相对于各帧外接矩形左沿的偏移，跨帧应该基本一致
    # 缩放系数现在是逐张各一个（表情模式），取中位数作为代表来换算像素漂移
    drift = (head_cxs.max() - head_cxs.min()) * float(np.median(scales))
    if drift > spec["canvas_w"] * 0.04:
        print("      ⚠️ 各帧头部水平位置相差 %.0fpx（缩放后）—— 对齐后角色会左右晃。" % drift)

    if min(scales) > 1.02:
        print("      ⚠️ 这是在【放大】—— 原图角色只有 %dpx 高，硬拉到 %dpx 会糊。"
              % (median_h, spec["target_h"]))
        print("         首选：让即梦出更大的图（角色至少 1024px 高）。")
        print("         次选：调小目标高度重跑 —— 但规格是全局的，")
        print("               改了一个动作会让它和别的动作大小对不上。")

    # ── 第三步：统一画布 + 对齐 ──
    cw, ch = spec["canvas_w"], spec["canvas_h"]
    baseline = int(round(ch * spec["baseline_frac"]))
    cx = cw / 2.0

    for i, (p, img, m) in enumerate(loaded):
        # 逐帧模式整批同一个系数；表情模式每张各算各的（见上面的说明）
        scale = scales[i]
        nw = max(1, int(round(img.width * scale)))
        nh = max(1, int(round(img.height * scale)))
        sc = img.resize((nw, nh), Image.LANCZOS)
        if sc.mode != "RGBA":
            sc = sc.convert("RGBA")

        # 缩放会带来取整误差，锚点重新量一遍，比拿旧值乘系数准
        m2 = measure(sc)
        if m2 is None:
            print("      ✗ %s 缩放后变空了，跳过" % os.path.basename(p))
            continue

        off_x = int(round(cx - m2["head_cx"]))
        off_y = int(round(baseline - m2["feet_y"]))

        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        canvas.paste(sc, (off_x, off_y), sc)

        # 出界检查：宁可吵一句，也别默默交出一张被裁掉脚的图
        clipped = []
        if off_x < 0:
            clipped.append("左%+d" % off_x)
        if off_y < 0:
            clipped.append("上%+d" % off_y)
        if off_x + nw > cw:
            clipped.append("右%+d" % (off_x + nw - cw))
        if off_y + nh > ch:
            clipped.append("下%+d" % (off_y + nh - ch))
        if clipped:
            print("      ⚠️ %s 超出画布（%s）—— 角色被裁了，建议调大 target_h 或画布"
                  % (os.path.basename(p), " ".join(clipped)))

        dst = os.path.join(out_dir, os.path.splitext(os.path.basename(p))[0] + ".png")
        canvas.save(dst)

        if want_debug and p is loaded[0][0]:
            save_debug(sc, m2, canvas, off_x, off_y, spec,
                       os.path.join(PREVIEW_DIR, "%s_检测调试.png" % action))

    # ── 第四步：记录"角色在画布里的实际位置"给渲染端 ──
    box = union_box(out_dir)
    if box:
        geo = {
            "canvas_w": cw,
            "canvas_h": ch,
            "char_x0": box[0], "char_y0": box[1],
            "char_x1": box[2], "char_y1": box[3],
            "_说明": ("角色在画布里的外接框（画布坐标）。渲染端必须用它来适配窗口大小和锚点 —— "
                      "别按整张画布缩放：画布是方的、桌宠窗口是窄高的，按画布缩放会宽度受限，"
                      "角色会白白小掉四成。"),
        }
        with open(os.path.join(out_dir, "_几何.json"), "w", encoding="utf-8") as f:
            json.dump(geo, f, ensure_ascii=False, indent=2)
        print("      角色外接框 x[%d,%d] y[%d,%d]（占总高 %.0f%%）→ 已写 _几何.json"
              % (box[0], box[2], box[1], box[3], (box[3] - box[1]) / float(ch) * 100))

    print("      ✓ 输出 %d 张 → %s" % (len(loaded), out_dir))
    return True


def save_debug(sc, m2, canvas, off_x, off_y, spec, dst):
    """画一张调试图：外接矩形 / 头部带 / 基线 / 中心线。
    如果哪天对齐结果不对，看这张图就能知道脚本量到哪去了。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    vis = sc.copy()
    d = ImageDraw.Draw(vis)
    x0, y0, x1, y1 = m2["bbox"]
    d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 0, 0, 255), width=2)
    band_y = y0 + int(m2["h"] * spec["head_frac"])
    d.rectangle([x0, y0, x1 - 1, band_y], outline=(0, 160, 255, 255), width=2)
    d.line([m2["head_cx"], y0, m2["head_cx"], y1], fill=(0, 200, 0, 255), width=2)
    d.line([0, m2["feet_y"], vis.width, m2["feet_y"]], fill=(255, 120, 0, 255), width=3)

    board = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    board.paste(vis, (off_x, off_y), vis)
    d2 = ImageDraw.Draw(board)
    d2.line([0, int(canvas.height * spec["baseline_frac"]), canvas.width,
             int(canvas.height * spec["baseline_frac"])], fill=(255, 0, 0, 255), width=2)
    d2.line([canvas.width // 2, 0, canvas.width // 2, canvas.height], fill=(0, 120, 255, 255), width=2)
    board.save(dst)


# ════════════════════════════════════════════════════════════
# 4. 接入桌宠素材目录
# ════════════════════════════════════════════════════════════

def install(action):
    src = os.path.join(OUT_DIR, action)
    if not os.path.isdir(src):
        print("  ✗ 还没有归一化结果：%s" % src)
        return
    dst = os.path.join(PET_FRAMES, action)
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(src), key=natural_key):
        if f.lower().endswith(".png"):
            shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
            n += 1
    print("  ✓ 已拷进桌宠素材目录 %d 张 → %s" % (n, dst))
    print("    下一步：在 petAnimations.js 里把这个动作的 frames 数组填上（路径 assets/pet/frames/%s/xxx.png）" % action)


# ════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════

def load_spec():
    if os.path.exists(SPEC_FILE):
        with open(SPEC_FILE, "r", encoding="utf-8") as f:
            spec = json.load(f)
        for k, v in DEFAULT_SPEC.items():
            spec.setdefault(k, v)
        return spec, True
    return dict(DEFAULT_SPEC), False


def main():
    ap = argparse.ArgumentParser(description="动作帧归一化")
    ap.add_argument("action", nargs="?", help="动作名（walk / idle / talk …），不填=处理全部")
    ap.add_argument("--接入", dest="install_it", action="store_true", help="处理完拷进桌宠素材目录")
    ap.add_argument("--调试", dest="debug", action="store_true", help="额外输出检测结果调试图")
    ap.add_argument("--模式", dest="mode", default="表情", choices=["表情", "逐帧"],
                    help="表情=逐张缩放到同高（9张立绘用）；逐帧=整批共用一个缩放系数（走路/待机等动画帧用）")
    ap.add_argument("--target", type=int, help="覆盖角色目标高度（像素）")
    args = ap.parse_args()

    os.makedirs(PREVIEW_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    spec, existed = load_spec()
    if args.target:
        spec["target_h"] = args.target
        existed = False

    if existed:
        print("规格：沿用 %s（画布 %dx%d，角色高 %d）"
              % (os.path.basename(SPEC_FILE), spec["canvas_w"], spec["canvas_h"], spec["target_h"]))
        print("      ⚠️ 所有动作共用这一套规格，中途别改，改了旧帧就对不上了")
    else:
        print("规格：画布 %dx%d，角色高 %d，脚底线 %.0f%%"
              % (spec["canvas_w"], spec["canvas_h"], spec["target_h"], spec["baseline_frac"] * 100))

    if args.action:
        actions = [args.action]
    else:
        actions = sorted(
            [d for d in os.listdir(RAW_DIR) if os.path.isdir(os.path.join(RAW_DIR, d))]
        ) if os.path.isdir(RAW_DIR) else []

    if not actions:
        print("\n原始目录里一个动作文件夹都没有。先建一个，比如：")
        print("  %s" % os.path.join(RAW_DIR, "walk"))
        print("把即梦导出的走路帧丢进去，命名 walk_0.png / walk_1.png …")
        return 1

    ok_any = False
    for a in actions:
        print("\n[%s]" % a)
        if normalize_action(a, spec, mode=args.mode, want_debug=args.debug):
            ok_any = True

    if ok_any and not existed:
        with open(SPEC_FILE, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2)
        print("\n已记录规格 → %s" % SPEC_FILE)

    if args.install_it:
        print()
        for a in actions:
            install(a)

    print("\n接着跑：python 预览.py %s" % (args.action or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
