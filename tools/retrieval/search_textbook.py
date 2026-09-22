"""Retrieval tool backed by the MVP-4 resource library."""

from __future__ import annotations

from typing import Any, Callable

from runtime.tools.base import FunctionTool

__all__ = ["SEARCH_TEXTBOOK_TOOL", "STATUS_INSUFFICIENT", "build_search_textbook_tool"]

SEARCH_TEXTBOOK_TOOL = "search_textbook"
STATUS_INSUFFICIENT = "insufficient_evidence"

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "学生问题中的检索关键词或问题"},
        "course_id": {"type": "string", "description": "课程标识"},
        "resource_type": {"type": "string", "description": "资源类型过滤"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签过滤"},
        "concept_ids": {"type": "array", "items": {"type": "string"}, "description": "知识点标识"},
        "top_k": {"type": "integer", "description": "返回证据条数上限", "default": 5},
    },
    "required": ["query"],
}


async def search_textbook(arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Fallback used when the composition root has no library instance."""
    del ctx
    query = str(arguments.get("query") or "")
    return {
        "evidence": [],
        "status": STATUS_INSUFFICIENT,
        "query": query,
        "course_id": str(arguments.get("course_id") or ""),
        "concept_ids": list(arguments.get("concept_ids") or []),
        "top_k": int(arguments.get("top_k") or 5),
        "missing": ["course_corpus", "vector_index"],
        "owner": "MVP-4 resource library",
    }


def build_search_textbook_tool(
    searcher: Callable[..., dict[str, Any]] | None = None,
) -> FunctionTool:
    """Build the stable tool name with an optional real-library searcher."""

    async def handler(arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        if searcher is None:
            return await search_textbook(arguments, ctx)
        result = searcher(
            str(arguments.get("query") or ""),
            top_k=int(arguments.get("top_k") or 5),
            course_id=str(arguments.get("course_id") or ""),
            resource_type=str(arguments.get("resource_type") or ""),
            tags=list(arguments.get("tags") or []),
            owner_id=str(ctx.get("learner_id") or "local"),
        )
        if hasattr(result, "__await__"):
            result = await result
        return dict(result or {})

    return FunctionTool(
        name=SEARCH_TEXTBOOK_TOOL,
        handler=handler,
        description=(
            "检索课程资源中的可定位证据片段，返回 document_id / chunk_id / page / source；"
            "没有可信命中时返回 insufficient_evidence。"
        ),
        parameters=_PARAMETERS,
        requires_approval=False,
    )
