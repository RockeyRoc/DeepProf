"""README 展示图 —— 按参考版式（暖白底 + 左侧大字 + 右侧深色产品板 + 状态清单）重做。

两点纪律：
  1. 深色板里的每一行都是 CLI 真会打印的字符串，照抄自 apps/cli/src/index.ts
     与 src/output.ts，不是编的截图。
  2. 中文必须走 CJK 字体。Latin-only 的等宽字体（DejaVuSansMono）会把中文渲成豆腐块，
     所以这里用抽出来的 Noto Sans Mono CJK SC 当控制台字体（抽取方法见本目录 README 的
     「字体」一节，那一节里的四份之外还要多抽这一份）。

依赖 media/wordmark.png 作为字标。字体目录用 DP_FONT_DIR 指定，默认是本目录下的 fonts/。

输出：banner-zh.png / banner-en.png，2400×1040。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import os
HERE = Path(__file__).resolve().parent
FONTS = Path(os.environ.get("DP_FONT_DIR", HERE / "fonts"))
SERIF_B = FONTS / "NotoSerifCJKsc-Bold.otf"
SANS_R = FONTS / "NotoSansCJKsc-Regular.otf"
SANS_B = FONTS / "NotoSansCJKsc-Bold.otf"
MONO_CJK = FONTS / "NotoSansMonoCJKsc-Regular.otf"   # 含中文，控制台字体
WORDMARK = (HERE / ".." / ".." / "media" / "wordmark.png").resolve()  # 仓库 media/ 里的字标

BW, BH = 2400, 1040
PAD = 100
RX = 1060                     # 左栏右边界
PX0, PY0, PX1, PY1 = 1160, 140, 2320, 940

PAPER = (244, 243, 239)
INK = (20, 22, 26)
INK2 = (74, 79, 87)
MUTED = (138, 140, 145)
RULE = (223, 220, 213)
BRAND = (7, 101, 252)
DARK = (10, 21, 38)
DARK2 = (88, 114, 158)
DARKT = (222, 232, 250)
ACCENT = (232, 88, 60)


def tracked(d, xy, text, font, fill, track=0.0):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + track
    return x


def _break_word(d, word, font, max_w):
    """单词本身超宽时按字断（中文串走这条）。"""
    out, cur = [], ""
    for ch in word:
        if d.textlength(cur + ch, font=font) <= max_w or not cur:
            cur += ch
        else:
            out.append(cur); cur = ch
    if cur:
        out.append(cur)
    return out


def wrap(d, text, font, max_w):
    """按词优先折行；单词超宽才按字断。"""
    lines, cur = [], ""
    for word in text.split(" "):
        cand = word if not cur else cur + " " + word
        if d.textlength(cand, font=font) <= max_w:
            cur = cand
        elif not cur:
            parts = _break_word(d, word, font, max_w)
            lines.extend(parts[:-1]); cur = parts[-1]
        else:
            lines.append(cur)
            if d.textlength(word, font=font) <= max_w:
                cur = word
            else:
                parts = _break_word(d, word, font, max_w)
                lines.extend(parts[:-1]); cur = parts[-1]
    if cur:
        lines.append(cur)
    return lines


T = {
    "zh": dict(
        eyebrow="本机优先的教学原型",
        head=["让每一条教学建议", "都有出处"],
        lede="DeepProf 把课程材料的可定位证据、合格作答事实、教学策略与 BKT 学情估计，"
             "连到同一条可回放的教学链路上。",
        rows=[("01", "本机链路：CLI → Gateway → 策略图 → 回放", "WORKING", None),
              ("02", "M3 离线矩阵 120 + 120 + 160 格", "400 / 400", None),
              ("03", "BKT 学情模型", "UNCALIBRATED", ACCENT)],
        plate=[
            ("  ◇ DeepProf", DARKT),
            ("  ──────────", DARK2),
            ("  learn · reflect · grow", DARKT),
            (None, None),
            ("ds.c_language.v1 · group B · 模型未配置", DARK2),
            ("/help 查看命令 · 空闲时 Ctrl+C 退出", DARK2),
            (None, None),
            ("CMD", '/new --mode study --group C --title "线性表"'),
        ],
        ev="M1 76/80 · M3 400/400 · AUC 0.296",
        ev2="construction cases only · no human participants",
        foot="v0.6.2  ·  2026-09-24  ·  github.com/RockeyRoc/DeepProf",
    ),
    "en": dict(
        eyebrow="A local-first teaching prototype",
        head=["Every teaching", "suggestion, sourced."],
        lede="DeepProf links locatable course evidence, reliable answer facts, a teaching "
             "policy and a BKT mastery estimate onto one replayable teaching chain.",
        rows=[("01", "Local chain: CLI → Gateway → policy → replay", "WORKING", None),
              ("02", "M3 offline matrix, 120 + 120 + 160 cells", "400 / 400", None),
              ("03", "BKT learner model", "UNCALIBRATED", ACCENT)],
        plate=[
            ("  ◇ DeepProf", DARKT),
            ("  ──────────", DARK2),
            ("  learn · reflect · grow", DARKT),
            (None, None),
            ("ds.c_language.v1 · group B · model not configured", DARK2),
            ("/help for commands · Ctrl+C to quit", DARK2),
            (None, None),
            ("CMD", '/new --mode study --group C --title "linked list"'),
        ],
        ev="M1 76/80 · M3 400/400 · AUC 0.296",
        ev2="construction cases only · no human participants",
        foot="v0.6.2  ·  2026-09-24  ·  github.com/RockeyRoc/DeepProf",
    ),
}


def build(lang: str, out: Path):
    t = T[lang]
    img = Image.new("RGB", (BW, BH), PAPER)
    d = ImageDraw.Draw(img)

    f_mono = ImageFont.truetype(MONO_CJK, 24)
    f_mono_s = ImageFont.truetype(MONO_CJK, 19)
    f_mono_xs = ImageFont.truetype(MONO_CJK, 21)
    f_sans = ImageFont.truetype(SANS_R, 25)
    f_row = ImageFont.truetype(SANS_B, 27)

    # ---------------- 左栏：字标
    wm = Image.open(WORDMARK).convert("RGBA")
    wm = wm.crop(wm.getbbox())
    h = 70
    wm = wm.resize((int(wm.width * h / wm.height), h), Image.LANCZOS)
    img.paste(wm, (PAD, 92), wm)

    # 眉标
    d.rectangle([PAD, 254, PAD + 26, 257], fill=BRAND)
    tracked(d, (PAD + 40, 244), t["eyebrow"], f_mono_xs, MUTED, track=1.4)

    # 大标题
    f_head = ImageFont.truetype(SERIF_B, 84)
    y = 306
    for line in t["head"]:
        d.text((PAD, y), line, font=f_head, fill=INK)
        y += 104

    # 引言
    for i, line in enumerate(wrap(d, t["lede"], f_sans, RX - PAD)):
        d.text((PAD, y + 32 + i * 42), line, font=f_sans, fill=INK2)

    # ---------------- 左栏：状态清单（右侧状态标签右对齐）
    ry = 704
    for num, label, tag, tagcol in t["rows"]:
        d.line([(PAD, ry - 18), (RX, ry - 18)], fill=RULE, width=2)
        d.text((PAD, ry), num, font=f_mono_xs, fill=MUTED)
        d.text((PAD + 66, ry - 6), label, font=f_row, fill=INK)
        d.text((RX, ry), tag, font=f_mono_s, fill=tagcol or MUTED, anchor="ra")
        ry += 56

    d.line([(PAD, 892), (RX, 892)], fill=RULE, width=2)
    d.text((PAD, 918), t["foot"], font=f_mono_s, fill=MUTED)

    # ---------------- 右栏：深色产品板
    plate = Image.new("RGBA", (PX1 - PX0, PY1 - PY0), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle(
        [0, 0, PX1 - PX0 - 1, PY1 - PY0 - 1], 26, fill=DARK + (255,))
    img.paste(plate, (PX0, PY0), plate)
    d = ImageDraw.Draw(img)

    d.text((PX0 + 54, PY0 + 44), "deepprof · repl", font=f_mono_s, fill=(96, 126, 178))

    x0 = PX0 + 54
    y = PY0 + 118
    for line, col in t["plate"]:
        if line is None:
            y += 34
        elif line == "CMD":
            pre = "you › "
            d.text((x0, y), pre, font=f_mono, fill=BRAND)
            d.text((x0 + f_mono.getlength(pre), y), col, font=f_mono, fill=DARKT)
            cw = x0 + f_mono.getlength(pre + col)
            d.rectangle([cw + 6, y + 4, cw + 17, y + 30], fill=BRAND)
            y += 48
        else:
            d.text((x0, y), line, font=f_mono, fill=col)
            y += 48

    # evidence 区块：只放可核对的数字，且明确标为 evidence，不伪装成 CLI 输出
    ey = PY1 - 250
    d.rectangle([x0, ey, x0 + 420, ey + 1], fill=(34, 52, 82))
    d.text((x0, ey + 22), "evidence", font=f_mono_s, fill=(96, 126, 178))
    d.text((x0, ey + 60), t["ev"], font=ImageFont.truetype(MONO_CJK, 22), fill=DARKT)
    d.text((x0, ey + 98), t["ev2"], font=f_mono_s, fill=DARK2)

    d.rectangle([x0, PY1 - 78, x0 + 92, PY1 - 75], fill=BRAND)

    img.save(out / f"banner-{lang}.png", optimize=True)
    print("ok", lang, img.size)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: make_banner.py <output-dir>   (通常是 ../../media)")
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    for lang in ("zh", "en"):
        build(lang, out)
