"""Resource-library application service."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Callable

from library.chunking import chunk_pages
from library.crawler import CrawlPolicy, SingleUrlCrawler
from library.embeddings import Embedder, HashingEmbedder
from library.errors import LibraryError
from library.models import ImportResult, ResourceRecord
from library.parsers import parse_bytes
from runtime.core.events import new_id, utc_now
from runtime.storage.resource_store import SqliteResourceStore


class ResourceLibrary:
    """Coordinates files, parsing, indexing, retrieval and audit metadata."""

    def __init__(
        self,
        store: SqliteResourceStore,
        *,
        library_root: str | Path,
        embedder: Embedder | None = None,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        max_import_bytes: int = 50_000_000,
        path_allowed: Callable[[str | Path], bool] | None = None,
        crawler: SingleUrlCrawler | None = None,
        allowlist: list[str] | None = None,
        tos_confirmed_domains: list[str] | None = None,
        crawl_delay_seconds: float = 1.0,
    ) -> None:
        self.store = store
        self.library_root = Path(library_root)
        self.files_root = self.library_root / "files"
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or HashingEmbedder()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_import_bytes = max_import_bytes
        self.path_allowed = path_allowed
        self.crawler = crawler or SingleUrlCrawler(
            CrawlPolicy(
                allowlist=list(allowlist or []),
                tos_confirmed_domains=list(tos_confirmed_domains or []),
                delay_seconds=crawl_delay_seconds,
                max_bytes=max_import_bytes,
            )
        )

    def import_path(
        self,
        path: str | Path,
        *,
        source_type: str = "import",
        metadata: dict[str, Any] | None = None,
        actor_id: str = "local",
        as_new_version: bool = False,
        activate: bool = False,
    ) -> ImportResult:
        candidate = Path(path).expanduser().resolve()
        if self.path_allowed is not None and not self.path_allowed(candidate):
            raise LibraryError("资源路径不在沙箱白名单内", kind="sandbox_denied", path=str(candidate))
        if not candidate.is_file():
            raise LibraryError("资源文件不存在", kind="file_not_found", path=str(candidate))
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            raise LibraryError("无法读取资源文件属性", kind="read_failed", path=str(candidate)) from exc
        if size > self.max_import_bytes:
            raise LibraryError(
                "资源文件超过大小限制", kind="size_limit", path=str(candidate), max_bytes=self.max_import_bytes
            )
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise LibraryError("无法读取资源文件", kind="read_failed", path=str(candidate)) from exc
        result = self._ingest_bytes(
            data,
            filename=candidate.name,
            source_url=str(candidate),
            source_type=source_type,
            metadata=metadata,
            actor_id=actor_id,
            as_new_version=as_new_version,
            activate=activate,
        )
        if size > 10_000_000:
            result.warnings.append("文件较大，后续 OCR 或重新索引可能需要更长时间")
        return result

    async def crawl(
        self,
        url: str,
        *,
        metadata: dict[str, Any] | None = None,
        actor_id: str = "local",
        as_new_version: bool = False,
        activate: bool = False,
    ) -> ImportResult:
        fetched = await self.crawler.fetch(url)
        if not fetched.extension:
            raise LibraryError(
                "无法根据 URL 或 Content-Type 判断资源格式",
                kind="unsupported_format",
                url=fetched.url,
            )
        result = self._ingest_bytes(
            fetched.content,
            filename=Path(fetched.url.split("?", 1)[0]).name or f"download{fetched.extension}",
            source_url=fetched.url,
            source_type="crawl",
            metadata=metadata,
            actor_id=actor_id,
            as_new_version=as_new_version,
            activate=activate,
            extra_events=[
                {
                    "type": "library.crawled",
                    "payload": {"source_url": fetched.url},
                }
            ],
        )
        return result

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        course_id: str = "",
        resource_type: str = "",
        status: str = "active",
        tags: list[str] | None = None,
        owner_id: str = "local",
        min_score: float = 0.10,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"status": "insufficient_evidence", "evidence": [], "missing": ["query"]}
        query_vector = self.embedder.embed([query])[0]
        raw_hits = self.store.search(
            query_vector,
            score=self.embedder.similarity if hasattr(self.embedder, "similarity") else _dot,
            top_k=top_k,
            course_id=course_id or None,
            resource_type=resource_type or None,
            status=status or "active",
            tags=tags or [],
            owner_id=owner_id or "local",
            min_score=min_score,
        )
        evidence = [
            {
                "document_id": hit["document_id"],
                "chunk_id": hit["chunk_id"],
                "page": hit["page"],
                "source": hit["source"],
                "text": hit["text"],
                "score": round(float(hit["score"]), 6),
                "section": hit["section"],
            }
            for hit in raw_hits
            if hit.get("document_id") and hit.get("chunk_id") and hit.get("page") is not None and hit.get("source")
        ]
        if not evidence:
            return {
                "status": "insufficient_evidence",
                "evidence": [],
                "query": query,
                "missing": ["matching_active_resource"],
            }
        return {"status": "ok", "evidence": evidence, "query": query, "count": len(evidence)}

    def list_resources(self, **filters: Any) -> list[dict[str, Any]]:
        filters["owner_id"] = filters.get("owner_id") or "local"
        return self.store.list_resources(**filters)

    def preview(
        self,
        resource_id: str,
        *,
        limit: int = 100,
        owner_id: str = "local",
    ) -> list[dict[str, Any]]:
        record = self.store.get(resource_id)
        if record is None:
            raise LibraryError("资源不存在", kind="resource_not_found", resource_id=resource_id)
        if record.get("visibility") != "public" and record.get("owner_id") != (owner_id or "local"):
            raise LibraryError("无权查看私有资源", kind="resource_forbidden", resource_id=resource_id)
        return self.store.preview(resource_id, limit=limit)

    def activate(self, resource_id: str, *, actor_id: str = "local") -> ResourceRecord:
        existing = self.store.get(resource_id)
        if existing is None:
            raise LibraryError("资源不存在", kind="resource_not_found", resource_id=resource_id)
        if existing.get("visibility") != "public" and existing.get("owner_id") != (actor_id or "local"):
            raise LibraryError("无权激活私有资源", kind="resource_forbidden", resource_id=resource_id)
        record = self.store.activate(resource_id)
        if record is None:  # pragma: no cover - guarded by the lookup above
            raise LibraryError("资源不存在", kind="resource_not_found", resource_id=resource_id)
        self.store.record_operation(
            {
                "action": "activate",
                "resource_id": resource_id,
                "actor_id": actor_id,
                "status": "success",
            }
        )
        return _record(record)

    def _ingest_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        source_url: str,
        source_type: str,
        metadata: dict[str, Any] | None,
        actor_id: str,
        as_new_version: bool,
        activate: bool,
        extra_events: list[dict[str, Any]] | None = None,
    ) -> ImportResult:
        if source_type not in {"import", "upload", "crawl"}:
            raise LibraryError("未知资源来源类型", kind="invalid_request", source_type=source_type)
        if len(data) > self.max_import_bytes:
            raise LibraryError("资源文件超过大小限制", kind="size_limit", max_bytes=self.max_import_bytes)
        digest = hashlib.sha256(data).hexdigest()
        existing = self.store.find_by_hash(digest)
        if existing and not as_new_version:
            record = _record(existing[-1])
            self.store.record_operation(
                {
                    "action": "duplicate",
                    "resource_id": record.resource_id,
                    "source_url": source_url,
                    "actor_id": actor_id,
                    "status": "duplicate",
                    "hash": digest,
                }
            )
            events = []
            for event in extra_events or []:
                copied = {"type": event["type"], "payload": dict(event.get("payload") or {})}
                copied["payload"]["resource_id"] = record.resource_id
                if copied["type"] == "library.crawled":
                    copied["payload"]["hash"] = digest
                events.append(copied)
            events.append(
                {
                    "type": "library.imported",
                    "payload": {
                        "resource_id": record.resource_id,
                        "source_type": source_type,
                        "hash": digest,
                        "duplicate": True,
                    },
                }
            )
            return ImportResult(
                resource=record,
                duplicate=True,
                indexed=True,
                chunk_count=len(self.store.preview(record.resource_id, limit=100000)),
                events=events,
            )

        parsed = parse_bytes(data, Path(filename).suffix.lower(), filename)
        if not any(page.text.strip() for page in parsed.pages):
            raise LibraryError("资源不包含可解析文本", kind="empty_document", filename=filename)
        resource_id = new_id("res")
        document_id = "doc_" + hashlib.sha256(f"{digest}:{resource_id}".encode()).hexdigest()[:24]
        metadata = dict(metadata or {})
        now = utc_now()
        parent = existing[-1]["resource_id"] if existing and as_new_version else None
        record = ResourceRecord(
            resource_id=resource_id,
            document_id=document_id,
            course_id=str(metadata.get("course_id")) if metadata.get("course_id") else None,
            type=str(metadata.get("type") or ("student_upload" if source_type == "upload" else "textbook")),  # type: ignore[arg-type]
            title=str(metadata.get("title") or Path(filename).stem),
            tags=[str(tag) for tag in metadata.get("tags", [])],
            source_type=source_type,  # type: ignore[arg-type]
            source_url=source_url,
            license=str(metadata.get("license") or ""),
            content_hash=digest,
            status="active" if activate else "draft",
            owner_id=str(metadata.get("owner_id") or actor_id or "local"),
            visibility=str(metadata.get("visibility") or "private"),  # type: ignore[arg-type]
            version_of=parent,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        chunks = chunk_pages(
            parsed.pages,
            resource_id=resource_id,
            document_id=document_id,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
        )
        vectors = self.embedder.embed([chunk.text for chunk in chunks])
        chunk_rows = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "resource_id": chunk.resource_id,
                "page": chunk.page,
                "ordinal": chunk.ordinal,
                "section": chunk.section,
                "text": chunk.text,
                "vector": vector,
            }
            for chunk, vector in zip(chunks, vectors)
        ]
        stored_path = self.files_root / resource_id / Path(filename).name
        try:
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            stored_path.write_bytes(data)
        except OSError as exc:
            shutil.rmtree(stored_path.parent, ignore_errors=True)
            raise LibraryError(
                "无法复制资源到资源库", kind="write_failed", path=str(stored_path)
            ) from exc
        try:
            self.store.ingest(
                record.to_dict(),
                filename=Path(filename).name,
                media_type=parsed.media_type,
                page_count=len(parsed.pages),
                chunks=chunk_rows,
                operation={
                    "action": source_type,
                    "source_url": source_url,
                    "actor_id": actor_id,
                    "status": "success",
                    "parameters": {"filename": Path(filename).name, "embedder": getattr(self.embedder, "name", "unknown")},
                },
            )
        except Exception:
            shutil.rmtree(stored_path.parent, ignore_errors=True)
            raise
        events = []
        for event in extra_events or []:
            copied = {"type": event["type"], "payload": dict(event.get("payload") or {})}
            copied["payload"]["resource_id"] = resource_id
            if copied["type"] == "library.crawled":
                copied["payload"]["hash"] = digest
            events.append(copied)
        events.append(
            {
                "type": "library.imported",
                "payload": {"resource_id": resource_id, "source_type": source_type, "hash": digest, "duplicate": False},
            }
        )
        events.append(
            {
                "type": "library.indexed",
                "payload": {"resource_id": resource_id, "chunks": len(chunk_rows)},
            }
        )
        return ImportResult(resource=record, chunk_count=len(chunk_rows), events=events)


def _record(data: dict[str, Any]) -> ResourceRecord:
    return ResourceRecord(
        resource_id=str(data["resource_id"]),
        document_id=str(data["document_id"]),
        course_id=data.get("course_id"),
        type=data["type"],
        title=str(data.get("title", "")),
        tags=list(data.get("tags", [])),
        source_type=data["source_type"],
        source_url=str(data.get("source_url", "")),
        license=str(data.get("license", "")),
        content_hash=str(data.get("hash", "")),
        status=data.get("status", "draft"),
        owner_id=str(data.get("owner_id", "local")),
        visibility=data.get("visibility", "private"),
        version_of=data.get("version_of"),
        created_by=str(data.get("created_by", "local")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


__all__ = ["ResourceLibrary"]
