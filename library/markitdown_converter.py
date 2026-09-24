"""Constrained local MarkItDown conversion with page-aware offline OCR."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from config import paths as user_paths


SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".md", ".markdown", ".docx", ".pptx"}
MAX_PAGES = 50
MAX_RENDER_PIXELS = 40_000_000
RENDER_SCALE = 2.0


def convert_local(path: str, *, first_page: int | None = None, last_page: int | None = None,
                  force_ocr: bool = False, max_bytes: int = 50_000_000,
                  render_scale: float = RENDER_SCALE) -> dict[str, Any]:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("document_path_is_not_a_file")
    extension = source.suffix.lower()
    if extension not in SUPPORTED:
        raise ValueError("unsupported_document_format:" + (extension or "unknown"))
    size = source.stat().st_size
    if size <= 0 or size > max_bytes:
        raise ValueError("document_size_limit_exceeded")
    if not 0.5 <= float(render_scale) <= 4.0:
        raise ValueError("render_scale_out_of_bounds:0.5-4.0")
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if extension != ".pdf" and (first_page is not None or last_page is not None):
        raise ValueError("page_range_only_supported_for_pdf")
    pages: list[dict[str, Any]]
    markdown: str
    ocr_used = False
    total_pages: int | None = None
    if extension == ".pdf":
        pages, ocr_used, total_pages = _convert_pdf(data, first_page, last_page, force_ocr, float(render_scale))
        markdown = "\n\n".join(f"<!-- PDF page {row['page']} | {row['conversion']} -->\n\n{row['text']}" for row in pages)
    elif extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        page, confidence = _ocr_image(data)
        pages = [{"unit": 1, "page": None, "label": "image 1", "conversion": "rapidocr_onnxruntime",
                  "text": page, "ocr_confidence": confidence, "review_required": True}]
        markdown = page
        ocr_used = True
    else:
        markdown = _markitdown_builtin(source)
        pages = _structured_units(markdown, extension)

    payload = {
        "format_version": "1", "status": "ocr_candidate_only" if ocr_used else "converted",
        "source": {"filename": source.name, "sha256": digest, "size_bytes": size,
                   "format": extension[1:], **({"page_count": total_pages,
                       "page_range": {"first": pages[0]["page"], "last": pages[-1]["page"]}}
                       if extension == ".pdf" and pages else {})},
        "conversion": {"engine": "MarkItDown", "version": _package_version("markitdown"),
                       "ocr_engine": "rapidocr_onnxruntime" if ocr_used else None,
                       "ocr_review": "required_before_use" if ocr_used else None,
                       "render_scale": float(render_scale) if ocr_used and extension == ".pdf" else None},
        "pages": pages,
        "markdown": markdown,
    }
    output_dir = user_paths.course_data_dir() / "conversions"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{digest[:16]}-{uuid.uuid4().hex[:8]}"
    markdown_path = output_dir / f"{stem}.md"
    metadata_path = output_dir / f"{stem}.json"
    _atomic_write(markdown_path, markdown)
    _atomic_write(metadata_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"status": "ok", "source_sha256": digest, "page_count": len(pages),
            "ocr_used": ocr_used, "review_required": ocr_used,
            "markdown_path": str(markdown_path), "metadata_path": str(metadata_path),
            "format": extension[1:], "markitdown_version": payload["conversion"]["version"]}


def _convert_pdf(data: bytes, first: int | None, last: int | None,
                 force_ocr: bool, render_scale: float) -> tuple[list[dict[str, Any]], bool, int]:
    try:
        from markitdown import MarkItDown
        from markitdown._base_converter import DocumentConverter, DocumentConverterResult
        from markitdown._stream_info import StreamInfo
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("markitdown_pdf_dependencies_missing; install requirements.txt") from exc
    reader = PdfReader(io.BytesIO(data), strict=False)
    page_count = len(reader.pages)
    start = 1 if first is None else int(first)
    end = page_count if last is None else int(last)
    if start < 1 or end < start or end > page_count:
        raise ValueError(f"pdf_page_range_out_of_bounds:1-{page_count}")
    if end - start + 1 > MAX_PAGES:
        raise ValueError(f"pdf_page_range_exceeds_limit:{MAX_PAGES}")
    class LocalPdfConverter(DocumentConverter):
        def __init__(self) -> None:
            self.pages: list[dict[str, Any]] = []
            self.ocr_used = False
            self.engine: Any = None
            self.document: Any = None

        def accepts(self, file_stream: Any, stream_info: StreamInfo, **kwargs: Any) -> bool:
            del file_stream, kwargs
            return str(getattr(stream_info, "extension", "") or "").lower() == ".pdf"

        def convert(self, file_stream: Any, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
            del file_stream, stream_info, kwargs
            for number in range(start, end + 1):
                text = str(reader.pages[number - 1].extract_text() or "").strip()
                if force_ocr:
                    use_ocr = True
                else:
                    try:
                        from library.parsers import _text_reliable
                    except ImportError as exc:
                        raise RuntimeError("markitdown_pdf_dependencies_missing; install requirements.txt") from exc
                    use_ocr = not _text_reliable(text) or len(text) < 24
                confidence: float | None = None
                if use_ocr:
                    if self.document is None:
                        try:
                            import pypdfium2 as pdfium
                            from rapidocr_onnxruntime import RapidOCR
                        except ImportError as exc:
                            raise RuntimeError("local_ocr_dependencies_missing; install requirements-ocr.txt") from exc
                        self.document = pdfium.PdfDocument(io.BytesIO(data))
                        self.engine = RapidOCR()
                    width, height = self.document[number - 1].get_size()
                    predicted_pixels = math.ceil(width * render_scale) * math.ceil(height * render_scale)
                    if predicted_pixels > MAX_RENDER_PIXELS:
                        raise ValueError(f"pdf_page_render_too_large:{number}")
                    bitmap = self.document[number - 1].render(scale=render_scale)
                    if bitmap.width * bitmap.height > MAX_RENDER_PIXELS:
                        raise ValueError(f"pdf_page_render_too_large:{number}")
                    text, confidence = _ocr_result(self.engine, bitmap.to_pil())
                    self.ocr_used = True
                self.pages.append({"unit": number, "page": number, "label": f"PDF page {number}",
                                   "conversion": "rapidocr_onnxruntime" if use_ocr else "markitdown_pdf_text",
                                   "text": text, "ocr_confidence": confidence, "review_required": bool(use_ocr)})
            markdown = "\n\n".join(
                f"<!-- PDF page {row['page']} | {row['conversion']} -->\n\n{row['text']}" for row in self.pages
            )
            return DocumentConverterResult(markdown, title=str(getattr(stream_info, "filename", "") or "document.pdf"))

    markitdown = MarkItDown(enable_plugins=False)
    local_converter = LocalPdfConverter()
    markitdown.register_converter(local_converter, priority=-100.0)
    # The local page-aware converter is selected by MarkItDown's format registry.
    info = StreamInfo(extension=".pdf", filename="local.pdf", mimetype="application/pdf")
    try:
        markitdown.convert_stream(io.BytesIO(data), stream_info=info)
        if not local_converter.pages:
            raise RuntimeError("markitdown_pdf_converter_not_selected")
        return local_converter.pages, local_converter.ocr_used, page_count
    finally:
        if local_converter.document is not None:
            local_converter.document.close()


def _ocr_image(data: bytes) -> tuple[str, float | None]:
    try:
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("local_ocr_dependencies_missing; install requirements-ocr.txt") from exc
    image = Image.open(io.BytesIO(data))
    if image.width * image.height > MAX_RENDER_PIXELS:
        raise ValueError("image_dimensions_exceed_limit")
    return _ocr_result(RapidOCR(), image)


def _ocr_result(engine: Any, image: Any) -> tuple[str, float | None]:
    recognized, _ = engine(image)
    lines: list[str] = []
    confidences: list[float] = []
    for row in recognized or []:
        if len(row) < 2:
            continue
        lines.append(str(row[1]))
        if len(row) > 2:
            confidences.append(float(row[2]))
    return "\n".join(lines), sum(confidences) / len(confidences) if confidences else None


def _markitdown_builtin(source: Path) -> str:
    try:
        from markitdown import MarkItDown
        converter = MarkItDown(enable_plugins=False)
        return str(converter.convert_local(str(source)).markdown or "")
    except ImportError as exc:
        raise RuntimeError("markitdown_dependency_missing; install requirements.txt") from exc
    except Exception as exc:
        if type(exc).__name__ == "MissingDependencyException":
            raise RuntimeError("markitdown_format_dependencies_missing; install requirements.txt") from exc
        raise


def _structured_units(markdown: str, extension: str) -> list[dict[str, Any]]:
    if extension == ".pptx":
        matches = list(re.finditer(
            r"(?im)^(?:<!--\s*Slide number:\s*(\d+)\s*-->|#{1,6}\s*(?:slide|幻灯片)\s*(\d+).*?)\s*$",
            markdown,
        ))
        if matches:
            result = []
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
                slide_number = match.group(1) or match.group(2)
                result.append({"unit": index + 1, "slide": int(slide_number), "page": None,
                               "label": f"slide {slide_number}", "conversion": "markitdown",
                               "text": markdown[match.start():end].strip(), "review_required": False})
            return result
    label = "document" if extension == ".docx" else "text"
    return [{"unit": 1, "page": None, "label": label, "conversion": "markitdown",
             "text": markdown, "review_required": False}]


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _atomic_write(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


__all__ = ["MAX_PAGES", "SUPPORTED", "convert_local"]
