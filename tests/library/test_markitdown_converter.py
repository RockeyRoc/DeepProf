from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from library.markitdown_converter import convert_local


def _text_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 700 Td (This is reliable text content on page {index + 1}.) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


def _scanned_pdf(path: Path, pages: int = 2) -> None:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 72)
    frames = []
    for index in range(pages):
        image = Image.new("RGB", (1600, 320), "white")
        ImageDraw.Draw(image).text((50, 80), f"线性表是具有相同特性数据元素的有限序列 第{index + 1}页", font=font, fill="black")
        frames.append(image)
    frames[0].save(path, "PDF", resolution=144.0, save_all=True, append_images=frames[1:])


def test_text_pdf_keeps_page_order_and_does_not_require_optional_ocr(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    source = tmp_path / "text.pdf"
    _text_pdf(source, pages=2)
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)

    result = convert_local(str(source), first_page=2, last_page=2)
    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    assert result["ocr_used"] is False
    assert metadata["source"]["page_count"] == 2
    assert metadata["source"]["page_range"] == {"first": 2, "last": 2}
    assert metadata["pages"][0]["page"] == 2
    assert metadata["pages"][0]["conversion"] == "markitdown_pdf_text"
    assert "page 2" in metadata["markdown"]


def test_scanned_pdf_uses_local_chinese_ocr_and_marks_review_required(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    source = tmp_path / "scan.pdf"
    _scanned_pdf(source)
    result = convert_local(str(source), first_page=2, last_page=2)
    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    page = metadata["pages"][0]
    assert result["ocr_used"] is True and result["review_required"] is True
    assert page["page"] == 2 and page["review_required"] is True
    assert page["conversion"] == "rapidocr_onnxruntime"
    assert "线性表" in page["text"]
    assert isinstance(page["ocr_confidence"], float)
    assert metadata["status"] == "ocr_candidate_only"
    assert metadata["source"]["sha256"] == result["source_sha256"]
    assert "线性表" in Path(result["markdown_path"]).read_text(encoding="utf-8")


def test_local_images_and_markitdown_office_formats_have_accurate_locations(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 64)
    image = Image.new("RGB", (1400, 260), "white")
    ImageDraw.Draw(image).text((40, 70), "线性表属于数据结构", font=font, fill="black")
    image_path = tmp_path / "scan.png"
    image.save(image_path)
    png_result = convert_local(str(image_path))
    png_meta = json.loads(Path(png_result["metadata_path"]).read_text(encoding="utf-8"))
    assert png_meta["pages"][0]["page"] is None
    assert png_meta["pages"][0]["label"] == "image 1"
    assert png_meta["pages"][0]["review_required"] is True

    blank_path = tmp_path / "blank.png"
    Image.new("RGB", (400, 200), "white").save(blank_path)
    blank_result = convert_local(str(blank_path))
    blank_meta = json.loads(Path(blank_result["metadata_path"]).read_text(encoding="utf-8"))
    assert blank_meta["pages"][0]["text"] == ""
    assert blank_result["review_required"] is True

    import rapidocr_onnxruntime
    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", lambda: lambda _image: ([[None, "image text", 0.9]], None))
    for extension, format_name in (("jpg", "JPEG"), ("tiff", "TIFF")):
        image_path = tmp_path / f"scan.{extension}"
        image.save(image_path, format=format_name)
        result = convert_local(str(image_path))
        metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
        assert metadata["pages"][0]["text"] == "image text"

    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("A DOCX page number is intentionally not inferred.")
    docx_path = tmp_path / "notes.docx"
    document.save(docx_path)
    doc_result = convert_local(str(docx_path))
    doc_meta = json.loads(Path(doc_result["metadata_path"]).read_text(encoding="utf-8"))
    assert doc_meta["pages"][0]["page"] is None
    assert doc_meta["pages"][0]["label"] == "document"

    markdown_path = tmp_path / "notes.md"
    markdown_path.write_text("# Local note\n\nMarkItDown keeps Markdown as one logical unit.", encoding="utf-8")
    md_result = convert_local(str(markdown_path))
    md_meta = json.loads(Path(md_result["metadata_path"]).read_text(encoding="utf-8"))
    assert md_meta["pages"][0]["page"] is None and "Local note" in md_meta["markdown"]

    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    for number in (1, 2):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        shape = slide.shapes.add_textbox(100000, 100000, 8000000, 500000)
        shape.text = f"Course slide {number}"
    pptx_path = tmp_path / "slides.pptx"
    presentation.save(pptx_path)
    ppt_result = convert_local(str(pptx_path))
    ppt_meta = json.loads(Path(ppt_result["metadata_path"]).read_text(encoding="utf-8"))
    assert [unit["slide"] for unit in ppt_meta["pages"]] == [1, 2]
    assert all(unit["page"] is None for unit in ppt_meta["pages"])


def test_ocr_candidate_errors_page_limits_and_empty_image_results(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    image_path = tmp_path / "blank.png"
    Image.new("RGB", (320, 160), "white").save(image_path)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    with pytest.raises(RuntimeError, match="install requirements-ocr.txt"):
        convert_local(str(image_path))

    text_path = tmp_path / "small.txt"
    text_path.write_text("small", encoding="utf-8")
    with pytest.raises(ValueError, match="document_size_limit_exceeded"):
        convert_local(str(text_path), max_bytes=2)

    pdf_path = tmp_path / "range.pdf"
    _text_pdf(pdf_path, pages=2)
    with pytest.raises(ValueError, match="pdf_page_range_out_of_bounds"):
        convert_local(str(pdf_path), first_page=3)
    with pytest.raises(ValueError, match="page_range_only_supported_for_pdf"):
        convert_local(str(text_path), first_page=1)
