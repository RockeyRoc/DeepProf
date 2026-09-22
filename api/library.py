"""Resource-library HTTP views used by the Desktop and CLI clients."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from library.service import ResourceLibrary

router = APIRouter(prefix="/library", tags=["library"])


def _library(request: Request) -> ResourceLibrary:
    library = getattr(request.app.state.service, "library", None)
    if library is None:
        raise RuntimeError("resource library is not attached")
    return library


class ActivateRequest(BaseModel):
    actor_id: str = "local"


@router.get("/resources")
async def list_resources(
    request: Request,
    course_id: str | None = None,
    resource_type: str | None = Query(default=None, alias="type"),
    status: str | None = None,
    owner_id: str | None = "local",
    tags: str = "",
) -> list[dict[str, Any]]:
    return _library(request).list_resources(
        course_id=course_id,
        resource_type=resource_type,
        status=status,
        owner_id=owner_id or "local",
        tags=[item.strip() for item in tags.split(",") if item.strip()],
    )


@router.get("/search")
async def search_resources(
    request: Request,
    query: str,
    top_k: int = 5,
    course_id: str = "",
    resource_type: str = "",
    status: str = "active",
    owner_id: str = "local",
    tags: str = "",
) -> dict[str, Any]:
    return _library(request).search(
        query,
        top_k=top_k,
        course_id=course_id,
        resource_type=resource_type,
        status=status,
        tags=[item.strip() for item in tags.split(",") if item.strip()],
        owner_id=owner_id,
    )


@router.get("/resources/{resource_id}/preview")
async def preview_resource(
    resource_id: str,
    request: Request,
    limit: int = 100,
    owner_id: str = "local",
) -> list[dict[str, Any]]:
    return _library(request).preview(resource_id, limit=limit, owner_id=owner_id)


@router.post("/resources/{resource_id}/activate")
async def activate_resource(
    resource_id: str,
    request: Request,
    payload: ActivateRequest | None = None,
) -> dict[str, Any]:
    record = _library(request).activate(resource_id, actor_id=(payload.actor_id if payload else "local"))
    return record.to_dict()


__all__ = ["router"]
