"""Local course catalogue and the configured textbook import action."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from runtime.core.events import RuntimeEvent, new_id

router = APIRouter(tags=["courses"])
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "courses" / "data_structures_c" / "manifest.json"


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@router.get("/courses")
async def list_courses(request: Request) -> list[dict[str, Any]]:
    manifest = _manifest()
    library = request.app.state.service.library
    resources = library.list_resources(course_id=manifest["course_id"], owner_id="local") if library else []
    active = next((item for item in resources if item.get("status") == "active"), None)
    return [{
        "course_id": manifest["course_id"],
        "title": manifest["title"],
        "concept_count": len(manifest["concepts"]),
        "configured": bool(request.app.state.service.settings.data_structures_pdf_path),
        "imported": active is not None,
        "resource_id": active.get("resource_id") if active else None,
        "content_hash": active.get("hash") if active else None,
        "concepts": manifest["concepts"],
        "source": manifest["source"],
        "page_mapping": manifest["page_mapping"],
    }]


@router.post("/courses/{course_id}/import")
async def import_course(course_id: str, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    manifest = _manifest()
    if course_id != manifest["course_id"]:
        raise HTTPException(status_code=404, detail={"code": "course_not_found", "message": "课程不存在"})
    path = service.settings.resolved_data_structures_pdf_path
    if path is None:
        raise HTTPException(status_code=409, detail={"code": "textbook_not_configured", "message": "请先在本机配置 DEEPPROF_DATA_STRUCTURES_PDF"})
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "textbook_not_found", "message": "配置的教材文件不存在"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.lower() != str(manifest["source"]["sha256"]).lower():
        raise HTTPException(status_code=409, detail={"code": "textbook_hash_mismatch", "message": "教材文件哈希与课程清单不一致"})
    library = service.library
    if library is None:
        raise HTTPException(status_code=503, detail={"code": "library_unavailable", "message": "教材库不可用"})

    result = await run_in_threadpool(
        library.import_path,
        path,
        source_type="import",
        metadata={
            "course_id": manifest["course_id"],
            "title": manifest["source"]["title"],
            "type": "textbook",
            "visibility": "public",
            "tags": ["course:data-structures", "source:verified-local"],
            "license": manifest["source"]["license_note"],
            "source_url": manifest["source"]["source_uri"],
            "chapter_ranges": manifest["page_mapping"]["chapter_ranges"],
        },
        actor_id="local",
        activate=True,
    )
    trace_id = new_id("trc")
    for item in result.events:
        await service.emit(RuntimeEvent(
            type=str(item["type"]),
            payload=dict(item.get("payload") or {}),
            trace_id=trace_id,
            source="library",
            client_id="cli",
            surface="cli",
        ).to_dict())
    payload = result.to_dict()
    payload["warnings"] = result.warnings
    payload["pdf_pages"] = 347
    payload["trace_id"] = trace_id
    return payload
