"""Page-preserving chunking for the resource index."""

from __future__ import annotations

import re

from library.models import ChunkRecord, DocumentPage


def normalize_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\x00", "")).strip()


def chunking_version(chunk_size: int, overlap: int) -> str:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller")
    return f"page-char-overlap-v1:size={int(chunk_size)}:overlap={int(overlap)}"


def structured_chunking_version(chunk_size: int, overlap: int) -> str:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller")
    return f"page-paragraph-code-v1:size={int(chunk_size)}:overlap={int(overlap)}"


def chunk_pages(
    pages: list[DocumentPage],
    *,
    resource_id: str,
    document_id: str,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[ChunkRecord]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller")
    chunks: list[ChunkRecord] = []
    ordinal = 0
    for page in pages:
        text = normalize_text(page.text)
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            piece = text[start:end].strip()
            if piece:
                chunks.append(
                    ChunkRecord(
                        chunk_id=f"{document_id}:p{page.page}:c{ordinal}",
                        document_id=document_id,
                        resource_id=resource_id,
                        page=page.page,
                        ordinal=ordinal,
                        text=piece,
                        section=page.section,
                        printed_page=page.printed_page,
                        chapter=page.chapter,
                        reliable=page.reliable,
                    )
                )
                ordinal += 1
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


def chunk_pages_structured(
    pages: list[DocumentPage],
    *,
    resource_id: str,
    document_id: str,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[ChunkRecord]:
    """Chunk by paragraphs and fenced code blocks while preserving page locators."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller")
    chunks: list[ChunkRecord] = []
    ordinal = 0
    for page in pages:
        text = normalize_text(page.text)
        if not text:
            continue
        lines = text.splitlines()
        blocks: list[str] = []
        current: list[str] = []
        in_fence = False
        for line in lines:
            if line.strip().startswith("```"):
                if current and not in_fence:
                    blocks.append("\n".join(current).strip())
                    current = []
                current.append(line)
                in_fence = not in_fence
                if not in_fence:
                    blocks.append("\n".join(current).strip())
                    current = []
                continue
            if not line.strip() and not in_fence:
                if current:
                    blocks.append("\n".join(current).strip())
                    current = []
                continue
            current.append(line)
        if current:
            blocks.append("\n".join(current).strip())

        pieces: list[str] = []
        buffer = ""
        for block in blocks:
            if len(block) > chunk_size:
                if buffer:
                    pieces.append(buffer)
                    buffer = ""
                start = 0
                while start < len(block):
                    end = min(len(block), start + chunk_size)
                    if end < len(block):
                        boundary = max(block.rfind("\n", start, end), block.rfind("。", start, end),
                                       block.rfind(";", start, end))
                        if boundary > start + chunk_size // 2:
                            end = boundary + (1 if block[boundary] != "\n" else 0)
                    piece = block[start:end].strip()
                    if piece:
                        pieces.append(piece)
                    if end >= len(block):
                        break
                    start = max(start + 1, end - overlap)
                continue
            combined = f"{buffer}\n\n{block}" if buffer else block
            if buffer and len(combined) > chunk_size:
                pieces.append(buffer)
                tail = buffer[-overlap:] if overlap else ""
                overlapped = f"{tail}\n\n{block}".strip() if tail else block
                buffer = overlapped if len(overlapped) <= chunk_size else block
            else:
                buffer = combined
        if buffer:
            pieces.append(buffer)
        for piece in pieces:
            chunks.append(ChunkRecord(
                chunk_id=f"{document_id}:p{page.page}:c{ordinal}",
                document_id=document_id, resource_id=resource_id, page=page.page,
                ordinal=ordinal, text=piece, section=page.section,
                printed_page=page.printed_page, chapter=page.chapter, reliable=page.reliable,
            ))
            ordinal += 1
    return chunks


__all__ = ["chunk_pages", "chunk_pages_structured", "chunking_version", "normalize_text",
           "structured_chunking_version"]
