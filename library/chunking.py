"""Page-preserving chunking for the resource index."""

from __future__ import annotations

import re

from library.models import ChunkRecord, DocumentPage


def normalize_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\x00", "")).strip()


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
                    )
                )
                ordinal += 1
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


__all__ = ["chunk_pages", "normalize_text"]
