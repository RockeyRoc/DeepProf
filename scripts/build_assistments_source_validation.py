"""Create the non-identifying preparation record for the public BKT dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from evaluation.public_bkt import FOLDS, SEED, development_fold, load_assistments, split_outer


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "tmp" / "assistments-2009-2010-skill-builder-corrected.csv"
DEFAULT_OUTPUT = ROOT / "docs" / "experiments" / "public-bkt" / "assistments-source-validation.json"


def build_record(data_path: Path) -> dict[str, object]:
    dataset = load_assistments(data_path)
    development, final_test = split_outer(dataset.sequences)
    dev_learners = {row.learner_id for row in development}
    test_learners = {row.learner_id for row in final_test}
    test_membership = sorted(hashlib.sha256(learner.encode("utf-8")).hexdigest() for learner in test_learners)
    split_digest = hashlib.sha256((str(SEED) + "|" + "\n".join(test_membership)).encode("ascii")).hexdigest()
    fold_counts: dict[str, dict[str, int]] = {}
    for fold in range(FOLDS):
        learners = {row.learner_id for row in development if development_fold(row.learner_id) == fold}
        attempts = sum(len(row.outcomes) for row in development if row.learner_id in learners)
        fold_counts[str(fold + 1)] = {"learners": len(learners), "attempts": attempts}
    return {
        "schema_version": "deepprof-public-bkt-source-validation-v1",
        "prepared_on": date.today().isoformat(),
        "status": "prepared; no model performance claim; locked test outcomes not evaluated",
        "source": {
            "name": "ASSISTments 2009-2010 corrected Skill Builder data",
            "official_url": "https://sites.google.com/site/assistmentsdata/home/2009-2010-assistment-data/skill-builder-data-2009-2010",
            "corrected_file_note": "official page says corrected file has one row per student-problem and joins multiple skills with underscores",
            "usage_note": "official page requests citing the dataset URL and ASSISTments; it does not state a data license, so redistribution rights are not inferred",
            "source_file_name": data_path.name,
            "sha256": dataset.sha256,
            "encoding": dataset.encoding,
            "raw_data_in_repository": False,
        },
        "label_policy": {
            "label": "source correct field retained as provided",
            "official_help_semantics": "official page says using Hint or Break this Problem Into Steps marks the question incorrect",
            "product_attempt_difference": "public benchmark retains source-coded labels; DeepProf course BKT excludes hinted and unreliable-scored Attempts",
            "hint_label_audit": {
                "included_rows_with_positive_hint_count": dataset.rows_with_tutoring,
                "included_rows_correct_with_positive_hint_count": dataset.rows_correct_with_tutoring,
                "included_rows_missing_or_invalid_hint_count": dataset.rows_missing_hint_metadata,
                "discordant_rows_policy": f"retain the source correct label and disclose all {dataset.rows_correct_with_tutoring} included correct-plus-hint records; do not silently rewrite labels",
            },
            "multiple_skill_policy": "exclude records with zero or multiple skill identifiers; no synthetic attribution",
            "open_response_policy": "exclude answer_type=open_response",
            "deduplication_key": "user_id + order_id + unique skill identifier",
        },
        "filter_summary": {
            "rows_seen": dataset.rows_seen,
            "rows_used": dataset.rows_used,
            "rows_deduplicated": dataset.rows_deduplicated,
            "rows_dropped": dataset.rows_dropped,
            "unique_learners": len({row.learner_id for row in dataset.sequences}),
            "unique_single_skill_concepts": len({row.concept_id for row in dataset.sequences}),
        },
        "split": {
            "method": "fixed 80/20 learner-disjoint SHA256 ranking; development-only 5-fold learner grouping",
            "seed": SEED,
            "development_learners": len(dev_learners),
            "locked_test_learners": len(test_learners),
            "locked_test_membership_sha256": split_digest,
            "development_fold_counts": fold_counts,
            "test_scored_in_this_preparation_step": False,
        },
        "planned_acceptance": {
            "development_oof_log_loss_ratio_max": 0.95,
            "final_test_log_loss_improvement_min": 0.05,
            "final_test_auc_min": 0.60,
            "final_test_brier_no_worse_than_either_constant_baseline": True,
            "final_test_ece_10_bins_max": 0.05,
            "learner_paired_bootstrap_samples": 1000,
            "bootstrap_95_percent_ci_lower_bound_must_exceed_zero": True,
            "course_parameter_promotion_allowed_from_public_data": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    record = build_record(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_sha256": record["source"]["sha256"],
                      "rows_used": record["filter_summary"]["rows_used"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
