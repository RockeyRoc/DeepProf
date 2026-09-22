"""Dependency-light parsers for the first resource-library formats."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from library.errors import LibraryError
from library.models import DocumentPage


@dataclass(slots=True)
class ParsedDocument:
    pages: list[DocumentPage]
    media_type: str


class _VisibleHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.headings: list[str] = []
        self._hidden = 0
        self._heading = False
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._hidden += 1
        if tag.lower() in {"h1", "h2", "h3", "h4"} and not self._hidden:
            self._heading = True
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"h1", "h2", "h3", "h4"} and self._heading:
            heading = " ".join("".join(self._heading_parts).split())
            if heading:
                self.headings.append(heading)
            self._heading = False
        if lowered in {"script", "style", "noscript", "svg"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        if self._heading:
            self._heading_parts.append(data)
        if data.strip():
            self.parts.append(data.strip())


def parse_path(path: str) -> ParsedDocument:
    from pathlib import Path

    candidate = Path(path)
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise LibraryError(
            f"无法读取资源文件: {candidate}", kind="read_failed", path=str(candidate), error=str(exc)
        ) from exc
    return parse_bytes(data, candidate.suffix.lower(), candidate.name)


def parse_bytes(data: bytes, suffix: str, name: str = "resource") -> ParsedDocument:
    extension = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
    try:
        if extension == ".pdf":
            return _parse_pdf(data)
        if extension in {".md", ".markdown", ".txt"}:
            return ParsedDocument(_logical_pages(_decode(data)), "text/markdown" if extension != ".txt" else "text/plain")
        if extension == ".docx":
            return ParsedDocument(_parse_docx(data), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        if extension == ".pptx":
            return ParsedDocument(_parse_pptx(data), "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        if extension == ".epub":
            return ParsedDocument(_parse_epub(data), "application/epub+zip")
        if extension in {".html", ".htm"}:
            return ParsedDocument(_html_pages(_decode(data)), "text/html")
    except LibraryError:
        raise
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise LibraryError(
            f"资源解析失败: {name}", kind="parse_failed", filename=name, error=str(exc)
        ) from exc
    raise LibraryError(
        f"不支持的资源格式: {extension or name}",
        kind="unsupported_format",
        filename=name,
        extension=extension,
    )


def _parse_pdf(data: bytes) -> ParsedDocument:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]

        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception:
                decrypted = 0
            if not decrypted:
                raise LibraryError("PDF 受密码保护，无法导入", kind="encrypted_document")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            pages.append(DocumentPage(number, str(page.extract_text() or ""), ""))
        return ParsedDocument(pages, "application/pdf")
    except ImportError:
        pass
    except LibraryError:
        raise
    except Exception as exc:
        raise LibraryError("PDF 解析失败", kind="parse_failed", error=str(exc)) from exc

    # The development environment may already provide PyMuPDF.  Keep this
    # fallback optional; the declared core dependency remains pypdf.
    try:
        import fitz  # type: ignore[import-not-found]

        document = fitz.open(stream=data, filetype="pdf")
        if getattr(document, "needs_pass", False):
            raise LibraryError("PDF 受密码保护，无法导入", kind="encrypted_document")
        pages = [
            DocumentPage(number, page.get_text("text") or "", "")
            for number, page in enumerate(document, start=1)
        ]
        document.close()
        return ParsedDocument(pages, "application/pdf")
    except LibraryError:
        raise
    except ImportError as exc:
        raise LibraryError("PDF 解析器未安装", kind="parser_unavailable", format="pdf") from exc
    except Exception as exc:
        raise LibraryError("PDF 解析失败", kind="parse_failed", error=str(exc)) from exc


def _parse_docx(data: bytes) -> list[DocumentPage]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    lines: list[str] = []
    for paragraph in root.iter(_q("p")):
        text = "".join(node.text or "" for node in paragraph.iter(_q("t"))).strip()
        if text:
            lines.append(text)
    return _logical_pages("\n".join(lines))


def _parse_pptx(data: bytes) -> list[DocumentPage]:
    pages: list[DocumentPage] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda value: int(re.search(r"(\d+)", value).group(1)),  # type: ignore[union-attr]
        )
        for number, name in enumerate(names, start=1):
            root = ET.fromstring(archive.read(name))
            text = " ".join(node.text or "" for node in root.iter() if _local_name(node.tag) == "t").strip()
            pages.append(DocumentPage(number, text, f"Slide {number}"))
    return pages


def _parse_epub(data: bytes) -> list[DocumentPage]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            node.attrib.get("full-path", "")
            for node in container.iter()
            if node.tag.rsplit("}", 1)[-1] == "rootfile"
        )
        if not rootfile:
            raise LibraryError("EPUB 缺少 OPF 清单", kind="parse_failed")
        opf = ET.fromstring(archive.read(rootfile))
        base = posixpath.dirname(rootfile)
        manifest = {
            node.attrib.get("id", ""): posixpath.normpath(posixpath.join(base, node.attrib.get("href", "")))
            for node in opf.iter()
            if node.tag.rsplit("}", 1)[-1] == "item"
        }
        pages: list[DocumentPage] = []
        next_page = 1
        for number, itemref in enumerate(
            (node for node in opf.iter() if node.tag.rsplit("}", 1)[-1] == "itemref"), start=1
        ):
            href = manifest.get(itemref.attrib.get("idref", ""))
            if not href or href not in archive.namelist():
                continue
            text = _decode(archive.read(href))
            chapter_pages = _html_pages(text, start_page=next_page)
            pages.extend(chapter_pages)
            next_page = pages[-1].page + 1 if pages else next_page
    return pages or [DocumentPage(1, "", "")]


def _html_pages(text: str, *, start_page: int = 1) -> list[DocumentPage]:
    parser = _VisibleHtml()
    parser.feed(text)
    body = "\n".join(parser.parts)
    pages = _logical_pages(body)
    for index, page in enumerate(pages, start=start_page):
        page.page = index
        if not page.section and parser.headings:
            page.section = parser.headings[min(index - start_page, len(parser.headings) - 1)]
    return pages


def _logical_pages(text: str, *, page_chars: int = 4000) -> list[DocumentPage]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    blocks: list[tuple[str, list[str]]] = []
    heading = ""
    current: list[str] = []
    for line in lines:
        if not line:
            continue
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match and current:
            blocks.append((heading, current))
            current = []
        if match:
            heading = match.group(1).strip()
            current.append(heading)
        else:
            current.append(line)
    if current:
        blocks.append((heading, current))
    if not blocks and text.strip():
        blocks = [("", [text.strip()])]

    pages: list[DocumentPage] = []
    for section, block in blocks:
        joined = "\n".join(block).strip()
        for offset in range(0, len(joined), page_chars):
            pages.append(DocumentPage(len(pages) + 1, joined[offset : offset + page_chars], section))
    return pages or [DocumentPage(1, "", "")]


def _q(local: str) -> str:
    return f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{local}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


__all__ = ["ParsedDocument", "parse_bytes", "parse_path"]
