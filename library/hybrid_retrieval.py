"""Auditable rank fusion and optional reranking for research retrieval profiles."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, Sequence


class Reranker(Protocol):
    name: str
    model_version: str

    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


def retrieval_config_id(index_profile: dict[str, Any], *, strategy: str, top_k: int,
                        candidate_k: int = 50, rrf_constant: int = 60,
                        dense_min_score: float | None = None) -> str:
    config = {"schema_version": "deepprof-retrieval-config-v1",
              "index_profile_id": index_profile.get("config_id"), "strategy": strategy,
              "top_k": int(top_k), "candidate_k": int(candidate_k),
              "rrf_constant": int(rrf_constant) if strategy == "hybrid_rrf" else None,
              "dense_min_score": float(dense_min_score) if strategy == "dense" else None}
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def reciprocal_rank_fusion(rankings: dict[str, Sequence[dict[str, Any]]], *,
                           rank_constant: int = 60, candidate_limit: int = 50) -> list[dict[str, Any]]:
    """Fuse ranked candidate lists while retaining each stage's raw score."""
    if rank_constant < 1 or candidate_limit < 1:
        raise ValueError("invalid_rrf_configuration")
    fused: dict[tuple[str, str, int], dict[str, Any]] = {}
    for stage, rows in rankings.items():
        for rank, row in enumerate(rows[:candidate_limit], start=1):
            key = (str(row.get("document_id") or ""), str(row.get("chunk_id") or ""),
                   int(row.get("page") or 0))
            if not key[0] or not key[1] or key[2] <= 0:
                continue
            candidate = fused.setdefault(key, {**row, "retrieval_stage_scores": {}, "rrf_score": 0.0})
            candidate["retrieval_stage_scores"][stage] = float(row.get("score") or 0.0)
            candidate["rrf_score"] += 1.0 / (rank_constant + rank)
    ordered = sorted(fused.values(), key=lambda row: (-row["rrf_score"],
                     str(row.get("document_id")), str(row.get("chunk_id"))))
    return ordered[:candidate_limit]


def rerank_candidates(query: str, candidates: Sequence[dict[str, Any]], reranker: Reranker,
                      *, top_k: int = 5) -> list[dict[str, Any]]:
    if top_k < 1:
        raise ValueError("reranker_top_k_must_be_positive")
    if not candidates:
        return []
    scores = reranker.score(query, [str(row.get("text") or "") for row in candidates])
    if len(scores) != len(candidates):
        raise ValueError("reranker_candidate_score_count_mismatch")
    ranked = []
    for row, raw_score in zip(candidates, scores):
        score = float(raw_score)
        stages = dict(row.get("retrieval_stage_scores") or {})
        stages["reranker"] = score
        ranked.append({**row, "reranker_score": score, "retrieval_stage_scores": stages})
    ranked.sort(key=lambda row: (-row["reranker_score"],
                  -float(row.get("rrf_score") or 0.0), str(row.get("chunk_id") or "")))
    return ranked[:top_k]


def hybrid_rank(query: str, dense_rows: Sequence[dict[str, Any]], bm25_rows: Sequence[dict[str, Any]],
                *, reranker: Reranker | None = None, candidate_k: int = 50,
                final_k: int = 5) -> list[dict[str, Any]]:
    """Combine dense/BM25 top-k lists using RRF, then optionally rerank the pool."""
    dense = list(dense_rows[:candidate_k])
    lexical = list(bm25_rows[:candidate_k])
    candidates = reciprocal_rank_fusion({"dense": dense, "bm25": lexical},
                                        rank_constant=60, candidate_limit=candidate_k)
    if reranker is not None:
        return rerank_candidates(query, candidates, reranker, top_k=final_k)
    for row in candidates:
        row["retrieval_stage_scores"]["rrf"] = float(row["rrf_score"])
    return candidates[:final_k]


__all__ = ["Reranker", "hybrid_rank", "reciprocal_rank_fusion", "rerank_candidates",
           "retrieval_config_id"]
