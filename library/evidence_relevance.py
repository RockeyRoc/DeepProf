"""Conservative checks for specialized claims against retrieved source text."""

from __future__ import annotations

from typing import Any


_CLAIM_TERMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("均摊", "摊还", "摊销", "amortized"), ("均摊", "摊还", "摊销", "amortiz")),
    (
        ("扩容因子", "扩展因子", "增长因子", "growth factor"),
        ("扩容因子", "扩展因子", "增长因子", "增长倍率", "扩容倍数", "扩展倍数", "增长比例", "growth factor"),
    ),
    (("严格证明", "证明其选择", "prove", "proof"), ("证明", "推导", "论证", "demonstrate", "proof")),
)


def supports_claim_specific_evidence(query: str, evidence: list[dict[str, Any]]) -> bool:
    """Require specialized requested claims to appear in retrieved passages.

    This does not certify semantic citation support. It is a fail-closed check
    for explicit technical claims that a lexical retriever can otherwise match
    to unrelated, locatable textbook fragments.
    """
    normalized_query = str(query or "").casefold()
    source_text = "\n".join(str(item.get("text") or "") for item in evidence).casefold()
    if not source_text:
        return False
    for request_terms, source_terms in _CLAIM_TERMS:
        if any(term in normalized_query for term in request_terms) and not any(
            term in source_text for term in source_terms
        ):
            return False
    return True
