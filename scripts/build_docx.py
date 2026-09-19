"""把项目计划书 HTML 转成 DOCX 可编辑版的构建脚本。

流程：
    1. 从计划书 HTML 提取 #cover-source（封面）与 #source（正文）
    2. 清理分页/预览专用属性，把 CSS 图形（架构图）转成语义表格，
       表格 caption 转为独立段落，展开 colspan，保证 DOCX 不丢内容
    3. 生成中间 clean HTML
    4. 调用 Doc Page 技能自带的 export-paper-docx-fallback.py 生成 DOCX

用法（在项目根目录）：
    py scripts/build_docx.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from lxml import etree, html as lhtml

ROOT = Path(__file__).resolve().parent.parent
SRC_HTML = ROOT / "docs" / "DeeepProf项目计划书" / "DeeepProf项目计划书.html"
OUT_DIR = SRC_HTML.parent
CLEAN_HTML = OUT_DIR / "DeeepProf项目计划书.clean.html"
OUT_DOCX = OUT_DIR / "DeeepProf项目计划书.docx"
FALLBACK = Path.home() / ".trae-cn" / "skills" / "doc-page" / "scripts" / "export-paper-docx-fallback.py"

# 分页/预览专用 class，转 DOCX 时无意义，直接剥掉
STRIP_CLASSES = {"flow-block", "page-frame", "page", "page-content", "folio", "has-cover"}


def clean_classes(el) -> None:
    """移除分页相关 class，保留语义 class（如 callout/subtitle/caption）。"""
    cls = el.get("class")
    if not cls:
        return
    kept = [c for c in cls.split() if c not in STRIP_CLASSES]
    if kept:
        el.set("class", " ".join(kept))
    else:
        del el.attrib["class"]


def strip_preview_attrs(root) -> None:
    """删除脚本、样式与预览属性。"""
    for el in root.xpath("//script | //style | //nav"):
        el.getparent().remove(el)
    for el in root.xpath("//*[@aria-hidden]"):
        del el.attrib["aria-hidden"]
    for el in root.xpath("//*[@style]"):
        del el.attrib["style"]
    for el in root.iter():
        if isinstance(el.tag, str):
            clean_classes(el)


def convert_arch_figure(root) -> None:
    """把 CSS 架构图（.arch）转成单列表格，保证 DOCX 里结构清晰。"""
    for figure in root.xpath("//figure[.//div[contains(concat(' ', @class, ' '), ' arch ')]]"):
        layers = figure.xpath(".//div[contains(concat(' ', @class, ' '), 'arch-layer')]")
        table = etree.SubElement(figure, "table")
        tbody = etree.SubElement(table, "tbody")
        for layer in layers:
            # layer.text 是层名，span 内是层描述，拼成 "层名：描述"
            name = " ".join((layer.text or "").split())
            spans = layer.xpath("./span")
            desc = " ".join(" ".join(s.itertext()) for s in spans)
            tr = etree.SubElement(tbody, "tr")
            td = etree.SubElement(tr, "td")
            td.text = f"{name}：{desc}" if desc else name
        # figcaption → 独立 caption 段落
        for cap in figure.xpath("./figcaption"):
            p = etree.Element("p")
            p.set("class", "caption")
            p.text = " ".join(cap.itertext()).strip()
            figure.append(p)
            figure.remove(cap)
        # 移除原 .arch 容器与箭头
        for el in figure.xpath("./div"):
            figure.remove(el)


def figures_to_divs(root) -> None:
    """fallback 转换器不识别 figure 标签（会压平成纯文本），改为 div 递归处理。"""
    for fig in root.xpath("//figure"):
        fig.tag = "div"


def captions_to_paragraphs(root) -> None:
    """表格 caption 元素在 DOCX 转换中会丢失，转为表后独立段落。"""
    for cap in root.xpath("//table/caption"):
        text = " ".join(cap.itertext()).strip()
        table = cap.getparent()
        p = etree.Element("p")
        p.set("class", "caption")
        p.text = text
        table.remove(cap)
        table.addnext(p)


def expand_colspan(root) -> None:
    """展开 colspan：fallback 转换器不识别合并单元格，拆成等量单格。"""
    for cell in root.xpath("//td[@colspan] | //th[@colspan]"):
        span = int(cell.get("colspan", "1"))
        del cell.attrib["colspan"]
        for _ in range(span - 1):
            empty = etree.Element(cell.tag)
            cell.addnext(empty)
            cell = empty


def serialize_fragment(el) -> str:
    """把元素内部 HTML 序列化为字符串。"""
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(etree.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


def main() -> int:
    if not SRC_HTML.exists():
        print(f"[错误] 找不到源文件: {SRC_HTML}")
        return 1
    if not FALLBACK.exists():
        print(f"[错误] 找不到 DOCX fallback 脚本: {FALLBACK}")
        return 1

    doc = lhtml.parse(str(SRC_HTML))
    root = doc.getroot()
    strip_preview_attrs(root)
    convert_arch_figure(root)
    figures_to_divs(root)
    captions_to_paragraphs(root)
    expand_colspan(root)

    cover = root.xpath("//*[@id='cover-source']")
    source = root.xpath("//*[@id='source']")
    if not source:
        print("[错误] 未找到 #source 正文块")
        return 1
    title = root.xpath("string(//title)").strip() or "DeeepProf项目计划书"

    cover_html = serialize_fragment(cover[0]) if cover else ""
    source_html = serialize_fragment(source[0])

    clean = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ color: #222; font-family: "Noto Serif SC", "SimSun", serif; font-size: 11pt; line-height: 1.65; }}
  h1, h2, h3 {{ color: #264653; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #d9d9d9; padding: 6px 8px; vertical-align: top; }}
  th {{ background: #eeeeee; }}
  .caption {{ color: #555; font-size: 9pt; }}
  .callout {{ border-left: 3px solid #777; padding-left: 12px; }}
</style>
</head>
<body>
{cover_html}
{source_html}
</body>
</html>
"""
    CLEAN_HTML.write_text(clean, encoding="utf-8")
    print(f"[1/2] 已生成中间 clean HTML: {CLEAN_HTML}")

    result = subprocess.run(
        [sys.executable, str(FALLBACK), str(CLEAN_HTML), str(OUT_DOCX)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"[错误] DOCX 导出失败:\n{result.stderr}")
        return result.returncode
    print(f"[2/2] {result.stdout.strip()}")
    size_kb = OUT_DOCX.stat().st_size / 1024
    print(f"DOCX 大小: {size_kb:.1f} KB（远低于 20M 限制）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
