"""RAG Skill：教材检索与可追踪来源（DESIGNv0.6 §6.3 / §7.3 / §20.5）。

职责：调用检索工具取教材片段，把结果规范化成 Evidence 契约
（document_id / chunk_id / page / text / source），供教学图引用与前端定位。

硬性要求（§7.3、§12“不编造引用”）：
- 工具不可用、无命中、或返回结果缺少可定位标识时，
  **必须**返回 ``status="insufficient_evidence"`` 且 ``evidence`` 为空列表；
- 任何情况下都不生成、不猜测来源。

责任人：许阳毅（§20 资源库与教材语料；工具与语料由其交接）。
"""

from __future__ import annotations

from typing import Any

from tools.retrieval import SEARCH_TEXTBOOK_TOOL

SEARCH_TOOL = SEARCH_TEXTBOOK_TOOL

__all__ = ["INSUFFICIENT_NOTE", "RAGSkill", "SEARCH_TOOL"]

#: 结构化命中可能出现的字段名（工具实现方稍有差异时仍可解析）
_HIT_KEYS = ("hits", "evidence", "results", "chunks")

#: 无命中时的统一说明，便于前端与测试识别
INSUFFICIENT_NOTE = (
    "教材检索无命中或工具不可用：按 §7.3 返回 insufficient_evidence，不编造任何来源"
)


class RAGSkill:
    """教材检索 Skill：只回传可定位证据，不带证据就不给答案。"""

    name = "rag"
    description = "检索教材片段并返回可追踪来源；无命中明确返回 insufficient_evidence"
    uses_host = True

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        """入参：query / concept / top_k / course_id；出参：status + evidence 列表。"""
        query = str(input.get("query") or input.get("concept") or "").strip()
        concept = str(input.get("concept") or "")
        if not query:
            return self._insufficient("检索问题为空，无法检索")

        result = await host.call_tool(
            SEARCH_TEXTBOOK_TOOL,
            {
                "query": query,
                "top_k": int(input.get("top_k") or 5),
                "course_id": str(input.get("course_id") or ""),
                "resource_type": str(input.get("resource_type") or ""),
                "tags": list(input.get("tags") or []),
                "concept_ids": [concept] if concept else [],
            },
            ctx,
        )
        data = result if isinstance(result, dict) else {}
        evidence = _normalize_hits(data)
        if not evidence:
            return self._insufficient(
                str(data.get("status") or "检索结果缺少可定位标识（document_id/chunk_id/page）"),
                missing=list(data.get("missing") or []),
            )
        return {
            "status": "ok",
            "skill": self.name,
            "tool": SEARCH_TEXTBOOK_TOOL,
            "query": query,
            "concept": concept,
            "count": len(evidence),
            "evidence": evidence,
            "source": "tool:" + SEARCH_TEXTBOOK_TOOL,
        }

    # ---------- 内部 ----------
    def _insufficient(self, note: str, **extra: Any) -> dict[str, Any]:
        """统一的证据不足返回：evidence 必为空，绝不编造来源。"""
        return {
            "status": "insufficient_evidence",
            "skill": self.name,
            "tool": SEARCH_TEXTBOOK_TOOL,
            "count": 0,
            "evidence": [],
            "note": f"{INSUFFICIENT_NOTE}（原因：{note}）",
            **extra,
        }


def _normalize_hits(data: dict[str, Any]) -> list[dict[str, Any]]:
    """把工具返回的命中规范化为 Evidence 契约（§18.2）。

    只认带定位标识的命中：缺 document_id/chunk_id/page 的条目会被丢弃，
    因为无法定位的“来源”等于不可验证的来源。
    """
    raw_hits: list[Any] = []
    for key in _HIT_KEYS:
        value = data.get(key)
        if isinstance(value, list) and value:
            raw_hits = value
            break

    evidence: list[dict[str, Any]] = []
    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id") or item.get("doc_id") or "")
        chunk_id = str(item.get("chunk_id") or item.get("chunk") or "")
        page = item.get("page")
        if not document_id or not chunk_id or page is None:
            continue
        evidence.append(
            {
                "evidence_id": str(item.get("evidence_id") or f"{document_id}#{chunk_id}@{page}"),
                "document_id": document_id,
                "chunk_id": chunk_id,
                "page": page,
                "printed_page": item.get("printed_page"),
                "chapter": str(item.get("chapter") or item.get("section") or ""),
                "text": str(item.get("text") or item.get("content") or ""),
                "source": str(item.get("source") or item.get("title") or document_id),
                "score": item.get("score"),
            }
        )
    return evidence
