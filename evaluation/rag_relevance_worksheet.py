"""Build a local-only candidate passage worksheet for the fixed 40 M3 questions."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import time
from pathlib import Path
from typing import Any

from evaluation.dev_cases import build_cases
from library.concept_queries import expand_concept_query
from library.embeddings import HashingEmbedder, retrieval_index_profile
from library.hybrid_retrieval import reciprocal_rank_fusion
from library.chunking import chunking_version
from runtime.storage.resource_store import SqliteResourceStore


def build_worksheet(database: Path, output: Path, *, per_stage_k: int = 50) -> dict[str, Any]:
    if not 1 <= per_stage_k <= 50:
        raise ValueError("per_stage_k_must_be_between_1_and_50")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    store = SqliteResourceStore(connection)
    embedder = HashingEmbedder()
    profile = retrieval_index_profile(embedder, chunking_version=chunking_version(800, 120))
    columns = ["case_id", "category", "expected_no_evidence", "concept_id", "query", "rrf_rank", "document_id",
               "chunk_id", "page", "printed_page", "chapter", "section", "source",
               "hash_dense_rank", "bm25_rank", "dense_score", "bm25_score", "rrf_score",
               "hash_dense_latency_ms", "bm25_latency_ms", "rrf_merge_latency_ms", "total_retrieval_latency_ms",
               "source_excerpt", "relevance_grade", "relevance_status", "rationale", "reviewer",
               "adjudication_status"]
    cases = build_cases()["cases"]
    candidate_count = 0
    no_candidate_cases: list[str] = []
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for case in cases:
                total_started = time.perf_counter()
                query = str((case.get("user_turns") or [""])[0]).strip()
                expanded, _ = expand_concept_query(query, "ds.c_language.v1", [case["concept_id"]])
                vector = embedder.embed([expanded])[0]
                filters = {"course_id": "ds.c_language.v1", "owner_id": "local"}
                started = time.perf_counter()
                dense = store.search(vector, score=HashingEmbedder.similarity, top_k=per_stage_k,
                    min_score=-1.0, retrieval_profile=profile, **filters)
                dense_latency = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                lexical = store.search_bm25(expanded, top_k=per_stage_k, **filters)
                lexical_latency = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                fused = reciprocal_rank_fusion({"dense": dense, "bm25": lexical},
                                               rank_constant=60, candidate_limit=per_stage_k * 2)
                merge_latency = (time.perf_counter() - started) * 1000
                total_latency = (time.perf_counter() - total_started) * 1000
                dense_rank = {row["chunk_id"]: index for index, row in enumerate(dense, 1)}
                lexical_rank = {row["chunk_id"]: index for index, row in enumerate(lexical, 1)}
                if not fused:
                    no_candidate_cases.append(str(case["case_id"]))
                for rank, hit in enumerate(fused, 1):
                    stages = hit.get("retrieval_stage_scores") or {}
                    writer.writerow({
                        "case_id": case["case_id"], "category": case["category"],
                        "expected_no_evidence": int(case["category"] == "insufficient_evidence"),
                        "concept_id": case["concept_id"], "query": query,
                        "rrf_rank": rank, "document_id": hit["document_id"],
                        "chunk_id": hit["chunk_id"], "page": hit["page"],
                        "printed_page": hit.get("printed_page"), "chapter": hit.get("chapter") or "",
                        "section": hit.get("section") or "", "source": hit.get("source") or "",
                        "hash_dense_rank": dense_rank.get(hit["chunk_id"], ""),
                        "bm25_rank": lexical_rank.get(hit["chunk_id"], ""),
                        "dense_score": stages.get("dense", ""),
                        "bm25_score": stages.get("bm25", ""),
                        "rrf_score": hit.get("rrf_score", ""),
                        "hash_dense_latency_ms": round(dense_latency, 3),
                        "bm25_latency_ms": round(lexical_latency, 3),
                        "rrf_merge_latency_ms": round(merge_latency, 3),
                        "total_retrieval_latency_ms": round(total_latency, 3),
                        "source_excerpt": str(hit.get("text") or "")[:280],
                        "relevance_grade": "", "relevance_status": "pending",
                        "rationale": "", "reviewer": "", "adjudication_status": "pending",
                    })
                    candidate_count += 1
    finally:
        store.close()
    return {"questions": len(cases), "candidate_rows": candidate_count,
            "dense_and_bm25_pool_k": per_stage_k, "max_union_candidates_per_question": per_stage_k * 2,
            "no_candidate_cases": no_candidate_cases,
            "retrieval_profile_id": profile["config_id"],
            "worksheet": str(output.resolve()),
            "status": "candidate set prepared; relevance labels remain pending human review"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Frozen library SQLite database")
    parser.add_argument("--output", type=Path, required=True, help="Local worksheet CSV with review columns")
    parser.add_argument("--per-stage-k", type=int, default=50)
    args = parser.parse_args()
    import json
    print(json.dumps(build_worksheet(args.database, args.output, per_stage_k=args.per_stage_k),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
