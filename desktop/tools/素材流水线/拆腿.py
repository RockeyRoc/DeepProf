# -*- coding: utf-8 -*-
"""把一张平铺的立绘，拆成「身体 / 左腿 / 右腿」三层 —— 单图做腿部分层的 rig。

═══ 为什么走这条路 ═══

逐帧动画（另一条路）要 AI 出 4 张走路图，一致性得靠运气。
分层 rig 只需要**现有这一张图**，腿怎么摆由代码算 —— 零生成风险。

═══ 难点（以及怎么绕过去的）═══

1. **两条腿在裙摆以下是粘连的**（y<690 那段两腿贴在一起），
   连通域分不开 → 改用「垂直切分」：在下方两腿已分开的地方量出分界线，
   往上沿用同一条线。切开的直边会被裙子盖住，看不见。

2. **右侧的鲸尾紧贴着右腿**，会被一并切进腿里 → 用右腿在下方干净区域的
   右边界作为上限，超出部分留给身体层（尾巴归身体）。

3. **裙摆是波浪形的**，下沿不是一条直线 → 光按 y 切会啃掉裙子下缘。
   所以裙摆线附近额外用颜色排除：藏青色的像素留在身体层。

4. **腿绕髋点旋转后，裙摆下沿会露出缝** → 让身体层多保留大腿最上面
   十几像素（INSET），那块是静止的皮肤色，旋转的腿接在下面，接缝是同色皮肤，
   看不出来；同时身体层始终盖住这条缝，不会透出桌面。

═══ 输出 ═══

    动作帧/rig/body.png      身体层（含裙子、尾巴），在腿上，z 序最高
    动作帧/rig/leg_left.png     左腿，绕髋点摆动
    动作帧/rig/leg_right.png     右腿
    动作帧/rig/参数.json     髋点坐标等，渲染端直接读，别手抄
    动作帧/rig/_拆解调试图.png  三层染色叠加，用来肉眼确认拆对了

用法：
    python 拆腿.py
    python 拆腿.py --源 <图片路径> --摆线 55 --内缩 14
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # deepprof/
RIG_DIR = os.path.join(HERE, "动作帧", "rig")
PET_DIR = os.path.join(ROOT, "desktop", "src", "renderer", "src", "assets", "pet")

# ⚠️ 2026-09-19 踩过的坑：这里原来写的是 PET_DIR/idle.png —— 那是**上一版形象**
#    （642×1024，2026-09-18 接入新角色.py 留下的根目录旧图），
#    而桌宠默认显示的是 PET_DIR/expressions/idle.png（1536×1536，角色高 1200）。
#    照旧路径拆出来的 rig 切过去就变成另一个形象，还带着旧图透明区的杂色。
#    源图必须跟 FramePet 实际显示的那批一致。
DEFAULT_SRC = os.path.join(PET_DIR, "expressions", "idle.png")


def src_rel(path):
    """源图相对 DeepProf 根的路径 —— 写进 参数.json，给 预览rig.py 复用，
    免得校验脚本自己再硬编码一份（那正是这次出事的根因）。"""
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return path

ALPHA_TH = 8


# ════════════════════════════════════════════════════════════
# 检测
# ════════════════════════════════════════════════════════════

def detect_hem(al, rgb):
    """找裙摆下沿：最后一个「还有一大片藏青」的行。

    裙子是百褶的，每一褶之间都有深色线，所以不能用"最长连续段"来判，
    要用"整行藏青像素总数"。
    """
    H, W = al.shape
    b, r = rgb[:, :, 2], rgb[:, :, 0]
    navy = (b - r > 25) & (b < 150) & (al > 200)

    counts = navy.sum(axis=1)
    lo, hi = int(H * 0.35), int(H * 0.85)   # 裙子一定在这个范围内
    band = counts[lo:hi]
    if band.max() < 25:
        return None
    rows = np.nonzero(band >= 25)[0]
    return int(lo + rows.max())


def detect_splits(al, hem_y, hip_drop):
    """在裙摆下方一段"两腿已经分开"的干净区域，量出分界线。

    :returns: (左腿右边界, 右腿右边界) 或 None
    """
    y0 = hem_y + hip_drop + 10
    y1 = hem_y + hip_drop + 70
    left_edges, right_edges = [], []

    for y in range(y0, y1, 4):
        if y >= al.shape[0]:
            break
        xs = np.nonzero(al[y] > ALPHA_TH)[0]
        if len(xs) < 20:
            continue
        # 找连续段
        segs, s, prev = [], xs[0], xs[0]
        for x in xs[1:]:
            if x - prev > 2:
                segs.append((int(s), int(prev)))
                s = x
            prev = x
        segs.append((int(s), int(prev)))
        if len(segs) < 2:
            continue
        # 最左那段是左腿；它右边那段是右腿（尾巴在更右边，但那行它已经结束了）
        l, r = segs[0], segs[1]
        left_edges.append((l[1] + r[0]) / 2.0)   # 两腿缝的中点
        right_edges.append(r[1])                  # 右腿右边界

    if not left_edges:
        return None
    return float(np.median(left_edges)), float(np.median(right_edges))


# ════════════════════════════════════════════════════════════
# 拆
# ════════════════════════════════════════════════════════════

def build(img, hem_y, x_mid, x_right, hip_drop, inset):
    a = np.array(img.convert("RGBA"))
    al, rgb = a[:, :, 3], a[:, :, :3].astype(int)
    H, W = al.shape

    solid = al > ALPHA_TH
    b, r = rgb[:, :, 2], rgb[:, :, 0]
    navy = (b - r > 25) & (b < 150)

    yy = np.arange(H)[:, None]

    # 裙摆以下的"可能是腿"的区域
    below = (yy >= hem_y) & solid
    # 裙摆下沿是波浪形的：藏青色的像素不算腿，留给身体，免得啃掉裙子
    near_hem = (yy >= hem_y) & (yy < hem_y + 50)
    is_skirt = near_hem & navy

    leg_area = below & ~is_skirt

    xx = np.arange(W)[None, :]
    mask_l = leg_area & (xx < x_mid)
    mask_r = leg_area & (xx >= x_mid) & (xx <= x_right)

    # ── 身体层 = 原图 - 腿（但大腿最上面 INSET 像素保留，用来盖住旋转缝）──
    hole = (mask_l | mask_r)
    hole_inset = hole.copy()
    hole_inset[hem_y:hem_y + inset, :] = False   # 裙摆正下方那条带不挖

    body = a.copy()
    body[hole_inset] = (0, 0, 0, 0)

    # ── 腿层：腿的像素 + 向上延长到髋点（延长部分被裙子盖住）──
    hip_y = hem_y - hip_drop
    legs = {}
    pivots = {}
    for name, mask in (("leg_left", mask_l), ("leg_right", mask_r)):
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return None, "有一侧完全没找到腿的像素，切分线不对"
        top_row = int(ys.min())
        row_sel = np.nonzero(mask[top_row])[0]
        cx = (int(row_sel.min()) + int(row_sel.max())) / 2.0

        layer = np.zeros_like(a)
        layer[mask] = a[mask]

        # 向上延长：把腿最上面一行的像素往上复制到髋点。
        # 那一段始终被裙子盖住，所以复制出来的内容质量无所谓，
        # 关键是要够宽、够高，腿转起来才不会在裙摆下沿露出缝。
        if hip_y < top_row:
            top_strip = layer[top_row:top_row + 1, :, :]
            layer[hip_y:top_row, :, :] = np.repeat(top_strip, top_row - hip_y, axis=0)

        legs[name] = layer
        pivots[name] = {"x": round(cx, 2), "y": int(hip_y)}

    meta = {
        "canvas_w": W,
        "canvas_h": H,
        "hem_y": int(hem_y),
        "hip_y": int(hip_y),
        "inset": int(inset),
        "leg_left": pivots["leg_left"],
        "leg_right": pivots["leg_right"],
    }
    return (body, legs["leg_left"], legs["leg_right"], meta, mask_l, mask_r), None


# ════════════════════════════════════════════════════════════
# 调试图
# ════════════════════════════════════════════════════════════

def bleed_transparent(rgba, th=ALPHA_TH):
    """颜色外扩：把透明像素的 RGB 填成"最近的不透明像素"的颜色。

    ⚠️ 这一步不是可选的。透明像素的 RGB 默认是 (0,0,0) 纯黑，
    而旋转/缩放时的插值（PIL 的 BICUBIC、Pixi/GPU 的纹理过滤都一样）
    会把邻近像素的颜色混进来 —— 于是黑色的透明像素在角色轮廓上
    糊出一圈黑边。人眼看就是"图没抠干净"。

    填成邻近色之后，混进来的就是本该在那里的颜色，边缘自然干净。
    这张图的 alpha 没变，所以对不齐的问题一点没动，只是变好看了。
    """
    from scipy import ndimage
    a = rgba[:, :, 3]
    # 只有 alpha 严格为 0 的才算"看不见、可以改颜色"。
    # 别用 >ALPHA_TH 当阈值 —— alpha=8 那种极淡的像素仍然是可见的，
    # 改掉它的颜色就是在改画面。
    solid = a > 0
    if solid.all() or not solid.any():
        return rgba
    idx = ndimage.distance_transform_edt(~solid, return_distances=False, return_indices=True)
    out = rgba.copy()
    out[:, :, :3] = rgba[idx[0], idx[1], :3]
    return out


def save_debug(body, leg_l, leg_r, mask_l, mask_r, meta, dst):
    """三层染色叠起来看：红=左腿 绿=右腿 灰=身体。拆错了这张图一眼就能看出来"""
    H, W = body.shape[:2]
    vis = np.full((H, W, 3), 255, np.uint8)

    # 棋盘格底，方便看透明
    yy, xx = np.mgrid[0:H, 0:W]
    tile = (((yy // 16) + (xx // 16)) % 2) == 0
    vis[tile] = 232
    vis[~tile] = 210

    body_a = body[:, :, 3] > ALPHA_TH
    vis[body_a] = (body[body_a][:, :3] * 0.45 + 150 * 0.55).astype(np.uint8)  # 身体压灰

    for mask, layer, tint in ((mask_l, leg_l, (255, 60, 60)), (mask_r, leg_r, (40, 190, 90))):
        m = mask & (layer[:, :, 3] > ALPHA_TH)
        vis[m] = (layer[m][:, :3] * 0.45 + np.array(tint) * 0.55).astype(np.uint8)
        # 髋点（延长出来的那部分）用更亮的同色标出
        ext = (layer[:, :, 3] > ALPHA_TH) & ~mask
        vis[ext] = np.array(tint, np.uint8)

    out = Image.fromarray(vis, "RGB")
    d = ImageDraw.Draw(out)
    hem, hip = meta["hem_y"], meta["hip_y"]
    d.line([0, hem, W, hem], fill=(255, 0, 0), width=2)
    d.line([0, hip, W, hip], fill=(0, 90, 255), width=2)
    d.text((6, hem + 4), "裙摆线 y=%d" % hem, fill=(200, 0, 0))
    d.text((6, hip - 18), "髋点线 y=%d（亮色=向上延长，被裙子盖住）" % hip, fill=(0, 80, 220))

    for name in ("leg_left", "leg_right"):
        p = meta[name]
        col = (255, 0, 0) if name == "leg_left" else (0, 150, 50)
        d.ellipse([p["x"] - 6, p["y"] - 6, p["x"] + 6, p["y"] + 6], outline=col, width=3)
        label = "左腿髋点" if name == "leg_left" else "右腿髋点"
        d.text((p["x"] + 10, p["y"] - 6), label, fill=col)

    out.save(dst)


# ════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="单图拆成 身体/左腿/右腿 分层 rig")
    ap.add_argument("--源", dest="src", default=DEFAULT_SRC, help="源立绘")
    ap.add_argument("--摆线", dest="hem", type=int, help="手动指定裙摆线 y（默认自动检测）")
    ap.add_argument("--髋降", dest="hip_drop", type=int, default=55, help="髋点在裙摆线上方多少像素")
    ap.add_argument("--内缩", dest="inset", type=int, default=14, help="身体层保留大腿顶部多少像素")
    ap.add_argument("--裁到角色", dest="crop", action="store_true",
                    help="先把源图裁到角色外接框再拆（见下方说明，配合全新表情立绘时**应该开**）")
    ap.add_argument("--留白", dest="pad", type=int, default=8, help="裁切时四周留多少透明边（默认 8）")
    ap.add_argument("--接入", dest="install", action="store_true", help="拷进桌宠工程的 assets 目录")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print("✗ 找不到源图：%s" % args.src)
        return 1

    img = Image.open(args.src).convert("RGBA")
    a = np.array(img)
    print("源图: %s  %dx%d" % (os.path.basename(args.src), img.width, img.height))

    # ── 可选：裁到角色外接框 ──
    #
    # ⚠️ 为什么需要这一步：RigPet.jsx 的 fitScale() 是**按整张画布**算缩放的
    #    （min(IDEAL_H/CH, w/CW, h/CH)），不是按角色内容框。
    #
    #    上一版根目录那张 idle.png 是紧贴角色裁过的（角色占画布 92%宽/100%高），
    #    所以画布≈角色，看不出问题。换成 expressions/idle.png（1536×1536，
    #    角色只占 41%宽/78%高）之后，大片透明边距让 **宽度**约束被触发：
    #      窗口 220×420 时 scale 从 0.285 掉到 0.143 → 角色 292px 缩成 172px（小了 41%）
    #
    #    裁掉透明边距，约束就回到高度上，角色恢复 292px，且脚底贴底
    #    （原来画布下方留白 1536−1413=123px，脚底比窗口底边高 17px）。
    #
    #    这是**渲染端按画布缩放**这个既有实现下的正确做法；如果将来 RigPet
    #    改成按内容框缩放，这一步就可以去掉。
    crop_box = None
    if args.crop:
        al = np.array(img)[:, :, 3]
        ys = np.nonzero(al.max(axis=1))[0]
        xs = np.nonzero(al.max(axis=0))[0]
        if len(ys) == 0 or len(xs) == 0:
            print("✗ --裁到角色：整张图都是透明的")
            return 1
        p = max(0, args.pad)
        box = (max(0, int(xs.min()) - p), max(0, int(ys.min()) - p),
               min(img.width, int(xs.max()) + 1 + p), min(img.height, int(ys.max()) + 1 + p))
        img = img.crop(box)
        a = np.array(img)
        # 记下来：校验脚本（预览rig.py）要用同一个框裁源图，才能逐像素比对
        crop_box = [int(v) for v in box]
        print("  已裁到角色外接框 + %dpx 留白 → %dx%d（角色占 %.0f%%宽 / %.0f%%高）"
              % (p, img.width, img.height,
                 (xs.max() - xs.min() + 1) / img.width * 100,
                 (ys.max() - ys.min() + 1) / img.height * 100))

    hem_y = args.hem or detect_hem(a[:, :, 3], a[:, :, :3].astype(int))
    if hem_y is None:
        print("✗ 没检测到裙摆线（图里没有大片藏青色裙子？）。用 --摆线 <y> 手动指定。")
        return 1
    print("  裙摆线 y = %d %s" % (hem_y, "(手动指定)" if args.hem else "(自动检测)"))

    sp = detect_splits(a[:, :, 3], hem_y, args.hip_drop)
    if sp is None:
        print("✗ 在裙摆下方没找到「两腿分开」的区域，量不出切分线。")
        return 1
    x_mid, x_right = sp
    print("  两腿分界 x = %.1f" % x_mid)
    print("  右腿右边界 x = %.1f（再往右是鲸尾，留给身体层）" % x_right)

    result, err = build(img, hem_y, x_mid, x_right, args.hip_drop, args.inset)
    if err:
        print("✗ %s" % err)
        return 1
    body, leg_l, leg_r, meta, mask_l, mask_r = result

    os.makedirs(RIG_DIR, exist_ok=True)
    # 导出前做颜色外扩，消掉旋转时的黑边（见 bleed_transparent 的说明）
    for name, layer in (("body", body), ("leg_left", leg_l), ("leg_right", leg_r)):
        Image.fromarray(bleed_transparent(layer), "RGBA").save(
            os.path.join(RIG_DIR, "%s.png" % name))

    meta["leg_left_px"] = int(mask_l.sum())
    meta["leg_right_px"] = int(mask_r.sum())
    meta["source"] = os.path.basename(args.src)
    # source_path = 相对 DeepProf 根的路径。校验脚本（预览rig.py）按它找源图，
    # 别让校验脚本自己硬编码一份源图路径 —— 两边写岔了校验就会"对着错图说通过"。
    meta["source_path"] = src_rel(os.path.abspath(args.src))
    # 用了 --裁到角色 时，记下裁切框（在**原图**坐标系里的 L,T,R,B）。
    # 校验脚本拿它把源图裁成同一尺寸再逐像素比 —— 不然会报"尺寸不一致"。
    if crop_box:
        meta["source_crop"] = crop_box
    # 这个文件是给渲染端读的，所以键名用 ASCII；说明留在这里给人看
    meta["_说明"] = (
        "leg_left / leg_right = 该腿的旋转中心（髋点），坐标相对 canvas_w × canvas_h 画布；"
        "hem_y = 裙摆线（身体层与腿层的分界）；hip_y = 髋点线；"
        "inset = 身体层保留的大腿顶部像素数（用来盖住旋转缝）。"
        "改这里没用，要重跑 拆腿.py。"
    )
    with open(os.path.join(RIG_DIR, "参数.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    save_debug(body, leg_l, leg_r, mask_l, mask_r, meta,
               os.path.join(RIG_DIR, "_拆解调试图.png"))

    print("\n  ✓ body.png   髋点线以上含裙子/尾巴，z 序最高")
    print("  ✓ leg_left.png  髋点 (%s, %s)   %d 像素" % (meta["leg_left"]["x"], meta["leg_left"]["y"], meta["leg_left_px"]))
    print("  ✓ leg_right.png  髋点 (%s, %s)   %d 像素" % (meta["leg_right"]["x"], meta["leg_right"]["y"], meta["leg_right_px"]))
    print("  ✓ 参数.json  渲染端直接读，别手抄坐标")
    print("  ✓ _拆解调试图.png  ← 先看这张，确认三层拆对了再往下走")
    print("\n输出目录：%s" % RIG_DIR)

    if args.install:
        print()
        install()
    return 0


def install():
    """把 rig 资产拷进桌宠工程，渲染端直接从 assets 里 import"""
    import shutil

    dst = os.path.join(os.path.dirname(HERE), "desktop", "src", "renderer", "src",
                       "assets", "pet", "rig")
    os.makedirs(dst, exist_ok=True)
    for f in ("body.png", "leg_left.png", "leg_right.png"):
        shutil.copy2(os.path.join(RIG_DIR, f), os.path.join(dst, f))
        print("  ✓ %s" % f)
    # 参数文件在工程里改叫 rig.json —— 避免中文文件名进打包链路
    shutil.copy2(os.path.join(RIG_DIR, "参数.json"), os.path.join(dst, "rig.json"))
    print("  ✓ rig.json（由 参数.json 改名而来）")
    print("  → %s" % dst)


if __name__ == "__main__":
    sys.exit(main())
