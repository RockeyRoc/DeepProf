"""Validate two independent human-review CSVs and compute agreement statistics."""
from __future__ import annotations
import argparse, csv, hashlib, json
from pathlib import Path
from typing import Any

CITATION_LABELS = {"完整支持", "部分支持", "无支持", "矛盾", "无法判定"}
RETRIEVAL_LABELS = {"0", "1", "2"}
ANSWERABILITY_LABELS = {"answerable", "insufficient_evidence", "无法判定"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def validate_ai_csv(path: Path, field: str, allowed: set[str]) -> dict[str, Any]:
    rows = read_csv(path)
    ids = [row.get("item_id", "") for row in rows]
    duplicate_count = len(ids) - len(set(ids))
    invalid, missing, source_errors = [], [], []
    missing_locators, invalid_joint = [], []
    for row in rows:
        key = row.get("item_id", "")
        value = row.get(field, "").strip()
        if value and value not in allowed:
            invalid.append(key)
        if not value or not row.get("rationale", row.get("individual_reason", "")).strip():
            missing.append(key)
        if row.get("annotation_source") != "ai" or not row.get("annotation_version"):
            source_errors.append(key)
        if field == 'individual_support_label':
            if row.get('joint_support_label') not in allowed or not row.get('joint_reason','').strip():
                invalid_joint.append(key)
            if row.get('record_type') == 'claim_citation_pair' and not row.get('evidence_locator','').strip():
                missing_locators.append(key)
        elif not row.get('evidence_locator','').strip():
            # An explicit empty evidence list is valid for unanswerable questions.
            missing_locators.append(key)
    return {"rows": len(rows), "duplicate_ids": duplicate_count, "empty_ids": ids.count(""),
            "invalid_labels": invalid, "missing_label_or_reason": missing,
            "provenance_errors": source_errors, "source_sha256": sha256(path),
            "missing_evidence_locator": missing_locators, "invalid_joint_support": invalid_joint,
            "agreement": None, "cohen_kappa": None, "annotation_source": "ai"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def nominal_kappa(left: list[str], right: list[str], labels: set[str]) -> float | None:
    n = len(left)
    if not n:
        return None
    po = sum(a == b for a, b in zip(left, right)) / n
    pa = {label: sum(a == label for a in left) / n for label in labels}
    pb = {label: sum(b == label for b in right) / n for label in labels}
    pe = sum(pa[label] * pb[label] for label in labels)
    return None if pe == 1 else (po - pe) / (1 - pe)


def quadratic_weighted_kappa(left: list[str], right: list[str]) -> float | None:
    n = len(left)
    if not n:
        return None
    labels = ("0", "1", "2")
    observed = sum(((int(a) - int(b)) / 2) ** 2 for a, b in zip(left, right)) / n
    pa = {label: sum(a == label for a in left) / n for label in labels}
    pb = {label: sum(b == label for b in right) / n for label in labels}
    expected = sum(pa[a] * pb[b] * ((int(a) - int(b)) / 2) ** 2 for a in labels for b in labels)
    return None if expected == 0 else 1 - observed / expected


def compare(package: Path, stem: str, field: str, allowed: set[str], *,
            eligible_ids: set[str] | None = None, weighted: bool = False) -> dict[str, Any]:
    paths = [package / f"{stem}-rater-{i}.csv" for i in (1, 2)]
    rows = [read_csv(path) for path in paths]
    maps: list[dict[str, dict[str, str]]] = []
    duplicates: list[list[str]] = []
    for rater_rows in rows:
        current: dict[str, dict[str, str]] = {}
        dup: list[str] = []
        for row in rater_rows:
            if row.get("annotation_source", "").casefold() == "ai":
                raise ValueError("AI_labels_cannot_enter_human_interrater_statistics")
            if row.get(field, "").strip() and (not row.get("reviewer", "").strip()
                    or row.get("reviewer", "").casefold().startswith(("ai", "gpt", "deepseek", "codex"))):
                raise ValueError("human_labels_require_identified_human_reviewer")
            key = row.get("item_id", "")
            if not key or key in current:
                dup.append(key or "<empty>")
            current[key] = row
        maps.append(current)
        duplicates.append(dup)
    ids1, ids2 = set(maps[0]), set(maps[1])
    eligible = (ids1 & ids2) if eligible_ids is None else (ids1 & ids2 & eligible_ids)
    missing1 = sorted(item for item in eligible if not maps[0][item].get(field, "").strip())
    missing2 = sorted(item for item in eligible if not maps[1][item].get(field, "").strip())
    invalid: dict[str, list[str]] = {"rater_1": [], "rater_2": []}
    valid: list[str] = []
    paired_left: list[str] = []
    paired_right: list[str] = []
    for item in sorted(eligible):
        values = [maps[i][item].get(field, "").strip() for i in (0, 1)]
        for i, value in enumerate(values):
            if value and value not in allowed:
                invalid[f"rater_{i + 1}"].append(f"{item}:{value}")
        if all(value in allowed for value in values):
            valid.append(item)
            paired_left.append(values[0])
            paired_right.append(values[1])
    agreement = (sum(a == b for a, b in zip(paired_left, paired_right)) / len(valid)) if valid else None
    kappa = (quadratic_weighted_kappa(paired_left, paired_right) if weighted else
             nominal_kappa(paired_left, paired_right, allowed))
    return {"file_1_sha256": sha256(paths[0]), "file_2_sha256": sha256(paths[1]),
            "items_rater_1": len(ids1), "items_rater_2": len(ids2),
            "items_only_rater_1": sorted(ids1 - ids2), "items_only_rater_2": sorted(ids2 - ids1),
            "duplicate_item_ids": {"rater_1": duplicates[0], "rater_2": duplicates[1]},
            "eligible_items": len(eligible), "complete_pairs": len(valid),
            "missing_rater_1": len(missing1), "missing_rater_2": len(missing2),
            "invalid_labels": invalid, "raw_agreement": agreement,
            "cohen_kappa": kappa, "kappa_type": "quadratic_weighted" if weighted else "nominal"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--source", choices=("human", "ai"), default="human")
    args = parser.parse_args()
    package = args.package_dir.resolve()
    if args.source == "ai":
        specs = {"citation": ("individual_support_label", CITATION_LABELS),
                 "retrieval": ("relevance_grade_0_1_2", RETRIEVAL_LABELS | {"无法判定"}),
                 "new-80": ("answerability", ANSWERABILITY_LABELS)}
        results = {stem: validate_ai_csv(package / f"{stem}-ai.csv", field, labels)
                   for stem, (field, labels) in specs.items()}
        failed = any(r["duplicate_ids"] or r["empty_ids"] or r["invalid_labels"]
                     or r["missing_label_or_reason"] or r["provenance_errors"] for r in results.values())
        payload = {"schema_version": "deepprof-ai-annotation-validation-v1",
                   "status": "incomplete" if failed else "complete_ai_annotation",
                   "human_calibration": False, "results": results}
        (package / "validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return int(failed)
    key_rows = read_csv(package / "citation-blinding-key.csv")
    citation_pairs = {row["item_id"] for row in key_rows if row.get("record_type") == "claim_citation_pair"}
    results = {
        "schema_version": "deepprof-m3-interrater-results-v1",
        "status": "computed_from_returned_rater_files; missing labels are not imputed",
        "citation_support": compare(package, "citation", "individual_support_label", CITATION_LABELS,
                                     eligible_ids=citation_pairs),
        "retrieval_relevance": compare(package, "retrieval", "relevance_grade_0_1_2", RETRIEVAL_LABELS, weighted=True),
        "new_question_answerability": compare(package, "new-80", "human_answerability", ANSWERABILITY_LABELS),
    }
    results["package_manifest_sha256"] = sha256(package / "package-manifest.json")
    output = package.parent / "inter-rater-results.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    hard_errors = []
    for name in ("citation_support", "retrieval_relevance", "new_question_answerability"):
        result = results[name]
        if any(result["duplicate_item_ids"].values()) or any(result["invalid_labels"].values()):
            hard_errors.append(name)
    return 1 if hard_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
