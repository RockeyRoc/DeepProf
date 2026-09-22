"""Stable domain DTOs for resources, parsed pages and retrieval hits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ResourceType = Literal["textbook", "lecture", "paper", "quiz_bank", "student_upload"]
SourceType = Literal["import", "upload", "crawl"]
ResourceStatus = Literal["draft", "active", "archived"]
Visibility = Literal["private", "public"]


@dataclass(slots=True)
class ResourceRecord:
    resource_id: str
    document_id: str
    course_id: str | None
    type: ResourceType
    title: str
    tags: list[str]
    source_type: SourceType
    source_url: str
    license: str
    content_hash: str
    status: ResourceStatus = "draft"
    owner_id: str = "local"
    visibility: Visibility = "private"
    version_of: str | None = None
    created_by: str = "local"
    created_at: str = ""
    updated_at: str = ""

    @property
    def hash(self) -> str:
        """Compatibility alias matching the design document's field name."""
        return self.content_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "document_id": self.document_id,
            "course_id": self.course_id,
            "type": self.type,
            "title": self.title,
            "tags": list(self.tags),
            "source_type": self.source_type,
            "source_url": self.source_url,
            "license": self.license,
            "hash": self.content_hash,
            "status": self.status,
            "owner_id": self.owner_id,
            "visibility": self.visibility,
            "version_of": self.version_of,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class Document:
    """Persisted document metadata associated with one resource."""

    document_id: str
    resource_id: str
    filename: str
    media_type: str
    page_count: int = 0
    pages: list["DocumentPage"] = field(default_factory=list)


@dataclass(slots=True)
class DocumentPage:
    page: int
    text: str
    section: str = ""


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    resource_id: str
    page: int
    ordinal: int
    text: str
    section: str = ""
    vector: list[float] = field(default_factory=list)

    def locator(self, *, source: str) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "page": self.page,
            "source": source,
        }


# Backward-compatible descriptive name used by the chunker.
ChunkRecord = Chunk


@dataclass(slots=True)
class SearchHit:
    document_id: str
    chunk_id: str
    page: int
    source: str
    text: str
    score: float
    section: str = ""
    resource_id: str = ""
    course_id: str | None = None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "page": self.page,
            "source": self.source,
            "text": self.text,
            "score": round(float(self.score), 6),
            "section": self.section,
        }


@dataclass(slots=True)
class ImportResult:
    resource: ResourceRecord
    duplicate: bool = False
    indexed: bool = True
    chunk_count: int = 0
    warnings: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource.to_dict(),
            "resource_id": self.resource.resource_id,
            "duplicate": self.duplicate,
            "indexed": self.indexed,
            "chunks": self.chunk_count,
            "warnings": list(self.warnings),
        }


__all__ = [
    "Chunk",
    "ChunkRecord",
    "Document",
    "DocumentPage",
    "ImportResult",
    "ResourceRecord",
    "ResourceStatus",
    "ResourceType",
    "SearchHit",
    "SourceType",
    "Visibility",
]
