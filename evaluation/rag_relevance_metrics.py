"""Score a completed candidate-pool relevance worksheet without model calls."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


RANK_FIELDS = {"hash_dense": "hash_dense_rank", "bm25": "bm25_rank", "rrf": "rrf_rank"}


def _dcg(grades: list[int], cutoff: int) -> float:
    return sum((2 ** grade - 1) / __import__("math").log2(rank + 1)
               for rank, grade in enumerate(grades[:cutoff], 1))


def evaluate_worksheet(path: Path) -> dict[str, Any]:
    cases: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("relevance_status") != "reviewed" or row.get("relevance_grade") not in {"0", "1", "2"}:
                raise ValueError("all_candidate_rows_must_have_reviewed_relevance_grade_0_1_2")
            cases[row["case_id"]].append(row)
    if not cases:
        raise ValueError("empty_relevance_worksheet")

    results: dict[str, Any] = {}
    for method, rank_field in RANK_FIELDS.items():
        recalls: list[float] = []
        ndcgs: list[float] = []
        relevant_case_count = 0
        for rows in cases.values():
            grades_by_chunk = {row["chunk_id"]: int(row["relevance_grade"]) for row in rows}
            if len(grades_by_chunk) != len(rows):
                raise ValueError("duplicate_case_chunk_id_in_relevance_worksheet")
            relevant_total = sum(grade > 0 for grade in grades_by_chunk.values())
            if relevant_total:
                relevant_case_count += 1
                ranked = sorted(rows, key=lambda row: (
                    int(row[rank_field]) if row.get(rank_field, "").strip() else 10**9,
                    row["chunk_id"]))
                retrieved = {row["chunk_id"] for row in ranked
                             if row.get(rank_field, "").strip() and int(row[rank_field]) <= 20}
                recalls.append(sum(grades_by_chunk[key] > 0 for key in retrieved) / relevant_total)
                actual_dcg = sum((2 ** grades_by_chunk[row['chunk_id']] - 1) /
                    __import__('math').log2(int(row[rank_field]) + 1) for row in ranked
                    if row.get(rank_field, '').strip() and int(row[rank_field]) <= 5)
                ideal = sorted(grades_by_chunk.values(), reverse=True)
                ideal_dcg = _dcg(ideal, 5)
                ndcgs.append(actual_dcg / ideal_dcg if ideal_dcg else 0.0)
        results[method] = {
            "recall_at_20": sum(recalls) / len(recalls) if recalls else None,
            "ndcg_at_5": sum(ndcgs) / len(ndcgs) if ndcgs else None,
            "questions_with_relevant_candidate": relevant_case_count,
            "questions_scored": len(cases),
            "recall_denominator": "relevant passages within the judged union of each question's dense and BM25 top-k pools",
        }

    no_evidence_cases = [rows for rows in cases.values() if rows[0].get("expected_no_evidence") == "1"]
    false_accepts = sum(any(row.get("rrf_rank", "").strip() and int(row["rrf_rank"]) <= 5 and
                            int(row["relevance_grade"]) == 2 for row in rows)
                        for rows in no_evidence_cases)
    latencies = [float(rows[0]["total_retrieval_latency_ms"]) for rows in cases.values()
                 if rows[0].get("total_retrieval_latency_ms", "").strip()]
    return {
        "schema_version": "deepprof-rag-relevance-metrics-v1",
        "worksheet_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
        "questions": len(cases),
        "judgment_protocol": {"0": "irrelevant or merely topical", "1": "partial support", "2": "directly supports the question's key evidence needs"},
        "retrieval_metrics": results,
        "no_evidence_error_acceptance": {
            "false_accepts": false_accepts, "no_evidence_questions": len(no_evidence_cases),
            "rate": false_accepts / len(no_evidence_cases) if no_evidence_cases else None,
            "definition": "an expected-no-evidence question has a grade-2 passage among the RRF top five; retrieval-only proxy, not generated-answer acceptance",
        },
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else None,
            "mean": statistics.mean(latencies) if latencies else None,
            "n": len(latencies),
            "includes": "concept expansion, hashing query embedding, dense search, BM25 search, and RRF merge",
        },
        "scope": "exploratory; candidate-pool recall is conditional on the judged union and is not corpus-wide recall",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_worksheet(args.worksheet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
