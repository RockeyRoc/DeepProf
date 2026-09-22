from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from library.crawler import CrawlPolicy
from library.embeddings import HashingEmbedder
from library.errors import LibraryError
from library.parsers import parse_bytes
from library.service import ResourceLibrary
from runtime.storage.migrations import connect_memory
from runtime.storage.resource_store import SqliteResourceStore
from tools.retrieval import build_search_textbook_tool


def make_library(tmp_path: Path) -> ResourceLibrary:
    return ResourceLibrary(
        SqliteResourceStore(connect_memory()),
        library_root=tmp_path / "library",
        chunk_size=180,
        chunk_overlap=30,
    )


def test_import_hash_dedup_and_locator_search(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    path = tmp_path / "calculus.md"
    path.write_text("# 导数\n导数表示切线斜率和瞬时变化率。\n# 积分\n定积分可以理解为带符号面积。", encoding="utf-8")

    first = library.import_path(
        path,
        metadata={"course_id": "math", "license": "CC0", "tags": ["calculus"]},
        activate=True,
    )
    duplicate = library.import_path(path, metadata={"course_id": "math"}, activate=True)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.resource.resource_id == first.resource.resource_id
    result = library.search("切线斜率", course_id="math")
    assert result["status"] == "ok"
    hit = result["evidence"][0]
    assert {"document_id", "chunk_id", "page", "source", "text"} <= set(hit)
    assert hit["page"] == 1


def test_draft_and_private_resources_are_not_publicly_retrievable(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    path = tmp_path / "private.txt"
    path.write_text("只有 Alice 可以看到的量子力学笔记。", encoding="utf-8")
    result = library.import_path(
        path,
        source_type="upload",
        metadata={"owner_id": "alice", "license": "private"},
    )
    assert result.resource.status == "draft"
    assert library.search("量子力学", owner_id="bob")["status"] == "insufficient_evidence"
    library.activate(result.resource.resource_id, actor_id="alice")
    assert library.search("量子力学", owner_id="bob")["status"] == "insufficient_evidence"
    assert library.search("量子力学", owner_id="alice")["status"] == "ok"


def test_no_evidence_is_explicit(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    path = tmp_path / "known.md"
    path.write_text("# 已知\n这里讨论导数。", encoding="utf-8")
    library.import_path(path, metadata={"license": "CC0"}, activate=True)
    result = library.search("完全不相关的火星主题")
    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []


def test_real_search_tool_is_injectable(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    path = tmp_path / "source.md"
    path.write_text("# 线性代数\n矩阵的行列式不为零时，方阵可逆。", encoding="utf-8")
    library.import_path(path, metadata={"course_id": "algebra", "license": "CC0"}, activate=True)
    tool = build_search_textbook_tool(library.search)
    result = asyncio.run(tool.execute({"query": "矩阵 行列式 可逆", "course_id": "algebra"}, {"learner_id": "local"}))
    assert result["status"] == "ok"
    assert result["evidence"][0]["document_id"]


def test_embedder_is_deterministic_and_normalized() -> None:
    embedder = HashingEmbedder()
    first = embedder.embed(["梯度下降 学习率"])[0]
    second = embedder.embed(["梯度下降 学习率"])[0]
    assert first == second
    assert len(first) == 384
    assert pytest.approx(sum(value * value for value in first), abs=1e-6) == 1.0


def test_parser_supports_text_zip_formats_and_rejects_unknown() -> None:
    docx = _zip_bytes({"word/document.xml": "<document xmlns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><body><p><t>DOCX 文本</t></p></body></document>".encode()})
    pptx = _zip_bytes({"ppt/slides/slide1.xml": "<s xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'><a:t>Slide 文本</a:t></s>".encode()})
    epub = _zip_bytes(
        {
            "META-INF/container.xml": b"<container><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>",
            "OEBPS/content.opf": b"<package><manifest><item id='s1' href='s1.xhtml'/></manifest><spine><itemref idref='s1'/></spine></package>",
            "OEBPS/s1.xhtml": "<html><body><h1>EPUB 章节</h1><p>EPUB 文本</p></body></html>".encode(),
        }
    )
    assert "DOCX 文本" in parse_bytes(docx, ".docx").pages[0].text
    assert parse_bytes(pptx, ".pptx").pages[0].page == 1
    assert "EPUB 文本" in parse_bytes(epub, ".epub").pages[0].text
    assert "网页文本" in parse_bytes("<html><body>网页文本</body></html>".encode(), ".html").pages[0].text
    with pytest.raises(LibraryError) as excinfo:
        parse_bytes(b"x", ".bin")
    assert excinfo.value.details["kind"] == "unsupported_format"


def test_pdf_parser_preserves_page_locator() -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "PDF page one")
    data = document.tobytes()
    document.close()

    parsed = parse_bytes(data, ".pdf", "sample.pdf")
    assert parsed.media_type == "application/pdf"
    assert parsed.pages[0].page == 1
    assert "PDF page one" in parsed.pages[0].text


def test_crawl_policy_defaults_to_deny_without_explicit_consent() -> None:
    with pytest.raises(LibraryError) as excinfo:
        CrawlPolicy().check("https://example.com/course.pdf")
    assert excinfo.value.details["kind"] == "domain_denied"


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()
