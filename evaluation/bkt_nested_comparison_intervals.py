"""Rebuild nested OOF predictions from stored fold choices and calculate paired intervals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluation import bkt_stratified_diagnostics as diagnostics
from evaluation import public_bkt
from evaluation.bkt_fitting import fit_hierarchical, predict_sequence
from evaluation.bkt_nested_selection import (_probabilities, apply_monotone_logistic,
                                              fit_monotone_logistic)
from evaluation.bkt_research_adapter import fit_item_guess_slip, predict_with_item_guess_slip
from models.learner.bkt import INITIAL_PARAMETERS


PAIRED_COMPARISONS = {
    "selected_shrinkage_forgetting_vs_concept_em": ("nested_selected", "concept_em"),
    "selected_calibrated_vs_concept_em": ("nested_selected_calibrated", "concept_em"),
    "selected_calibrated_vs_historical_shrinkage_200": (
        "nested_selected_calibrated", "historical_shrinkage_200"),
    "item_difficulty_vs_concept_em": ("item_difficulty", "concept_em"),
    "item_difficulty_vs_historical_shrinkage_200": ("item_difficulty", "historical_shrinkage_200"),
    "forgetting_vs_same_shrinkage_no_forgetting": ("nested_selected", "nested_shrinkage_only"),
    "calibration_vs_selected_uncalibrated": ("nested_selected_calibrated", "nested_selected"),
}


def _paired_metrics(rows: list[dict[str, Any]], *, seed: int) -> dict[str, Any]:
    report: dict[str, Any] = {}
    point_metrics = {method: public_bkt._metrics(
        [(row["correct"], row["probabilities"][method]) for row in rows])
        for method in {name for pair in PAIRED_COMPARISONS.values() for name in pair}}
    for index, (name, (model, baseline)) in enumerate(PAIRED_COMPARISONS.items()):
        metrics: dict[str, Any] = {}
        for offset, metric in enumerate(("auc", "log_loss", "brier", "ece_10_bins")):
            if metric == "auc":
                interval = diagnostics._clustered_auc_delta_ci(
                    rows, model, baseline, cluster_field="learner_id", seed=seed + index * 13 + offset)
            elif metric in {"log_loss", "brier"}:
                interval = diagnostics._clustered_mean_score_ci(
                    rows, model, baseline, metric, seed=seed + index * 13 + offset)
            else:
                interval = diagnostics._clustered_ece_ci(
                    rows, model, baseline, seed=seed + index * 13 + offset)
            interval["evidence_status"] = diagnostics._ci_evidence_status(interval)
            model_metric = point_metrics[model][metric]
            baseline_metric = point_metrics[baseline][metric]
            interval["point_improvement"] = (model_metric - baseline_metric if metric == "auc"
                                               else baseline_metric - model_metric)
            metrics[metric] = interval
        report[name] = {"model": model, "baseline": baseline, "paired_learner_bootstrap_95": metrics}
    return report


def run(data_path: Path, selection_path: Path, output_path: Path) -> dict[str, Any]:
    dataset = public_bkt.load_assistments(data_path)
    selection_bytes = selection_path.read_bytes()
    selection_hash = hashlib.sha256(selection_bytes).hexdigest()
    selection = json.loads(selection_bytes)
    if selection.get("source_sha256") != dataset.sha256:
        raise ValueError("selection_and_data_source_hash_mismatch")
    development, _locked_test = public_bkt.split_outer(dataset.sequences)
    predictions: list[dict[str, Any]] = []
    fold_metadata: list[dict[str, Any]] = []
    calibration_oof_labels: list[bool] = []
    calibration_oof_probabilities: list[float] = []
    for fold_index, selected in enumerate(selection["outer_fold_selection"]):
        fold = int(selected["outer_fold"]) - 1
        train = [row for row in development if public_bkt.development_fold(row.learner_id) != fold]
        validation = [row for row in development if public_bkt.development_fold(row.learner_id) == fold]
        print(f"Rebuilding paired OOF predictions for outer fold {fold + 1}/5", flush=True)
        fit = fit_hierarchical(train, min_concept_attempts=public_bkt.MIN_CONCEPT_ATTEMPTS,
                               shrinkage=0.0)
        item_fit = fit_item_guess_slip(train, fit, min_item_attempts=20, prior_strength=50.0,
                                       max_iterations=int(selected["item_difficulty"]["max_em_iterations"]),
                                       tolerance=1e-5, consecutive_tolerance_rounds=3)
        global_constant, concept_constants = public_bkt._constant_probabilities(train)
        alpha = float(selected["selected_shrinkage"])
        forgetting = float(selected["selected_opportunity_forgetting"])
        calibrator = selected["calibrator"]
        item_attempts = item_covered = 0
        fold_attempts = sum(len(row.outcomes) for row in validation)
        for row in validation:
            selected_scores = _probabilities(fit, row, alpha, forgetting)
            shrinkage_scores = _probabilities(fit, row, alpha)
            historical_shrinkage_scores = _probabilities(fit, row, 200.0)
            calibrated_scores = [apply_monotone_logistic(score, calibrator) for score in selected_scores]
            item_scores = predict_with_item_guess_slip(row, item_fit)
            concept_model = fit.concept_parameters.get(row.concept_id, fit.global_parameters)
            concept_scores = predict_sequence(row.outcomes, concept_model)
            original_scores = predict_sequence(row.outcomes, INITIAL_PARAMETERS)
            concept_constant = concept_constants.get(row.concept_id, global_constant)
            for index, label in enumerate(row.outcomes):
                calibration_oof_labels.append(bool(label))
                calibration_oof_probabilities.append(float(selected_scores[index]))
                item_attempts += 1
                item_covered += int((row.concept_id, row.problem_ids[index]) in item_fit.item_emissions)
                predictions.append({"learner_id": row.learner_id, "concept_id": row.concept_id,
                    "outer_fold": fold + 1, "correct": bool(label), "probabilities": {
                        "nested_selected": float(selected_scores[index]),
                        "nested_selected_calibrated": float(calibrated_scores[index]),
                        "nested_shrinkage_only": float(shrinkage_scores[index]),
                        "historical_shrinkage_200": float(historical_shrinkage_scores[index]),
                        "item_difficulty": float(item_scores[index]),
                        "concept_em": float(concept_scores[index]),
                        "original_bkt": float(original_scores[index]),
                        "global_constant": global_constant, "concept_constant": concept_constant}})
        fold_metadata.append({"outer_fold": fold + 1, "attempts": fold_attempts,
            "selected_shrinkage": alpha, "selected_forgetting": forgetting,
            "item_eligible_pairs": len(item_fit.item_emissions),
            "item_heldout_coverage": item_covered / item_attempts if item_attempts else None,
            "item_em_iterations": item_fit.iterations,
            "item_em_converged": all(item_fit.converged_by_concept.values()),
            "item_em_converged_by_concept": item_fit.converged_by_concept,
            "item_em_objective_history": item_fit.objective_history,
            "item_em_parameter_delta_history": item_fit.parameter_delta_history,
            "item_boundary_parameter_count": item_fit.boundary_parameter_count})
    full_calibrator = fit_monotone_logistic(calibration_oof_labels, calibration_oof_probabilities)
    selected_alpha = max({float(row["selected_shrinkage"]) for row in selection["outer_fold_selection"]},
        key=lambda alpha: (sum(float(row["selected_shrinkage"]) == alpha
                               for row in selection["outer_fold_selection"]), alpha))
    selected_forgetting = max({float(row["selected_opportunity_forgetting"])
        for row in selection["outer_fold_selection"]},
        key=lambda rate: (sum(float(row["selected_opportunity_forgetting"]) == rate
                              for row in selection["outer_fold_selection"]), -rate))
    print("Scoring previously viewed locked test for historical regression only", flush=True)
    full_fit = fit_hierarchical(development, min_concept_attempts=public_bkt.MIN_CONCEPT_ATTEMPTS,
                                shrinkage=0.0)
    full_item_fit = fit_item_guess_slip(development, full_fit, min_item_attempts=20,
                                        prior_strength=50.0, max_iterations=100,
                                        tolerance=1e-5, consecutive_tolerance_rounds=3)
    _, locked_test = public_bkt.split_outer(dataset.sequences)
    test_global_constant, test_concept_constants = public_bkt._constant_probabilities(development)
    locked_test_predictions: list[dict[str, Any]] = []
    test_item_attempts = test_item_covered = 0
    for row in locked_test:
        selected_scores = _probabilities(full_fit, row, selected_alpha, selected_forgetting)
        shrinkage_scores = _probabilities(full_fit, row, selected_alpha)
        historical_shrinkage_scores = _probabilities(full_fit, row, 200.0)
        item_scores = predict_with_item_guess_slip(row, full_item_fit)
        concept_model = full_fit.concept_parameters.get(row.concept_id, full_fit.global_parameters)
        concept_scores = predict_sequence(row.outcomes, concept_model)
        original_scores = predict_sequence(row.outcomes, INITIAL_PARAMETERS)
        concept_constant = test_concept_constants.get(row.concept_id, test_global_constant)
        for index, label in enumerate(row.outcomes):
            test_item_attempts += 1
            test_item_covered += int((row.concept_id, row.problem_ids[index]) in full_item_fit.item_emissions)
            locked_test_predictions.append({"learner_id": row.learner_id, "concept_id": row.concept_id,
                "correct": bool(label), "probabilities": {
                    "nested_selected": float(selected_scores[index]),
                    "nested_selected_calibrated": apply_monotone_logistic(
                        float(selected_scores[index]), full_calibrator),
                    "nested_shrinkage_only": float(shrinkage_scores[index]),
                    "historical_shrinkage_200": float(historical_shrinkage_scores[index]),
                    "item_difficulty": float(item_scores[index]), "concept_em": float(concept_scores[index]),
                    "original_bkt": float(original_scores[index]), "global_constant": test_global_constant,
                    "concept_constant": concept_constant}})
    report = {"schema_version": "deepprof-bkt-nested-paired-intervals-v1",
        "source_sha256": dataset.sha256, "selection_report_sha256": selection_hash,
        "data_domain": "ASSISTments public math; learner-grouped nested development OOF; research only",
        "refit_parameters": {"item_min_attempts": 20, "item_prior_strength": 50.0,
                             "item_max_em_iterations": 100,
                             "item_convergence_rule": "max parameter delta < 1e-5 for three consecutive rounds"},
        "outer_fold_metadata": fold_metadata, "attempts": len(predictions),
        "metrics": {name: public_bkt._metrics([(row["correct"], row["probabilities"][name])
                                               for row in predictions])
                    for name in ("nested_selected", "nested_selected_calibrated", "nested_shrinkage_only",
                                 "item_difficulty", "concept_em", "original_bkt", "global_constant",
                                 "concept_constant", "historical_shrinkage_200")},
        "paired_comparisons": _paired_metrics(predictions, seed=public_bkt.SEED + 202),
        "locked_test_regression": {"status": "historical regression comparison; this test partition was already inspected and is not a new independent validation",
            "selected_shrinkage": selected_alpha, "selected_opportunity_forgetting": selected_forgetting,
            "calibrator_fit_on_development_oof_only": full_calibrator,
            "test_attempts": len(locked_test_predictions),
            "test_learners": len({row.learner_id for row in locked_test}),
            "item_difficulty": {"eligible_pairs": len(full_item_fit.item_emissions),
                "heldout_attempts": test_item_attempts, "heldout_item_coverage":
                    test_item_covered / test_item_attempts if test_item_attempts else None,
                "em_iterations": full_item_fit.iterations, "max_em_iterations": 100,
                "converged": all(full_item_fit.converged_by_concept.values()),
                "converged_by_concept": full_item_fit.converged_by_concept,
                "boundary_parameter_count": full_item_fit.boundary_parameter_count},
            "metrics": {name: public_bkt._metrics([(row["correct"], row["probabilities"][name])
                                                   for row in locked_test_predictions])
                        for name in ("nested_selected", "nested_selected_calibrated", "nested_shrinkage_only",
                                     "item_difficulty", "concept_em", "original_bkt", "global_constant",
                                     "concept_constant", "historical_shrinkage_200")},
            "paired_comparisons": _paired_metrics(locked_test_predictions, seed=public_bkt.SEED + 303)},
        "bootstrap_samples": 1000,
        "interpretation": "These are development OOF uncertainty intervals for the stored fold-specific selected models. The previously viewed locked test was not used; item-specific EM candidates without the three-round convergence rule are exploratory and not eligible for selection."}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.data, args.selection, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
