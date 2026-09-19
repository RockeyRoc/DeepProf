"""RAG Skill：教材检索与可追踪来源（DESIGNv0.4 §6.3 / §7.3 / §18.2）。

职责：调用 runtime 的原子工具 `search_textbook` 取教材片段，
把结果规范化成 Evidence 契约（document_id / chunk_id / page / text / source），
供教学图引用与前端定位。

硬性要求（§7.3、§12"不编造引用"）：
- 工具不可用、无命中、或返回结果缺少可定位标识时，
  **必须**返回 status="insufficient_evidence"，且 evidence 为空列表；
- 任何情况下都不生成、不猜测来源。

责任人：许阳毅（§16.2 教材 RAG 与课程语料；工具与语料由其交接）。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimeContext, RuntimePort
from runtime.skills import Skill, SkillDescriptor

from .. import to_ctx_dict

#: 教材检索工具名（runtime/tools 注册；由许阳毅实现，保留 document_id/page/chunk_id）
SEARCH_TOOL = "search_textbook"

#: 结构化命中可能出现的字段名（工具实现方稍有差异时仍可解析）
_HIT_KEYS = ("hits", "evidence", "results", "chunks")

#: 无命中时的统一说明，便于前端与测试识别
INSUFFICIENT_NOTE = (
    "教材检索无命中或工具不可用：按 §7.3 返回 insufficient_evidence，不编造任何来源"
)


class RAGSkill(Skill):
    """教材检索 Skill：只回传可定位证据，不带证据就不给答案。"""

    descriptor = SkillDescriptor(
        name="rag",
        description="检索教材片段并返回可追踪来源；无命中明确返回 insufficient_evidence",
        when_to_use="教学图 Assess / Teach / Correct 需要教材依据时",
        version="0.1.0",
        owner="许阳毅",
        tags=["rag", "retrieval", "evidence", "citation"],
    )

    async def handle(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict[str, Any]:
        """入参：query / concept / top_k / course_id；出参：status + evidence 列表。"""
        query = str(payload.get("query") or payload.get("concept") or "").strip()
        concept = str(payload.get("concept") or "")
        top_k = int(payload.get("top_k") or 4)
        if not query:
            return self._insufficient("检索问题为空，无法检索")

        result = await port.call_tool(
            SEARCH_TOOL,
            {
                "query": query,
                "top_k": top_k,
                "course_id": str(payload.get("course_id") or ""),
                "concept": concept,
            },
            to_ctx_dict(ctx),
        )
        if not result.get("ok"):
            error = result.get("error") or {}
            return self._insufficient(
                "教材检索工具返回失败或不存在",
                error=error if isinstance(error, dict) else {},
            )

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        evidence = _normalize_hits(data)
        if not evidence:
            return self._insufficient(
                "检索结果缺少可定位标识（document_id/chunk_id/page），无法作为引用",
                error={},
                payload_keys=sorted(data),
            )
        return {
            "status": "ok",
            "skill": self.name,
            "tool": SEARCH_TOOL,
            "query": query,
            "concept": concept,
            "count": len(evidence),
            "evidence": evidence,
            "source": "tool:" + SEARCH_TOOL,
        }

    # ---------- 内部 ----------
    def _insufficient(self, note: str, **extra: Any) -> dict[str, Any]:
        """统一的证据不足返回：evidence 必为空，绝不编造来源。"""
        return {
            "status": "insufficient_evidence",
            "skill": self.name,
            "tool": SEARCH_TOOL,
            "count": 0,
            "evidence": [],
            "note": f"{INSUFFICIENT_NOTE}（原因：{note}）",
            **extra,
        }


def _normalize_hits(data: dict[str, Any]) -> list[dict[str, Any]]:
    """把工具返回的命中规范化为 Evidence 契约（§18.2）。

    只认带定位标识的命中：缺 document_id/chunk_id/page 的条目会被丢弃，
    因为无法定位的"来源"等于不可验证的来源。
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
                "evidence_id": str(
                    item.get("evidence_id") or f"{document_id}#{chunk_id}@{page}"
                ),
                "document_id": document_id,
                "chunk_id": chunk_id,
                "page": page,
                "text": str(item.get("text") or item.get("content") or ""),
                "source": str(item.get("source") or item.get("title") or document_id),
                "score": item.get("score"),
            }
        )
    return evidence


__all__ = ["INSUFFICIENT_NOTE", "RAGSkill", "SEARCH_TOOL"]