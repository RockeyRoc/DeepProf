"""Deterministic concept-held-out development calibration for the four-parameter BKT."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from evaluation.dev_cases import derive_m3_cases
from models.learner.bkt import BKTParameters, INITIAL_PARAMETERS, update

CASE_SOURCE = Path(__file__).resolve().with_name("dev_cases.json")
PARAMETER_VERSION = "bkt-four-parameter-dev-fit-v2"
FOLD_COUNT = 5
EPSILON = 1e-12


def _curriculum_promotion_eligible(source_type: str, teacher_review: str) -> bool:
    """Only reviewed course attempt data can authorize a curriculum default change."""
    return source_type == "approved_course_attempts" and teacher_review == "approved"


@dataclass(frozen=True, slots=True)
class Sequence:
    case_id: str
    concept_id: str
    outcomes: tuple[bool, ...]


def load_sequences(path: Path = CASE_SOURCE) -> tuple[list[Sequence], str]:
    source_bytes = path.read_bytes()
    source = json.loads(source_bytes)
    cases = derive_m3_cases(source)
    sequences = [Sequence(str(case["case_id"]), str(case["concept_id"]),
                          tuple(bool(row["correct"]) for row in case["attempt_history"]))
                 for case in cases]
    if len(sequences) != 40 or sum(len(row.outcomes) for row in sequences) != 120:
        raise ValueError("calibration_requires_40_complete_cases_and_120_constructed_attempts")
    if any(len(row.outcomes) != 3 for row in sequences):
        raise ValueError("calibration_requires_three_attempts_per_case")
    return sequences, hashlib.sha256(source_bytes).hexdigest()


def concept_folds(sequences: list[Sequence], fold_count: int = FOLD_COUNT) -> dict[str, int]:
    if fold_count < 2:
        raise ValueError("fold_count_must_be_at_least_two")
    counts: dict[str, int] = {}
    for row in sequences:
        counts[row.concept_id] = counts.get(row.concept_id, 0) + 1
    if len(counts) < fold_count:
        raise ValueError("not_enough_concepts_for_requested_folds")
    assigned: dict[str, int] = {}
    fold_sizes = [0] * fold_count
    for concept, size in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        fold = min(range(fold_count), key=lambda index: (fold_sizes[index], index))
        assigned[concept] = fold
        fold_sizes[fold] += size
    return assigned


def _sequence_predictions(row: Sequence, parameters: BKTParameters) -> list[tuple[bool, float]]:
    mastery = parameters.p_l0
    output = []
    for outcome in row.outcomes:
        prediction, mastery = update(mastery, outcome, parameters)
        output.append((outcome, prediction))
    return output


def _loss(rows: Iterable[Sequence], parameters: BKTParameters) -> float:
    labels_and_scores = [item for row in rows for item in _sequence_predictions(row, parameters)]
    if not labels_and_scores:
        raise ValueError("cannot_fit_empty_sequences")
    return sum(-math.log(max(EPSILON, score)) if label else
               -math.log(max(EPSILON, 1.0 - score)) for label, score in labels_and_scores) / len(labels_and_scores)


def _parameter(values: tuple[float, float, float, float]) -> BKTParameters:
    return BKTParameters(p_l0=values[0], p_t=values[1], p_g=values[2], p_s=values[3],
                         source="constructed_m3_development_fit; teacher review pending",
                         model_version=PARAMETER_VERSION, fitted=True, teacher_review="pending")


def _grid(start: float, stop: float, step: float) -> list[float]:
    count = round((stop - start) / step)
    return [round(start + index * step, 10) for index in range(count + 1)]


def fit_parameters(rows: list[Sequence]) -> tuple[BKTParameters, float, dict[str, Any]]:
    """Optimize training sequences only, then deterministically refine around the coarse optimum."""
    if not rows:
        raise ValueError("cannot_fit_empty_sequences")

    def evaluate(values: tuple[float, float, float, float]) -> float:
        return _loss(rows, _parameter(values))

    coarse_space = itertools.product(
        _grid(0.05, 0.95, 0.05), _grid(0.0, 0.30, 0.05),
        _grid(0.05, 0.45, 0.05), _grid(0.05, 0.45, 0.05),
    )
    best_values: tuple[float, float, float, float] | None = None
    best_loss = math.inf
    for raw in coarse_space:
        values = tuple(raw)
        candidate_loss = evaluate(values)
        if candidate_loss < best_loss - 1e-14 or (abs(candidate_loss - best_loss) <= 1e-14 and
                                                   (best_values is None or values < best_values)):
            best_values, best_loss = values, candidate_loss
    assert best_values is not None

    def local_values(value: float, low: float, high: float) -> list[float]:
        return sorted({round(max(low, min(high, value + offset)), 2)
                       for offset in _grid(-0.05, 0.05, 0.01)})

    local_space = itertools.product(
        local_values(best_values[0], 0.05, 0.95), local_values(best_values[1], 0.0, 0.30),
        local_values(best_values[2], 0.05, 0.45), local_values(best_values[3], 0.05, 0.45),
    )
    for raw in local_space:
        values = tuple(raw)
        candidate_loss = evaluate(values)
        if candidate_loss < best_loss - 1e-14 or (abs(candidate_loss - best_loss) <= 1e-14 and values < best_values):
            best_values, best_loss = values, candidate_loss
    parameters = _parameter(best_values)
    boundary = {"p_l0": best_values[0] in {0.05, 0.95},
                "p_t": best_values[1] in {0.0, 0.30},
                "p_g": best_values[2] in {0.05, 0.45},
                "p_s": best_values[3] in {0.05, 0.45}}
    return parameters, best_loss, {"training_log_loss": best_loss, "boundary_solution": boundary}


def _constant_predictions(rows: list[Sequence], probability: float) -> list[tuple[bool, float]]:
    return [(outcome, probability) for row in rows for outcome in row.outcomes]


def _metrics(observations: list[tuple[bool, float]]) -> dict[str, Any]:
    labels = [label for label, _ in observations]
    scores = [min(1 - EPSILON, max(EPSILON, score)) for _, score in observations]
    positives = sum(labels)
    negatives = len(labels) - positives
    brier = sum((score - int(label)) ** 2 for label, score in zip(labels, scores)) / len(labels) if labels else None
    log_loss = sum(-math.log(score) if label else -math.log(1 - score)
                   for label, score in zip(labels, scores)) / len(labels) if labels else None
    ece = 0.0
    for index in range(10):
        bucket = [(label, score) for label, score in zip(labels, scores)
                  if index / 10 <= score < (index + 1) / 10 or (index == 9 and score == 1)]
        if bucket:
            ece += len(bucket) / len(labels) * abs(
                sum(score for _, score in bucket) / len(bucket) -
                sum(label for label, _ in bucket) / len(bucket))
    positives_scores = [score for label, score in zip(labels, scores) if label]
    negatives_scores = [score for label, score in zip(labels, scores) if not label]
    auc = (sum(1 if positive > negative else 0.5 if positive == negative else 0
               for positive in positives_scores for negative in negatives_scores) /
           (positives * negatives)) if positives and negatives else None
    return {"n": len(labels), "positive": positives, "negative": negatives,
            "auc": auc, "brier": brier, "log_loss": log_loss, "ece_10_bins": ece if labels else None}


def cross_validate(sequences: list[Sequence], *, compatible_tests_passed: bool = False) -> dict[str, Any]:
    folds = concept_folds(sequences)
    old_predictions: list[tuple[bool, float]] = []
    fitted_predictions: list[tuple[bool, float]] = []
    baseline_predictions: list[tuple[bool, float]] = []
    fold_rows: list[dict[str, Any]] = []
    heldout_case_counts: list[int] = []
    parameters_by_fold: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    for fold in range(FOLD_COUNT):
        train = [row for row in sequences if folds[row.concept_id] != fold]
        validation = [row for row in sequences if folds[row.concept_id] == fold]
        if not train or not validation:
            raise ValueError("empty_training_or_validation_fold")
        fitted, training_loss, fit_detail = fit_parameters(train)
        labels = [outcome for row in train for outcome in row.outcomes]
        baseline_probability = (sum(labels) + 1) / (len(labels) + 2)
        old_fold = [item for row in validation for item in _sequence_predictions(row, INITIAL_PARAMETERS)]
        fitted_fold = [item for row in validation for item in _sequence_predictions(row, fitted)]
        baseline_fold = _constant_predictions(validation, baseline_probability)
        old_predictions.extend(old_fold)
        fitted_predictions.extend(fitted_fold)
        baseline_predictions.extend(baseline_fold)
        heldout_case_counts.append(len(validation))
        parameters_by_fold.append({key: getattr(fitted, key) for key in ("p_l0", "p_t", "p_g", "p_s")})
        for row in validation:
            old_sequence = _sequence_predictions(row, INITIAL_PARAMETERS)
            fitted_sequence = _sequence_predictions(row, fitted)
            for index, (label, old_prediction) in enumerate(old_sequence):
                prediction_rows.append({"case_id": row.case_id, "concept_id": row.concept_id,
                    "fold": fold + 1, "step": index + 1, "correct": int(label),
                    "old_probability": old_prediction,
                    "fitted_probability": fitted_sequence[index][1],
                    "baseline_probability": baseline_probability})
        fold_rows.append({"fold": fold + 1, "heldout_concepts": sorted(concept for concept, index in folds.items() if index == fold),
            "train_cases": len(train), "validation_cases": len(validation), "training_loss": training_loss,
            "old_parameters": _metrics(old_fold), "fitted_parameters": _metrics(fitted_fold),
            "constant_baseline": _metrics(baseline_fold), "parameters": parameters_by_fold[-1],
            **fit_detail})
    old_metrics = _metrics(old_predictions)
    fitted_metrics = _metrics(fitted_predictions)
    baseline_metrics = _metrics(baseline_predictions)
    log_loss_gates = {
        "beats_old_by_1e-6": fitted_metrics["log_loss"] <= old_metrics["log_loss"] - 1e-6,
        "beats_constant_baseline_by_1e-6": fitted_metrics["log_loss"] <= baseline_metrics["log_loss"] - 1e-6,
    }
    brier_gates = {
        "not_worse_than_old": fitted_metrics["brier"] <= old_metrics["brier"] + 1e-12,
        "not_worse_than_constant_baseline": fitted_metrics["brier"] <= baseline_metrics["brier"] + 1e-12,
    }
    all_parameters = list(parameters_by_fold)
    stability = {name: {"min": min(row[name] for row in all_parameters),
                        "max": max(row[name] for row in all_parameters),
                        "range": max(row[name] for row in all_parameters) - min(row[name] for row in all_parameters)}
                 for name in ("p_l0", "p_t", "p_g", "p_s")}
    research_metrics_eligible = (all(log_loss_gates.values()) and all(brier_gates.values()) and compatible_tests_passed)
    return {"schema_version": "deepprof-bkt-calibration-v1", "fold_count": FOLD_COUNT,
            "case_count": len(sequences), "attempt_count": sum(len(row.outcomes) for row in sequences),
            "grouping": "concept-balanced descending-size; concept name breaks ties; greedy smallest-fold assignment",
            "concept_to_fold": folds, "folds": fold_rows, "out_of_fold": {
                "old_parameters": old_metrics, "fitted_parameters": fitted_metrics,
                "constant_baseline": baseline_metrics,
                "log_loss_improvement_vs_old": old_metrics["log_loss"] - fitted_metrics["log_loss"],
                "log_loss_improvement_vs_baseline": baseline_metrics["log_loss"] - fitted_metrics["log_loss"],
                "brier_change_vs_old": fitted_metrics["brier"] - old_metrics["brier"],
                "brier_change_vs_baseline": fitted_metrics["brier"] - baseline_metrics["brier"]},
            "parameter_stability": stability,
            "gates": {"log_loss": log_loss_gates, "brier": brier_gates,
                      "compatible_tests_passed": compatible_tests_passed,
                      "research_metrics_eligible": research_metrics_eligible,
                      # This evaluator consumes only constructed developer fixtures. It can never
                      # authorize changing the course's default parameters.
                      "curriculum_promotion_eligible": _curriculum_promotion_eligible(
                          "constructed_developer_fixture", "pending"),
                      "promotion_eligible": False},
            "fold_assignment": {row.case_id: folds[row.concept_id] + 1 for row in sequences},
            "heldout_case_counts": heldout_case_counts,
            "fitted_parameters_by_fold": parameters_by_fold,
            "out_of_fold_predictions": prediction_rows}


def run_calibration(output_dir: Path, *, compatible_tests_passed: bool = False,
                    promote: bool = False) -> dict[str, Any]:
    sequences, source_hash = load_sequences()
    report = cross_validate(sequences, compatible_tests_passed=compatible_tests_passed)
    raw_candidate, full_loss, fit_detail = fit_parameters(sequences)
    candidate = BKTParameters(p_l0=raw_candidate.p_l0, p_t=raw_candidate.p_t,
        p_g=raw_candidate.p_g, p_s=raw_candidate.p_s,
        source=f"constructed_m3_dev_sha256={source_hash}; teacher review pending",
        model_version=PARAMETER_VERSION, fitted=True, teacher_review="pending")
    report["source_data"] = {"path": "evaluation/dev_cases.json", "sha256": source_hash,
                             "type": "constructed_developer_fixture; three sequence templates"}
    report["full_data_fit"] = {"parameters": candidate.to_dict(), "training_log_loss": full_loss,
                               **fit_detail, "is_held_out": False}
    report["gates"]["promotion_requested"] = promote
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "bkt-calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "bkt-candidate-parameters.json").write_text(json.dumps({
        **candidate.to_dict(), "calibration": report["source_data"],
        "research_metrics_eligible": report["gates"]["research_metrics_eligible"],
        "curriculum_promotion_eligible": report["gates"]["curriculum_promotion_eligible"],
        "promotion_eligible": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "bkt-predictions.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", "concept_id", "fold", "step", "correct",
                                                    "old_probability", "fitted_probability", "baseline_probability"])
        writer.writeheader()
        writer.writerows(report["out_of_fold_predictions"])
    with (output_dir / "bkt-folds.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["fold", "heldout_concepts", "train_cases", "validation_cases",
            "training_loss", "old_log_loss", "fitted_log_loss", "baseline_log_loss", "old_brier",
            "fitted_brier", "baseline_brier", "p_l0", "p_t", "p_g", "p_s"])
        writer.writeheader()
        for row in report["folds"]:
            writer.writerow({"fold": row["fold"], "heldout_concepts": ";".join(row["heldout_concepts"]),
                "train_cases": row["train_cases"], "validation_cases": row["validation_cases"],
                "training_loss": row["training_loss"], "old_log_loss": row["old_parameters"]["log_loss"],
                "fitted_log_loss": row["fitted_parameters"]["log_loss"],
                "baseline_log_loss": row["constant_baseline"]["log_loss"],
                "old_brier": row["old_parameters"]["brier"], "fitted_brier": row["fitted_parameters"]["brier"],
                "baseline_brier": row["constant_baseline"]["brier"], **row["parameters"]})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--compatible-tests-passed", action="store_true")
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    result = run_calibration(args.output_dir, compatible_tests_passed=args.compatible_tests_passed,
                             promote=args.promote)
    print(json.dumps({"research_metrics_eligible": result["gates"]["research_metrics_eligible"],
                      "curriculum_promotion_eligible": result["gates"]["curriculum_promotion_eligible"],
                      "out_of_fold": result["out_of_fold"], "candidate": result["full_data_fit"]["parameters"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
