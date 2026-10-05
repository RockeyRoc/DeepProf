"""Nested learner-grouped shrinkage selection and monotone calibration experiment."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from evaluation import bkt_stratified_diagnostics as diagnostics
from evaluation import public_bkt
from evaluation.bkt_fitting import BKTFit, _blend, fit_hierarchical, predict_sequence
from evaluation.bkt_research_adapter import (fit_item_guess_slip, predict_with_forgetting,
                                             predict_with_item_guess_slip)
from models.learner.bkt import INITIAL_PARAMETERS

SHRINKAGE_GRID = (0.0, 50.0, 200.0, 1000.0)
FORGETTING_GRID = (0.0, 0.01, 0.05, 0.10)


def _logit(probability: float) -> float:
    clipped = min(1 - 1e-12, max(1e-12, float(probability)))
    return math.log(clipped / (1.0 - clipped))


def fit_monotone_logistic(labels: list[bool], probabilities: list[float]) -> dict[str, float]:
    """Fit logistic calibration on logits of OOF probabilities, constrain slope >= 0."""
    if len(labels) != len(probabilities) or not labels:
        raise ValueError("calibration_requires_aligned_nonempty_predictions")
    if len(set(labels)) < 2:
        raise ValueError("calibration_requires_both_label_classes")
    x_values = [_logit(value) for value in probabilities]
    prevalence = (sum(labels) + 0.5) / (len(labels) + 1.0)
    intercept, slope = _logit(prevalence), 1.0
    slope_ridge = 1e-3

    def loss(a: float, b: float) -> float:
        total = 0.0
        for label, x in zip(labels, x_values):
            z = max(-40.0, min(40.0, a + b * x))
            total += math.log1p(math.exp(z)) - int(label) * z
        return total / len(labels) + 0.5 * slope_ridge * (b - 1.0) ** 2

    current = loss(intercept, slope)
    for _ in range(100):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for label, x in zip(labels, x_values):
            z = max(-40.0, min(40.0, intercept + slope * x))
            fitted = 1.0 / (1.0 + math.exp(-z))
            residual = fitted - int(label)
            curvature = fitted * (1.0 - fitted)
            g0 += residual
            g1 += residual * x
            h00 += curvature
            h01 += curvature * x
            h11 += curvature * x * x
        g0 /= len(labels)
        g1 = g1 / len(labels) + slope_ridge * (slope - 1.0)
        h00 = h00 / len(labels) + 1e-8
        h01 /= len(labels)
        h11 = h11 / len(labels) + slope_ridge
        determinant = h00 * h11 - h01 * h01
        if determinant <= 1e-18:
            break
        delta_a = (h11 * g0 - h01 * g1) / determinant
        delta_b = (-h01 * g0 + h00 * g1) / determinant
        step = 1.0
        accepted = False
        while step >= 1e-7:
            candidate_a = intercept - step * delta_a
            candidate_b = max(0.0, slope - step * delta_b)
            candidate_loss = loss(candidate_a, candidate_b)
            if candidate_loss <= current + 1e-14:
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
        change = max(abs(candidate_a - intercept), abs(candidate_b - slope))
        intercept, slope, current = candidate_a, candidate_b, candidate_loss
        if change < 1e-8:
            break
    return {"intercept": intercept, "slope": slope, "training_log_loss": current,
            "training_rows": len(labels), "training_positive": sum(labels),
            "training_negative": len(labels) - sum(labels),
            "input": "independent learner-grouped out-of-fold predictions",
            "monotone_slope_constraint": "slope >= 0"}


def apply_monotone_logistic(probability: float, calibrator: dict[str, float]) -> float:
    z = max(-40.0, min(40.0, calibrator["intercept"] + calibrator["slope"] * _logit(probability)))
    return 1.0 / (1.0 + math.exp(-z))


def _parameters(fit: BKTFit, concept_id: str, shrinkage: float):
    local = fit.concept_parameters.get(concept_id)
    count = fit.concept_counts.get(concept_id, 0)
    if local is None or count < public_bkt.MIN_CONCEPT_ATTEMPTS:
        return fit.global_parameters
    return _blend(fit.global_parameters, local, count, shrinkage)


def _probabilities(fit: BKTFit, row: public_bkt.AttemptSequence, shrinkage: float,
                   forget_probability: float = 0.0) -> list[float]:
    parameters = _parameters(fit, row.concept_id, shrinkage)
    if forget_probability:
        return predict_with_forgetting(row.outcomes, parameters, forget_probability=forget_probability)
    return predict_sequence(row.outcomes, parameters)


def _loss(rows: list[dict[str, Any]], key: str) -> float:
    return public_bkt._metrics([(row["correct"], row["probabilities"][key]) for row in rows])["log_loss"]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    methods = ("nested_selected", "nested_selected_calibrated", "nested_shrinkage_only",
               "item_difficulty", "concept_em", "original_bkt", "global_constant", "concept_constant")
    report: dict[str, Any] = {method: public_bkt._metrics(
        [(row["correct"], row["probabilities"][method]) for row in rows]) for method in methods}
    report["paired_learner_bootstrap_vs_global_constant"] = {}
    for metric, fn in (("auc", diagnostics._clustered_auc_delta_ci),
                       ("log_loss", diagnostics._clustered_mean_score_ci),
                       ("brier", diagnostics._clustered_mean_score_ci),
                       ("ece_10_bins", diagnostics._clustered_ece_ci)):
        if metric == "auc":
            interval = fn(rows, "nested_selected_calibrated", "global_constant",
                          cluster_field="learner_id", seed=public_bkt.SEED + 911)
        elif metric in {"log_loss", "brier"}:
            interval = fn(rows, "nested_selected_calibrated", "global_constant", metric,
                          seed=public_bkt.SEED + 919 + (metric == "brier"))
        else:
            interval = fn(rows, "nested_selected_calibrated", "global_constant", seed=public_bkt.SEED + 929)
        interval["evidence_status"] = diagnostics._ci_evidence_status(interval)
        report["paired_learner_bootstrap_vs_global_constant"][metric] = interval
    report["selected_method_auc_delta_vs_global_constant"] = (
        report["nested_selected"]["auc"] - report["global_constant"]["auc"]
        if report["nested_selected"]["auc"] is not None else None)
    report["opportunity_forgetting_improvements_vs_shrinkage_only"] = {
        "auc": (report["nested_selected"]["auc"] - report["nested_shrinkage_only"]["auc"]
                if report["nested_selected"]["auc"] is not None else None),
        "log_loss": report["nested_shrinkage_only"]["log_loss"] - report["nested_selected"]["log_loss"],
        "brier": report["nested_shrinkage_only"]["brier"] - report["nested_selected"]["brier"],
        "ece_10_bins": report["nested_shrinkage_only"]["ece_10_bins"] -
                       report["nested_selected"]["ece_10_bins"],
    }
    report["calibration_improvements_vs_selected_uncalibrated"] = {
        "auc": (report["nested_selected_calibrated"]["auc"] - report["nested_selected"]["auc"]
                if report["nested_selected"]["auc"] is not None else None),
        "log_loss": report["nested_selected"]["log_loss"] -
                    report["nested_selected_calibrated"]["log_loss"],
        "brier": report["nested_selected"]["brier"] -
                 report["nested_selected_calibrated"]["brier"],
        "ece_10_bins": report["nested_selected"]["ece_10_bins"] -
                       report["nested_selected_calibrated"]["ece_10_bins"],
    }
    report["item_difficulty_improvements_vs_concept_em"] = {
        "auc": (report["item_difficulty"]["auc"] - report["concept_em"]["auc"]
                if report["item_difficulty"]["auc"] is not None else None),
        "log_loss": report["concept_em"]["log_loss"] - report["item_difficulty"]["log_loss"],
        "brier": report["concept_em"]["brier"] - report["item_difficulty"]["brier"],
        "ece_10_bins": report["concept_em"]["ece_10_bins"] - report["item_difficulty"]["ece_10_bins"],
    }
    return report


def run(path: Path, output_path: Path) -> dict[str, Any]:
    dataset = public_bkt.load_assistments(path)
    development, _locked_test = public_bkt.split_outer(dataset.sequences)
    outer_predictions: list[dict[str, Any]] = []
    outer_selection: list[dict[str, Any]] = []
    for outer_fold in range(public_bkt.FOLDS):
        outer_train = [row for row in development if public_bkt.development_fold(row.learner_id) != outer_fold]
        outer_validation = [row for row in development if public_bkt.development_fold(row.learner_id) == outer_fold]
        inner_folds = [fold for fold in range(public_bkt.FOLDS) if fold != outer_fold]
        inner_scores: dict[float, list[tuple[bool, float]]] = {alpha: [] for alpha in SHRINKAGE_GRID}
        inner_fit_data: list[tuple[BKTFit, list[public_bkt.AttemptSequence]]] = []
        for inner_fold in inner_folds:
            inner_train = [row for row in outer_train if public_bkt.development_fold(row.learner_id) != inner_fold]
            inner_validation = [row for row in outer_train if public_bkt.development_fold(row.learner_id) == inner_fold]
            print(f"Outer fold {outer_fold + 1}/5: fitting inner fold {inner_fold + 1}/5", flush=True)
            fit = fit_hierarchical(inner_train, min_concept_attempts=public_bkt.MIN_CONCEPT_ATTEMPTS,
                                   shrinkage=0.0)
            inner_fit_data.append((fit, inner_validation))
            for row in inner_validation:
                for alpha in SHRINKAGE_GRID:
                    score = _probabilities(fit, row, alpha)
                    inner_scores[alpha].extend((bool(label), float(probability))
                                               for label, probability in zip(row.outcomes, score))
        mean_inner_loss = {alpha: public_bkt._metrics(rows)["log_loss"]
                           for alpha, rows in inner_scores.items()}
        # Prefer stronger regularization on exact ties; this tie rule is fixed before scoring outer validation.
        chosen_alpha = min(SHRINKAGE_GRID, key=lambda alpha: (mean_inner_loss[alpha], -alpha))
        inner_forgetting_scores: dict[float, list[tuple[bool, float]]] = {
            rate: [] for rate in FORGETTING_GRID}
        for fit, inner_validation in inner_fit_data:
            for row in inner_validation:
                for rate in FORGETTING_GRID:
                    score = _probabilities(fit, row, chosen_alpha, rate)
                    inner_forgetting_scores[rate].extend((bool(label), float(probability))
                        for label, probability in zip(row.outcomes, score))
        forgetting_loss = {rate: public_bkt._metrics(rows)["log_loss"]
                           for rate, rows in inner_forgetting_scores.items()}
        # A perfect tie keeps the no-forgetting model.
        chosen_forgetting = min(FORGETTING_GRID, key=lambda rate: (forgetting_loss[rate], rate))
        calibrator = fit_monotone_logistic(
            [label for label, _ in inner_forgetting_scores[chosen_forgetting]],
            [probability for _, probability in inner_forgetting_scores[chosen_forgetting]])
        fit = fit_hierarchical(outer_train, min_concept_attempts=public_bkt.MIN_CONCEPT_ATTEMPTS,
                               shrinkage=0.0)
        print(f"Outer fold {outer_fold + 1}/5: fitting item-specific guess/slip adapter", flush=True)
        item_fit = fit_item_guess_slip(outer_train, fit, min_item_attempts=20,
                                       prior_strength=50.0, max_iterations=100,
                                       tolerance=1e-5, consecutive_tolerance_rounds=3)
        global_constant, concept_constants = public_bkt._constant_probabilities(outer_train)
        item_test_attempts = item_covered_attempts = 0
        for row in outer_validation:
            selected_no_forgetting = _probabilities(fit, row, chosen_alpha)
            selected = _probabilities(fit, row, chosen_alpha, chosen_forgetting)
            item_scores = predict_with_item_guess_slip(row, item_fit)
            concept_parameters = fit.concept_parameters.get(row.concept_id, fit.global_parameters)
            concept_scores = predict_sequence(row.outcomes, concept_parameters)
            original = predict_sequence(row.outcomes, INITIAL_PARAMETERS)
            concept_constant = concept_constants.get(row.concept_id, global_constant)
            for index, (label, probability) in enumerate(zip(row.outcomes, selected)):
                item_key = (row.concept_id, row.problem_ids[index])
                item_test_attempts += 1
                item_covered_attempts += int(item_key in item_fit.item_emissions)
                outer_predictions.append({"learner_id": row.learner_id, "concept_id": row.concept_id,
                    "outer_fold": outer_fold + 1, "correct": bool(label),
                    "selected_shrinkage": chosen_alpha,
                    "probabilities": {"nested_selected": float(probability),
                        "nested_selected_calibrated": apply_monotone_logistic(probability, calibrator),
                        "nested_shrinkage_only": float(selected_no_forgetting[index]),
                        "item_difficulty": float(item_scores[index]),
                        "concept_em": float(concept_scores[index]),
                        "original_bkt": float(original[index]), "global_constant": global_constant,
                        "concept_constant": concept_constant}})
        outer_selection.append({"outer_fold": outer_fold + 1, "outer_train_learners": len(
            {row.learner_id for row in outer_train}), "outer_validation_learners": len(
            {row.learner_id for row in outer_validation}), "inner_log_loss_by_shrinkage": {
                str(alpha): float(mean_inner_loss[alpha]) for alpha in SHRINKAGE_GRID},
            "selected_shrinkage": chosen_alpha, "calibrator": calibrator,
            "selected_opportunity_forgetting": chosen_forgetting,
            "inner_forgetting_log_loss_at_selected_shrinkage": {
                str(rate): float(forgetting_loss[rate]) for rate in FORGETTING_GRID},
            "item_difficulty": {"eligible_concept_item_pairs": len(item_fit.item_emissions),
                "training_item_attempts_by_concept_counts": len(item_fit.item_attempt_counts),
                "max_em_iterations": 100, "iterations_used": item_fit.iterations,
                "converged": all(item_fit.converged_by_concept.values()),
                "converged_by_concept": item_fit.converged_by_concept,
                "objective_history": item_fit.objective_history,
                "parameter_delta_history": item_fit.parameter_delta_history,
                "boundary_parameter_count": item_fit.boundary_parameter_count,
                "convergence_rule": "max parameter delta < 1e-5 for three consecutive rounds; max 100 rounds",
                "prior_strength": item_fit.prior_strength, "minimum_item_attempts": item_fit.min_item_attempts,
                "heldout_attempts": item_test_attempts, "heldout_attempts_with_item_specific_emissions": item_covered_attempts,
                "heldout_item_coverage": (item_covered_attempts / item_test_attempts
                    if item_test_attempts else None)},
            "outer_validation_attempts": sum(len(row.outcomes) for row in outer_validation)})
        partial = {"schema_version": "deepprof-bkt-nested-selection-v1", "source_sha256": dataset.sha256,
                   "completed_outer_folds": outer_selection, "partial": True}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.with_suffix(output_path.suffix + ".partial.json").write_text(
            json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"schema_version": "deepprof-bkt-nested-selection-v1", "source_sha256": dataset.sha256,
        "source_name": dataset.source_name, "data_domain": "ASSISTments public math; research only",
        "selection_design": "outer five-fold learner grouped CV on the development partition; within each outer training partition, four inner learner-grouped folds select shrinkage by mean log loss (ties prefer larger shrinkage), then select opportunity-forgetting probability at that shrinkage (ties prefer zero forgetting)",
        "shrinkage_grid": list(SHRINKAGE_GRID), "prediction_timing": "score before observing current response; update after score",
        "opportunity_forgetting_grid": list(FORGETTING_GRID),
        "calibration_design": "fit only on inner-fold out-of-fold predictions of the selected shrinkage-plus-forgetting model, separately within each outer training partition; monotone logistic slope constrained nonnegative",
        "outer_fold_selection": outer_selection, "nested_development_oof": _summarize(outer_predictions),
        "nested_outer_predictions": len(outer_predictions), "bootstrap_samples": 1000,
        "locked_test_used_for_selection": False,
        "interpretation": "Nested development estimates are tuning diagnostics. The historical locked test is not used for selection and is not presented as a new independent validation."}
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(output_path.suffix + ".partial.json").unlink(missing_ok=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.data, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
