"""Leakage-safe benchmark for public ASSISTments BKT calibration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from evaluation.bkt_fitting import BKTFit, fit_hierarchical, predict_sequence
from models.learner.bkt import BKTParameters, INITIAL_PARAMETERS

SOURCE_URL = "https://sites.google.com/site/assistmentsdata/home/2009-2010-assistment-data/skill-builder-data-2009-2010"
SEED = 20260927
FOLDS = 5
MIN_CONCEPT_ATTEMPTS = 40
EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class AttemptSequence:
    learner_id: str
    concept_id: str
    outcomes: tuple[bool, ...]
    order_keys: tuple[int, ...]
    problem_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Dataset:
    sequences: tuple[AttemptSequence, ...]
    sha256: str
    source_name: str
    encoding: str
    rows_seen: int
    rows_used: int
    rows_deduplicated: int
    rows_dropped: dict[str, int]
    rows_with_tutoring: int
    rows_correct_with_tutoring: int
    rows_missing_hint_metadata: int


def load_assistments(path: Path) -> Dataset:
    """Read the corrected one-row-per-student-problem CSV without retaining answer text."""
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    try:
        decoded = source.decode("utf-8-sig", errors="strict")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        decoded = source.decode("cp1252", errors="strict")
        encoding = "cp1252"
    grouped: dict[tuple[str, str], list[tuple[int, str, bool]]] = defaultdict(list)
    seen: set[tuple[str, int, str]] = set()
    dropped: dict[str, int] = defaultdict(int)
    duplicates = 0
    rows_seen = 0
    rows_with_tutoring = 0
    rows_correct_with_tutoring = 0
    rows_missing_hint_metadata = 0
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    required = {"user_id", "problem_id", "correct", "order_id"}
    has_skill_column = reader.fieldnames is not None and bool({"list_skill_ids", "skill_id"} & set(reader.fieldnames))
    if reader.fieldnames is None or not required.issubset(reader.fieldnames) or not has_skill_column:
        raise ValueError("assistments_csv_missing_required_columns")
    for row in reader:
        rows_seen += 1
        learner = str(row.get("user_id") or "").strip()
        item = str(row.get("problem_id") or row.get("assistment_id") or "").strip()
        label = str(row.get("correct") or "").strip()
        answer_type = str(row.get("answer_type") or "").strip().lower()
        if not learner or not item:
            dropped["missing_identity"] += 1
            continue
        if answer_type == "open_response":
            dropped["open_response"] += 1
            continue
        if label not in {"0", "1"}:
            dropped["missing_or_invalid_label"] += 1
            continue
        raw_hint_count = str(row.get("hint_count") or "").strip()
        try:
            if not raw_hint_count:
                raise ValueError("missing_hint_count")
            hint_count = float(raw_hint_count)
            if not math.isfinite(hint_count) or hint_count < 0:
                raise ValueError("invalid_hint_count")
            hinted = hint_count > 0
        except ValueError:
            hinted = False
            rows_missing_hint_metadata += 1
        # Corrected Skill Builder records encode all associated skills with `_`.
        raw_skill = str(row.get("list_skill_ids") or row.get("skill_id") or "").strip()
        raw_skill = raw_skill.replace(";", "_")
        skill_tokens = [part.strip() for part in raw_skill.split("_") if part.strip()]
        missing_skill_markers = {"na", "n/a", "nan", "none", "null", "unknown"}
        skills = [] if any(part.casefold() in missing_skill_markers for part in skill_tokens) else skill_tokens
        if len(skills) != 1:
            dropped["missing_or_ambiguous_skill"] += 1
            continue
        try:
            order = int(str(row.get("order_id") or ""))
        except ValueError:
            dropped["invalid_order"] += 1
            continue
        unique_key = (learner, order, skills[0])
        if unique_key in seen:
            duplicates += 1
            continue
        seen.add(unique_key)
        if hinted:
            rows_with_tutoring += 1
            rows_correct_with_tutoring += int(label == "1")
        # The official label is first-try success; requesting a hint is recorded as 0.
        grouped[(learner, skills[0])].append((order, item, label == "1"))
    sequences: list[AttemptSequence] = []
    for (learner, concept), observations in sorted(grouped.items()):
        observations.sort(key=lambda item: item[0])
        sequences.append(AttemptSequence(learner, concept,
            tuple(value for _, _, value in observations),
            tuple(order for order, _, _ in observations),
            tuple(item for _, item, _ in observations)))
    return Dataset(tuple(sequences), digest, path.name, encoding, rows_seen, sum(len(row.outcomes) for row in sequences),
                   duplicates, dict(sorted(dropped.items())), rows_with_tutoring,
                   rows_correct_with_tutoring, rows_missing_hint_metadata)


def split_outer(sequences: Iterable[AttemptSequence]) -> tuple[list[AttemptSequence], list[AttemptSequence]]:
    rows = list(sequences)
    learners = sorted({row.learner_id for row in rows},
                      key=lambda learner: (hashlib.sha256(
                          f"assistments-split-v1|{SEED}|{learner}".encode("utf-8")).digest(), learner))
    test_count = round(len(learners) * 0.2)
    test_learners = set(learners[:test_count])
    development: list[AttemptSequence] = []
    final_test: list[AttemptSequence] = []
    for row in rows:
        (final_test if row.learner_id in test_learners else development).append(row)
    return development, final_test


def development_fold(learner_id: str) -> int:
    token = f"assistments-fold-v1|{SEED}|{learner_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(token).digest()[:8], "big") % FOLDS


def _labels(rows: Iterable[AttemptSequence]) -> list[bool]:
    return [value for row in rows for value in row.outcomes]


def _metrics(observations: list[tuple[bool, float]]) -> dict[str, float | int | None]:
    if not observations:
        return {"n": 0, "auc": None, "brier": None, "log_loss": None, "ece_10_bins": None}
    labels = [label for label, _ in observations]
    scores = [min(1 - EPSILON, max(EPSILON, float(score))) for _, score in observations]
    positive = sum(labels)
    negative = len(labels) - positive
    loss = sum(-math.log(p if label else 1 - p) for label, p in zip(labels, scores)) / len(labels)
    brier = sum((p - int(label)) ** 2 for label, p in zip(labels, scores)) / len(labels)
    ece = 0.0
    for bucket in range(10):
        selected = [(label, score) for label, score in zip(labels, scores)
                    if bucket / 10 <= score < (bucket + 1) / 10 or (bucket == 9 and score == 1)]
        if selected:
            ece += len(selected) / len(labels) * abs(
                sum(score for _, score in selected) / len(selected) -
                sum(label for label, _ in selected) / len(selected))
    positives = [score for label, score in zip(labels, scores) if label]
    negatives = [score for label, score in zip(labels, scores) if not label]
    if positive and negative:
        ordered = sorted(zip(scores, labels))
        rank_sum = 0.0
        index = 0
        while index < len(ordered):
            end = index + 1
            while end < len(ordered) and ordered[end][0] == ordered[index][0]:
                end += 1
            average_rank = ((index + 1) + end) / 2
            rank_sum += average_rank * sum(label for _, label in ordered[index:end])
            index = end
        auc = (rank_sum - positive * (positive + 1) / 2) / (positive * negative)
    else:
        auc = None
    return {"n": len(labels), "positive": positive, "negative": negative,
            "auc": auc, "brier": brier, "log_loss": loss, "ece_10_bins": ece}


def _constant_probabilities(train: list[AttemptSequence]) -> tuple[float, dict[str, float]]:
    labels = _labels(train)
    global_probability = (sum(labels) + 1) / (len(labels) + 2)
    by_concept: dict[str, list[bool]] = defaultdict(list)
    for row in train:
        by_concept[row.concept_id].extend(row.outcomes)
    concept_probabilities = {concept: (sum(values) + 1) / (len(values) + 2)
                             for concept, values in by_concept.items()
                             if len(values) >= MIN_CONCEPT_ATTEMPTS}
    return global_probability, concept_probabilities


def _score_fold(train: list[AttemptSequence], validation: list[AttemptSequence],
                fitted: BKTFit | None = None) -> dict[str, object]:
    fit: BKTFit = fitted or fit_hierarchical(train, min_concept_attempts=MIN_CONCEPT_ATTEMPTS)
    global_constant, concept_constants = _constant_probabilities(train)
    method_rows: dict[str, list[tuple[bool, float]]] = {
        "global_em": [], "concept_em": [], "hierarchical_shrinkage": [],
    }
    global_rows: list[tuple[bool, float]] = []
    concept_rows: list[tuple[bool, float]] = []
    original_rows: list[tuple[bool, float]] = []
    by_learner: dict[str, tuple[list[float], list[float], list[float]]] = {}
    for row in validation:
        global_predictions = predict_sequence(row.outcomes, fit.global_parameters)
        concept_predictions = predict_sequence(row.outcomes,
            fit.concept_parameters.get(row.concept_id, fit.global_parameters))
        hierarchical_predictions = predict_sequence(row.outcomes,
            fit.by_concept.get(row.concept_id, fit.global_parameters))
        original = predict_sequence(row.outcomes, INITIAL_PARAMETERS)
        method_rows["global_em"].extend(zip(row.outcomes, global_predictions))
        method_rows["concept_em"].extend(zip(row.outcomes, concept_predictions))
        method_rows["hierarchical_shrinkage"].extend(zip(row.outcomes, hierarchical_predictions))
        original_rows.extend(zip(row.outcomes, original))
        concept_probability = concept_constants.get(row.concept_id, global_constant)
        global_rows.extend((outcome, global_constant) for outcome in row.outcomes)
        concept_rows.extend((outcome, concept_probability) for outcome in row.outcomes)
        losses = by_learner.setdefault(row.learner_id, ([], [], []))
        for outcome, probability in zip(row.outcomes, hierarchical_predictions):
            losses[0].append(-math.log(max(EPSILON, probability if outcome else 1 - probability)))
            losses[1].append(-math.log(max(EPSILON, global_constant if outcome else 1 - global_constant)))
            losses[2].append(-math.log(max(EPSILON, concept_probability if outcome else 1 - concept_probability)))
    metric_sets = {"fit_methods": {name: _metrics(values) for name, values in method_rows.items()},
                   "original": _metrics(original_rows),
                   "global_constant": _metrics(global_rows), "concept_constant": _metrics(concept_rows)}
    train_concepts = {row.concept_id for row in train}
    metric_sets["concept_constant_fallback_concepts"] = len(train_concepts - concept_constants.keys())
    metric_sets["concept_constant_fitted_concepts"] = len(concept_constants)
    metric_sets["bkt"] = metric_sets["fit_methods"]["hierarchical_shrinkage"]
    constant_loss = min(metric_sets["global_constant"]["log_loss"], metric_sets["concept_constant"]["log_loss"])
    metric_sets["best_constant"] = metric_sets["global_constant" if
        metric_sets["global_constant"]["log_loss"] <= metric_sets["concept_constant"]["log_loss"] else "concept_constant"]
    metric_sets["log_loss_ratio"] = metric_sets["bkt"]["log_loss"] / constant_loss
    metric_sets["parameter_summary"] = fit.summary()
    metric_sets["_learner_losses"] = by_learner
    metric_sets["_oof_rows"] = {**method_rows, "original": original_rows,
                                 "global_constant": global_rows, "concept_constant": concept_rows}
    return metric_sets


def _paired_bootstrap(rows_by_learner: dict[str, tuple[list[float], list[float], list[float]]], *, seed: int,
                      samples: int = 1000) -> dict[str, float]:
    learners = sorted(rows_by_learner)
    if len(learners) < 2:
        return {"lower_95": 0.0, "median": 0.0, "upper_95": 0.0}
    randomizer = random.Random(seed)
    totals = {key: (sum(rows_by_learner[key][0]), sum(rows_by_learner[key][1]),
                    sum(rows_by_learner[key][2]), len(rows_by_learner[key][0]))
              for key in learners}
    global_loss = sum(totals[key][1] for key in learners)
    concept_loss = sum(totals[key][2] for key in learners)
    baseline_index = 1 if global_loss <= concept_loss else 2
    differences: list[float] = []
    for _ in range(samples):
        chosen = [learners[randomizer.randrange(len(learners))] for _ in learners]
        model_loss = sum(totals[key][0] for key in chosen)
        baseline_loss = sum(totals[key][baseline_index] for key in chosen)
        model_n = baseline_n = sum(totals[key][3] for key in chosen)
        if model_n and baseline_n:
            differences.append(baseline_loss / baseline_n - model_loss / model_n)
    differences.sort()
    return {"lower_95": differences[int(0.025 * (len(differences) - 1))],
            "median": differences[len(differences) // 2],
            "upper_95": differences[int(0.975 * (len(differences) - 1))]}


def evaluate(path: Path, *, mode: str = "tune") -> dict[str, object]:
    if mode not in {"tune", "final"}:
        raise ValueError("evaluation_mode_must_be_tune_or_final")
    dataset = load_assistments(path)
    development, final_test = split_outer(dataset.sequences)
    if not development or (mode == "final" and not final_test):
        raise ValueError("assistments_fixed_learner_split_is_empty")
    test_learners = {row.learner_id for row in final_test}
    split_membership = sorted(hashlib.sha256(learner.encode("utf-8")).hexdigest()
                              for learner in test_learners)
    split_digest = hashlib.sha256((str(SEED) + "|" + "\n".join(split_membership)).encode("ascii")).hexdigest()
    fold_results: list[dict[str, object]] = []
    heldout_by_learner: dict[str, tuple[list[float], list[float], list[float]]] = {}
    oof_rows: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for fold in range(FOLDS):
        train = [row for row in development if development_fold(row.learner_id) != fold]
        validation = [row for row in development if development_fold(row.learner_id) == fold]
        if not train or not validation:
            raise ValueError("assistments_development_fold_is_empty")
        result = _score_fold(train, validation)
        for learner_id, values in result.pop("_learner_losses").items():
            heldout_by_learner[learner_id] = values
        for name, observations in result.pop("_oof_rows").items():
            oof_rows[name].extend(observations)
        fold_results.append({"fold": fold + 1, "train_learners": len({r.learner_id for r in train}),
                             "validation_learners": len({r.learner_id for r in validation}), **result})
    fold_ratios = [float(row["log_loss_ratio"]) for row in fold_results]
    development_metrics = {name: _metrics(rows) for name, rows in oof_rows.items()}
    best_constant_name = min(("global_constant", "concept_constant"),
                             key=lambda name: float(development_metrics[name]["log_loss"]))
    best_constant_loss = float(development_metrics[best_constant_name]["log_loss"])
    method_ratios = {name: float(development_metrics[name]["log_loss"]) / best_constant_loss
                     for name in ("global_em", "concept_em", "hierarchical_shrinkage")}
    development_ratio = method_ratios["hierarchical_shrinkage"]
    bootstrap = _paired_bootstrap(heldout_by_learner, seed=SEED)
    ratio = development_ratio
    fitted_all: BKTFit | None = None
    report = {
        "schema_version": "deepprof-public-bkt-benchmark-v1", "source_url": SOURCE_URL,
        "data_domain": "public_assistments_2009_2010_math_skill_builder; research only",
        "fit_method": "four_start_global_em; local_concept_em_warm_started_from_global; count_based_shrinkage",
        "parameter_version": "bkt-four-parameter-assistments-research-v1",
        "source_file": dataset.source_name, "source_sha256": dataset.sha256, "source_encoding": dataset.encoding,
        "split_sha256": split_digest,
        "split_version": "learner-ranked-sha256-v1",
        "seed": SEED, "folds": FOLDS,
        "rows_seen": dataset.rows_seen, "attempts_used": dataset.rows_used,
        "unique_learners": len({row.learner_id for row in dataset.sequences}),
        "unique_concepts": len({row.concept_id for row in dataset.sequences}),
        "deduplicated_rows": dataset.rows_deduplicated, "dropped_rows": dataset.rows_dropped,
        "label_semantics": "ASSISTments correct label; official page says tutoring use marks the question incorrect; open_response excluded",
        "help_label_audit": {"included_rows_with_hint_count_positive": dataset.rows_with_tutoring,
            "included_rows_correct_and_hint_count_positive": dataset.rows_correct_with_tutoring,
            "rows_missing_hint_metadata": dataset.rows_missing_hint_metadata,
            "product_attempt_admission_difference": "public labels retain source-coded help outcomes; DeepProf product Attempts exclude hinted or unreliable-scored answers"},
        "development": {"learners": len({r.learner_id for r in development}), "folds": fold_results,
                        "mean_fold_log_loss_ratio": sum(fold_ratios) / len(fold_ratios),
                        "out_of_fold": development_metrics,
                        "fit_method_log_loss_ratios_vs_best_constant": method_ratios,
                        "best_constant": best_constant_name,
                        "mean_fold_ratio_for_hierarchical": sum(fold_ratios) / len(fold_ratios),
                        "log_loss_ratio": development_ratio,
                        "metric": "log_loss_ratio", "lower_is_better": True},
        "bootstrap_learner_paired_loss_improvement": {"samples": 1000, **bootstrap},
        "evaluation_mode": mode,
        "curriculum_promotion_eligible": False,
        "promotion_reason": "public math data does not calibrate the C-language curriculum; teacher review pending",
    }
    gates = {"development_log_loss_ratio_le_0_95": development_ratio <= 0.95,
             "development_auc_ge_0_60": development_metrics["hierarchical_shrinkage"]["auc"] is not None and
                                         float(development_metrics["hierarchical_shrinkage"]["auc"]) >= 0.60,
             "paired_bootstrap_lower_ci_positive": bootstrap["lower_95"] > 0}
    if mode == "final":
        fitted_all = fit_hierarchical(development, min_concept_attempts=MIN_CONCEPT_ATTEMPTS)
        test_result = _score_fold(development, final_test, fitted=fitted_all)
        test_result.pop("_learner_losses")
        test_result.pop("_oof_rows")
        gates.update({
            "final_test_log_loss_ratio_le_0_95": float(test_result["log_loss_ratio"]) <= 0.95,
            "final_test_beats_original_bkt": float(test_result["bkt"]["log_loss"]) < float(test_result["original"]["log_loss"]),
            "final_test_auc_ge_0_60": test_result["bkt"]["auc"] is not None and float(test_result["bkt"]["auc"]) >= 0.60,
            "final_test_brier_not_worse_than_constants": float(test_result["bkt"]["brier"]) <= min(
                float(test_result["global_constant"]["brier"]), float(test_result["concept_constant"]["brier"])),
        "final_test_ece_le_0_05": float(test_result["bkt"]["ece_10_bins"]) <= 0.05,
        })
        report["final_test"] = {"learners": len({r.learner_id for r in final_test}), **test_result}
        report["candidate_global_parameters"] = fitted_all.global_parameters.to_dict()
        report["candidate_concepts_fitted"] = len(fitted_all.by_concept)
        report["candidate_concept_parameters"] = {concept: {
            "parameters": parameters.to_dict(), "training_attempts": fitted_all.concept_counts[concept]}
            for concept, parameters in sorted(fitted_all.by_concept.items())}
        report["candidate_unshrunk_concept_parameters"] = {concept: {
            "parameters": parameters.to_dict(), "training_attempts": fitted_all.concept_counts[concept]}
            for concept, parameters in sorted(fitted_all.concept_parameters.items())}
        report["parameter_artifact_metadata"] = {
            "data_domain": report["data_domain"], "data_sha256": dataset.sha256,
            "split_sha256": split_digest, "fit_method": report["fit_method"],
            "parameter_version": report["parameter_version"],
            "research_validation_status": "passed" if all(gates.values()) else "not_passed",
            "curriculum_promotion_status": "not_eligible_teacher_review_and_course_data_required",
        }
    report["gates"] = gates
    report["research_eligible"] = mode == "final" and all(gates.values())
    # The metric reported to autoresearch is development-only; locked test data never drives iteration.
    report["log_loss_ratio"] = ratio
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("DEEPPROF_ASSISTMENTS_CSV", ""))
                        if os.environ.get("DEEPPROF_ASSISTMENTS_CSV") else None)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--mode", choices=("tune", "final"), default="tune",
                        help="tune evaluates development folds only; final reads the locked test split once")
    args = parser.parse_args()
    if args.data is None:
        raise SystemExit("Pass --data or set DEEPPROF_ASSISTMENTS_CSV to the corrected Skill Builder CSV.")
    result = evaluate(args.data, mode=args.mode)
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"log_loss_ratio": result["log_loss_ratio"],
                      "development_log_loss_ratio": result["development"]["log_loss_ratio"],
                      "research_eligible": result["research_eligible"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
