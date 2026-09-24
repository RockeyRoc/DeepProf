"""Build non-sensitive course coverage metadata from the local dev question bank."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "data" / "courses" / "data_structures_c"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    home = Path(os.environ.get("DEEPPROF_HOME", "~/.deepprof")).expanduser().resolve()
    bank_path = home / "course" / "question_bank.json"
    bank = read_json(bank_path)
    manifest = read_json(COURSE / "manifest.json")
    objectives = read_json(COURSE / "learning_objectives.json")["objectives"]
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in bank["questions"]:
        for concept_id in question.get("concept_ids", []):
            rows[str(concept_id)].append(question)

    coverage: list[dict[str, Any]] = []
    for concept in manifest["concepts"]:
        concept_id = str(concept["concept_id"])
        concept["learning_objective"] = objectives[concept_id]
        matched = rows.get(concept_id, [])
        review = Counter(str(row.get("source_review_status") or "unreviewed") for row in matched)
        deterministic = sum(
            row.get("source_review_status") == "verified"
            and row.get("grading_review_status") == "verified"
            and (row.get("grading") or {}).get("method") == "exact_normalized_match"
            for row in matched
        )
        coverage_status = "uncovered" if not matched else (
            "pending_reference" if review.get("pending_reference") else "covered"
        )
        concept["question_coverage"] = {
            "item_ids": sorted(str(row["item_id"]) for row in matched),
            "question_count": len(matched),
            "deterministic_count": deterministic,
            "status": coverage_status,
        }
        coverage.append({
            "concept_id": concept_id,
            "module": concept["module"],
            "mapping_status": concept.get("mapping_status", "unmapped"),
            "item_ids": sorted(str(row["item_id"]) for row in matched),
            "question_count": len(matched),
            "deterministic_count": deterministic,
            "review_counts": dict(sorted(review.items())),
            "coverage_status": coverage_status,
        })
    manifest["learning_objectives_version"] = "ds-learning-objectives-v1"
    manifest["question_bank_coverage_version"] = str(bank.get("version") or "")
    manifest["question_coverage_file"] = "question_coverage.json"
    (COURSE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output = {
        "course_id": manifest["course_id"],
        "bank_version": bank.get("version", ""),
        "question_count": len(bank["questions"]),
        "concept_count": len(coverage),
        "coverage": coverage,
        "uncovered_concepts": [row["concept_id"] for row in coverage if row["coverage_status"] == "uncovered"],
    }
    (COURSE / "question_coverage.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "question_count": output["question_count"],
        "concept_count": output["concept_count"],
        "uncovered_count": len(output["uncovered_concepts"]),
        "coverage_file": str(COURSE / "question_coverage.json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
