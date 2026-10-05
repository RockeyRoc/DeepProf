"""Leakage-safe sequence-length and concept-coverage diagnostics for BKT."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from evaluation import bkt_calibration, public_bkt
from evaluation.bkt_fitting import fit_hierarchical, predict_sequence
from models.learner.bkt import INITIAL_PARAMETERS

try:
    import numpy as np
except ImportError:  # pragma: no cover - the normal research environment bundles NumPy
    np = None


PUBLIC_LENGTH_BINS = ((1, 3, "1-3"), (4, 10, "4-10"), (11, 20, "11-20"), (21, math.inf, "21+"))
TRAINING_COVERAGE_BINS = ((0, 0, "0"), (1, 39, "1-39"), (40, 199, "40-199"), (200, math.inf, "200+"))
PRIOR_ATTEMPT_BINS = ((0, 2, "0-2"), (3, 5, "3-5"), (6, 9, "6-9"), (10, math.inf, "10+"))
METHODS = ("original_bkt", "global_em", "concept_em", "hierarchical_shrinkage",
           "global_constant", "concept_constant")


def _length_bin(value: int, bins: tuple[tuple[int, float, str], ...]) -> str:
    return next(name for low, high, name in bins if low <= value <= high)


def _coverage(train: list[public_bkt.AttemptSequence]) -> tuple[dict[str, int], dict[str, int]]:
    attempts: dict[str, int] = defaultdict(int)
    learners: dict[str, set[str]] = defaultdict(set)
    for row in train:
        attempts[row.concept_id] += len(row.outcomes)
        learners[row.concept_id].add(row.learner_id)
    return dict(attempts), {concept: len(values) for concept, values in learners.items()}


def _predict_fold(train: list[public_bkt.AttemptSequence], validation: list[public_bkt.AttemptSequence],
                  *, split: str, fold: int | None = None) -> list[dict[str, Any]]:
    fit = fit_hierarchical(train, min_concept_attempts=public_bkt.MIN_CONCEPT_ATTEMPTS)
    global_p, concept_p = public_bkt._constant_probabilities(train)
    train_attempts, train_learners = _coverage(train)
    observations: list[dict[str, Any]] = []
    for row in validation:
        model_parameters = {
            "original_bkt": INITIAL_PARAMETERS,
            "global_em": fit.global_parameters,
            "concept_em": fit.concept_parameters.get(row.concept_id, fit.global_parameters),
            "hierarchical_shrinkage": fit.by_concept.get(row.concept_id, fit.global_parameters),
        }
        probabilities = {name: predict_sequence(row.outcomes, parameters)
                         for name, parameters in model_parameters.items()}
        concept_constant = concept_p.get(row.concept_id, global_p)
        probabilities["global_constant"] = [global_p] * len(row.outcomes)
        probabilities["concept_constant"] = [concept_constant] * len(row.outcomes)
        train_count = int(train_attempts.get(row.concept_id, 0))
        train_learner_count = int(train_learners.get(row.concept_id, 0))
        for index, label in enumerate(row.outcomes):
            values = {name: float(sequence[index]) for name, sequence in probabilities.items()}
            observations.append({
                "learner_id": row.learner_id,
                "concept_id": row.concept_id,
                "split": split,
                "fold": fold,
                "sequence_length": len(row.outcomes),
                "sequence_length_bin": _length_bin(len(row.outcomes), PUBLIC_LENGTH_BINS),
                "prior_attempts": index,
                "prior_attempt_bin": _length_bin(index, PRIOR_ATTEMPT_BINS),
                "training_concept_attempts": train_count,
                "training_concept_attempts_bin": _length_bin(train_count, TRAINING_COVERAGE_BINS),
                "training_concept_learners": train_learner_count,
                "correct": bool(label),
                "probabilities": values,
            })
    return observations


def _clustered_log_loss_ci(rows: list[dict[str, Any]], model: str, baseline: str,
                           *, seed: int, samples: int = 1000) -> dict[str, Any]:
    by_learner: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    epsilon = 1e-12
    for row in rows:
        p_model = min(1 - epsilon, max(epsilon, row["probabilities"][model]))
        p_baseline = min(1 - epsilon, max(epsilon, row["probabilities"][baseline]))
        outcome = int(row["correct"])
        by_learner[row["learner_id"]][0] += -(outcome * math.log(p_model) + (1-outcome)*math.log(1-p_model))
        by_learner[row["learner_id"]][1] += -(outcome * math.log(p_baseline) + (1-outcome)*math.log(1-p_baseline))
        by_learner[row["learner_id"]][2] += 1
    learners = sorted(by_learner)
    if len(learners) < 2:
        return {"learners": len(learners), "lower_95": None, "median": None, "upper_95": None,
                "status": "insufficient_learner_clusters"}
    rng = random.Random(seed)
    samples_loss: list[float] = []
    for _ in range(samples):
        selected = [learners[rng.randrange(len(learners))] for _ in learners]
        model_loss = sum(by_learner[key][0] for key in selected)
        baseline_loss = sum(by_learner[key][1] for key in selected)
        count = sum(by_learner[key][2] for key in selected)
        if count:
            samples_loss.append((baseline_loss - model_loss) / count)
    samples_loss.sort()
    return {"learners": len(learners),
            "lower_95": samples_loss[int(0.025 * (len(samples_loss) - 1))],
            "median": samples_loss[len(samples_loss) // 2],
            "upper_95": samples_loss[int(0.975 * (len(samples_loss) - 1))],
            "status": "computed; positive means lower model log loss"}


def _clustered_mean_score_ci(rows: list[dict[str, Any]], model: str, baseline: str, metric: str,
                             *, seed: int, samples: int = 1000) -> dict[str, Any]:
    """Paired learner bootstrap for additive proper-score improvements."""
    score_index = {"log_loss": 0, "brier": 1}
    if metric not in score_index:
        raise ValueError("unsupported_additive_metric")
    by_learner: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    epsilon = 1e-12
    for row in rows:
        outcome = int(row["correct"])
        for name, slot in ((model, 0), (baseline, 1)):
            probability = min(1 - epsilon, max(epsilon, float(row["probabilities"][name])))
            value = (-(outcome * math.log(probability) + (1-outcome) * math.log(1-probability))
                     if metric == "log_loss" else (probability - outcome) ** 2)
            by_learner[row["learner_id"]][slot] += value
        by_learner[row["learner_id"]][2] += 1
    learners = sorted(by_learner)
    if len(learners) < 2:
        return {"learners": len(learners), "lower_95": None, "median": None, "upper_95": None,
                "status": "insufficient_learner_clusters"}
    if np is not None:
        totals = np.asarray([by_learner[key] for key in learners], dtype=np.float64)
        rng = np.random.default_rng(seed)
        improvements = []
        for _ in range(samples):
            selected = rng.integers(0, len(learners), size=len(learners))
            model_score, baseline_score, count = totals[selected].sum(axis=0)
            if count:
                improvements.append(float((baseline_score - model_score) / count))
        improvements.sort()
        return {"learners": len(learners),
                "lower_95": improvements[int(0.025 * (len(improvements) - 1))],
                "median": improvements[len(improvements) // 2],
                "upper_95": improvements[int(0.975 * (len(improvements) - 1))],
                "status": f"paired learner-cluster bootstrap; positive means lower model {metric}"}
    rng = random.Random(seed)
    improvements: list[float] = []
    for _ in range(samples):
        selected = [learners[rng.randrange(len(learners))] for _ in learners]
        model_score = sum(by_learner[key][0] for key in selected)
        baseline_score = sum(by_learner[key][1] for key in selected)
        count = sum(by_learner[key][2] for key in selected)
        if count:
                improvements.append(float((baseline_score - model_score) / count))
    improvements.sort()
    return {"learners": len(learners),
            "lower_95": improvements[int(0.025 * (len(improvements) - 1))],
            "median": improvements[len(improvements) // 2],
            "upper_95": improvements[int(0.975 * (len(improvements) - 1))],
            "status": f"paired learner-cluster bootstrap; positive means lower model {metric}"}


def _ece_from_histogram(histogram: list[list[float]], total: float) -> float:
    if not total:
        return math.nan
    return sum((count / total) * abs((score_sum / count) - (positive / count))
               for count, positive, score_sum in histogram if count)


def _clustered_ece_ci(rows: list[dict[str, Any]], model: str, baseline: str,
                      *, seed: int, samples: int = 1000) -> dict[str, Any]:
    """Paired learner bootstrap using the benchmark's exact ten-bin ECE definition."""
    by_learner: dict[str, dict[str, list[list[float]]]] = defaultdict(
        lambda: {name: [[0.0, 0.0, 0.0] for _ in range(10)] for name in (model, baseline)})
    for row in rows:
        outcome = int(row["correct"])
        for name in (model, baseline):
            probability = min(1.0, max(0.0, float(row["probabilities"][name])))
            bucket = min(9, int(probability * 10))
            record = by_learner[row["learner_id"]][name][bucket]
            record[0] += 1
            record[1] += outcome
            record[2] += probability
    learners = sorted(by_learner)
    if len(learners) < 2:
        return {"learners": len(learners), "lower_95": None, "median": None, "upper_95": None,
                "status": "insufficient_learner_clusters"}
    if np is not None:
        per_learner = np.asarray([
            [by_learner[learner][name] for name in (model, baseline)] for learner in learners
        ], dtype=np.float64)
        rng = np.random.default_rng(seed)
        improvements = []
        for _ in range(samples):
            selected = rng.integers(0, len(learners), size=len(learners))
            histograms = per_learner[selected].sum(axis=0)
            eces: list[float] = []
            for method_histogram in histograms:
                counts = method_histogram[:, 0]
                total = float(counts.sum())
                if not total:
                    eces.append(math.nan)
                    continue
                populated = counts > 0
                mean_score = np.divide(method_histogram[:, 2], counts,
                                       out=np.zeros_like(counts), where=populated)
                accuracy = np.divide(method_histogram[:, 1], counts,
                                     out=np.zeros_like(counts), where=populated)
                eces.append(float(np.sum((counts[populated] / total) *
                                         np.abs(mean_score[populated] - accuracy[populated]))))
            if all(math.isfinite(value) for value in eces):
                improvements.append(eces[1] - eces[0])
        improvements.sort()
        if not improvements:
            return {"learners": len(learners), "lower_95": None, "median": None, "upper_95": None,
                    "status": "no_estimable_bootstrap_samples"}
        return {"learners": len(learners),
                "lower_95": improvements[int(0.025 * (len(improvements) - 1))],
                "median": improvements[len(improvements) // 2],
                "upper_95": improvements[int(0.975 * (len(improvements) - 1))],
                "status": "paired learner-cluster bootstrap; positive means lower model ECE"}
    rng = random.Random(seed)
    improvements: list[float] = []
    for _ in range(samples):
        selected = [learners[rng.randrange(len(learners))] for _ in learners]
        histograms = {name: [[0.0, 0.0, 0.0] for _ in range(10)] for name in (model, baseline)}
        totals = {name: 0.0 for name in (model, baseline)}
        for learner in selected:
            for name in (model, baseline):
                for bucket, values in enumerate(by_learner[learner][name]):
                    for field, value in enumerate(values):
                        histograms[name][bucket][field] += value
                    totals[name] += values[0]
        model_ece = _ece_from_histogram(histograms[model], totals[model])
        baseline_ece = _ece_from_histogram(histograms[baseline], totals[baseline])
        if math.isfinite(model_ece) and math.isfinite(baseline_ece):
            improvements.append(baseline_ece - model_ece)
    improvements.sort()
    if not improvements:
        return {"learners": len(learners), "lower_95": None, "median": None, "upper_95": None,
                "status": "no_estimable_bootstrap_samples"}
    return {"learners": len(learners),
            "lower_95": improvements[int(0.025 * (len(improvements) - 1))],
            "median": improvements[len(improvements) // 2],
            "upper_95": improvements[int(0.975 * (len(improvements) - 1))],
            "status": "paired learner-cluster bootstrap; positive means lower model ECE"}


def _ci_evidence_status(ci: dict[str, Any]) -> str:
    lower, upper = ci.get("lower_95"), ci.get("upper_95")
    if lower is None or upper is None:
        return "not_estimable"
    if lower <= 0 <= upper:
        return "evidence_insufficient_ci_crosses_zero"
    return "directionally_positive_ci_excludes_zero" if lower > 0 else "directionally_negative_ci_excludes_zero"


def _clustered_auc_delta_ci(rows: list[dict[str, Any]], model: str, baseline: str,
                            *, cluster_field: str, seed: int, samples: int = 1000) -> dict[str, Any]:
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cluster[str(row[cluster_field])].append(row)
    clusters = sorted(by_cluster)
    if len(clusters) < 2:
        return {"clusters": len(clusters), "lower_95": None, "median": None, "upper_95": None,
                "status": "insufficient_clusters"}
    values: list[float] = []
    if np is not None:
        # Bootstrap clusters as integer multiplicities and compute exact tied-rank
        # AUC from weighted score histograms. This avoids rebuilding and sorting
        # hundreds of thousands of attempt rows for every replicate.
        cluster_index = {cluster: index for index, cluster in enumerate(clusters)}
        cluster_codes = np.fromiter((cluster_index[str(row[cluster_field])] for row in rows),
                                    dtype=np.int32, count=len(rows))
        labels = np.fromiter((int(bool(row["correct"])) for row in rows), dtype=np.int8, count=len(rows))
        encoded: dict[str, tuple[Any, int]] = {}
        for name in (model, baseline):
            scores = np.fromiter((float(row["probabilities"][name]) for row in rows),
                                 dtype=np.float64, count=len(rows))
            unique, inverse = np.unique(scores, return_inverse=True)
            encoded[name] = (inverse.astype(np.int32, copy=False), len(unique))

        def weighted_auc(score_codes: Any, score_count: int, observation_weights: Any) -> float | None:
            positive = np.bincount(score_codes, weights=observation_weights * labels,
                                   minlength=score_count)
            negative = np.bincount(score_codes, weights=observation_weights * (1 - labels),
                                   minlength=score_count)
            positive_total = float(positive.sum())
            negative_total = float(negative.sum())
            if positive_total == 0.0 or negative_total == 0.0:
                return None
            negative_before = np.cumsum(negative) - negative
            return float(np.dot(positive, negative_before + 0.5 * negative) /
                         (positive_total * negative_total))

        rng = np.random.default_rng(seed)
        for _ in range(samples):
            multiplicities = np.bincount(rng.integers(0, len(clusters), size=len(clusters)),
                                         minlength=len(clusters)).astype(np.float64)
            observation_weights = multiplicities[cluster_codes]
            model_auc = weighted_auc(*encoded[model], observation_weights)
            base_auc = weighted_auc(*encoded[baseline], observation_weights)
            if model_auc is not None and base_auc is not None:
                values.append(model_auc - base_auc)
    else:  # Exact but slower fallback for installations without NumPy.
        rng = random.Random(seed)
        for _ in range(samples):
            selected = [clusters[rng.randrange(len(clusters))] for _ in clusters]
            sample_rows = [row for cluster in selected for row in by_cluster[cluster]]
            model_auc = public_bkt._metrics([(row["correct"], row["probabilities"][model])
                                             for row in sample_rows])["auc"]
            base_auc = public_bkt._metrics([(row["correct"], row["probabilities"][baseline])
                                            for row in sample_rows])["auc"]
            if model_auc is not None and base_auc is not None:
                values.append(float(model_auc) - float(base_auc))
    values.sort()
    if not values:
        return {"clusters": len(clusters), "lower_95": None, "median": None, "upper_95": None,
                "status": "single_class_in_all_bootstrap_samples"}
    return {"clusters": len(clusters),
            "lower_95": values[int(0.025 * (len(values) - 1))],
            "median": values[len(values) // 2],
            "upper_95": values[int(0.975 * (len(values) - 1))],
            "status": "exploratory_cluster_bootstrap; positive favors model"}


def _summarize(observations: list[dict[str, Any]], *, seed: int) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for method in METHODS:
        metrics[method] = public_bkt._metrics(
            [(row["correct"], row["probabilities"][method]) for row in observations])
    metrics["auc_delta_vs_global_constant"] = {
        method: (metrics[method]["auc"] - metrics["global_constant"]["auc"]
                 if metrics[method]["auc"] is not None and metrics["global_constant"]["auc"] is not None else None)
        for method in METHODS
    }
    metrics["auc_delta_vs_concept_constant"] = {
        method: (metrics[method]["auc"] - metrics["concept_constant"]["auc"]
                 if metrics[method]["auc"] is not None and metrics["concept_constant"]["auc"] is not None else None)
        for method in METHODS
    }
    for baseline in ("global_constant", "concept_constant"):
        for metric, key in (("log_loss", "log_loss_improvement"), ("brier", "brier_improvement"),
                            ("ece_10_bins", "ece_improvement")):
            metrics[f"{key}_vs_{baseline}"] = {
                method: (metrics[baseline][metric] - metrics[method][metric]
                         if metrics[baseline][metric] is not None and metrics[method][metric] is not None else None)
                for method in METHODS
            }
    for baseline, offset in (("global_constant", 2), ("concept_constant", 3)):
        for metric in ("log_loss", "brier"):
            key = f"{metric}_improvement_ci_vs_{baseline}"
            metrics[key] = _clustered_mean_score_ci(observations, "hierarchical_shrinkage", baseline,
                                                    metric, seed=seed + offset + (5 if metric == "brier" else 0))
            metrics[key]["evidence_status"] = _ci_evidence_status(metrics[key])
        metrics[f"ece_improvement_ci_vs_{baseline}"] = _clustered_ece_ci(
            observations, "hierarchical_shrinkage", baseline, seed=seed + offset + 7)
        metrics[f"ece_improvement_ci_vs_{baseline}"]["evidence_status"] = _ci_evidence_status(
            metrics[f"ece_improvement_ci_vs_{baseline}"])
        metrics[f"log_loss_improvement_ci_vs_{baseline}"]["evidence_status"] = _ci_evidence_status(
            metrics[f"log_loss_improvement_ci_vs_{baseline}"])
        auc_ci = _clustered_auc_delta_ci(
            observations, "hierarchical_shrinkage", baseline, cluster_field="learner_id",
            seed=seed + offset)
        auc_ci["evidence_status"] = _ci_evidence_status(auc_ci)
        metrics[f"auc_delta_ci_vs_{baseline}"] = auc_ci
    metrics["positive_count"] = sum(bool(row["correct"]) for row in observations)
    metrics["negative_count"] = len(observations) - metrics["positive_count"]
    metrics["unique_learners"] = len({row["learner_id"] for row in observations})
    metrics["unique_concepts"] = len({row["concept_id"] for row in observations})
    metrics["auc_status"] = "computed" if metrics["hierarchical_shrinkage"]["auc"] is not None else "single_class_auc_not_computable"
    return metrics


def _bin_report(observations: list[dict[str, Any]], field: str, *, seed: int) -> dict[str, Any]:
    present = {str(row[field]) for row in observations}
    expected_bins = {"sequence_length_bin": PUBLIC_LENGTH_BINS,
                     "prior_attempt_bin": PRIOR_ATTEMPT_BINS,
                     "training_concept_attempts_bin": TRAINING_COVERAGE_BINS}.get(field)
    names = ([name for _, _, name in expected_bins if name in present]
             if expected_bins else sorted(present))
    result: dict[str, Any] = {}
    for index, name in enumerate(names):
        rows = [row for row in observations if str(row[field]) == name]
        if not rows:
            continue
        print(f"Summarizing {field}={name} ({index + 1}/{len(names)}; n={len(rows)})", flush=True)
        report = _summarize(rows, seed=seed + index * 13)
        report["n"] = len(rows)
        report["positive_count"] = sum(bool(row["correct"]) for row in rows)
        report["negative_count"] = len(rows) - report["positive_count"]
        by_concept_training_learners: dict[str, int] = {}
        for row in rows:
            by_concept_training_learners[row["concept_id"]] = int(row["training_concept_learners"])
        training_sizes = list(by_concept_training_learners.values())
        report["concepts"] = len(by_concept_training_learners)
        report["training_learners_per_concept"] = {
            "minimum": min(training_sizes) if training_sizes else None,
            "maximum": max(training_sizes) if training_sizes else None,
            "mean": sum(training_sizes) / len(training_sizes) if training_sizes else None,
        }
        report["per_fold"] = {}
        for fold in sorted({row["fold"] for row in rows}, key=lambda x: -1 if x is None else x):
            fold_rows = [row for row in rows if row["fold"] == fold]
            report["per_fold"]["locked_test" if fold is None else f"fold_{fold}"] = {
                "n": len(fold_rows), "positive_count": sum(bool(row["correct"]) for row in fold_rows),
                "negative_count": sum(not bool(row["correct"]) for row in fold_rows),
                "hierarchical_auc": public_bkt._metrics([(r["correct"], r["probabilities"]["hierarchical_shrinkage"])
                                                          for r in fold_rows])["auc"],
                "global_constant_auc": public_bkt._metrics([(r["correct"], r["probabilities"]["global_constant"])
                                                             for r in fold_rows])["auc"],
                "concept_constant_auc": public_bkt._metrics([(r["correct"], r["probabilities"]["concept_constant"])
                                                              for r in fold_rows])["auc"],
            }
        result[name] = report
    return result


def run_public(path: Path, output_path: Path) -> dict[str, Any]:
    dataset = public_bkt.load_assistments(path)
    development, final_test = public_bkt.split_outer(dataset.sequences)
    if not development or not final_test:
        raise ValueError("assistments_fixed_learner_split_is_empty")
    oof: list[dict[str, Any]] = []
    for fold in range(public_bkt.FOLDS):
        print(f"Fitting development fold {fold + 1}/{public_bkt.FOLDS}", flush=True)
        train = [row for row in development if public_bkt.development_fold(row.learner_id) != fold]
        validation = [row for row in development if public_bkt.development_fold(row.learner_id) == fold]
        oof.extend(_predict_fold(train, validation, split="development_oof", fold=fold + 1))
        print(f"Scored {len(oof)} development attempts", flush=True)
    print("Fitting locked-test model", flush=True)
    final = _predict_fold(development, final_test, split="locked_test", fold=None)
    print("Summarizing overall and stratified metrics (1000 learner bootstrap draws per comparison)", flush=True)
    test_learners = {row.learner_id for row in final_test}
    split_membership = sorted(hashlib.sha256(learner.encode("utf-8")).hexdigest()
                              for learner in test_learners)
    split_digest = hashlib.sha256((str(public_bkt.SEED) + "|" + "\n".join(split_membership)).encode("ascii")).hexdigest()
    report = {
        "schema_version": "deepprof-public-bkt-stratified-v1",
        "source_sha256": dataset.sha256, "source_name": dataset.source_name,
        "split_sha256": split_digest,
        "data_domain": "ASSISTments 2009-2010 math Skill Builder; public research comparison only",
        "methods": list(METHODS),
        "definitions": {
            "sequence_length": "full held-out sequence length; offline descriptive stratum, not an online feature",
            "prior_attempts": "number of earlier responses available before the predicted attempt",
            "training_concept_attempts": "attempts in training partition for the test sequence's concept only",
            "coverage_bins": ["0", "1-39", "40-199", "200+"],
            "length_bins": ["1-3", "4-10", "11-20", "21+"],
            "constant_auc": "report both fold-local and pooled; pooled constant scores can vary across folds",
            "bootstrap": "paired learner-cluster bootstrap of AUC delta and improvements in log loss, Brier, and 10-bin ECE; positive favors hierarchical BKT",
            "prediction_timing": "predict from mastery before observing the current label; update mastery after the scored response",
            "labels": "correct=1; incorrect=0, matching the corrected Skill Builder first-try target",
            "promotion": "research only; public math parameters do not update the C-language course defaults",
        },
        "development_oof": {"overall": _summarize(oof, seed=public_bkt.SEED),
            "by_full_sequence_length": _bin_report(oof, "sequence_length_bin", seed=public_bkt.SEED + 11),
            "by_prior_attempts": _bin_report(oof, "prior_attempt_bin", seed=public_bkt.SEED + 23),
            "by_training_concept_coverage": _bin_report(oof, "training_concept_attempts_bin", seed=public_bkt.SEED + 37)},
        "locked_test": {"overall": _summarize(final, seed=public_bkt.SEED + 51),
            "by_full_sequence_length": _bin_report(final, "sequence_length_bin", seed=public_bkt.SEED + 61),
            "by_prior_attempts": _bin_report(final, "prior_attempt_bin", seed=public_bkt.SEED + 71),
            "by_training_concept_coverage": _bin_report(final, "training_concept_attempts_bin", seed=public_bkt.SEED + 83)},
        "partition_counts": {"development_learners": len({row.learner_id for row in development}),
            "development_attempts": sum(map(len, (row.outcomes for row in development))),
            "locked_test_learners": len({row.learner_id for row in final_test}),
            "locked_test_attempts": sum(len(row.outcomes) for row in final_test)},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_constructed(output_path: Path, expected_path: Path | None = None) -> dict[str, Any]:
    sequences, source_hash = bkt_calibration.load_sequences()
    folds = bkt_calibration.concept_folds(sequences)
    observations: list[dict[str, Any]] = []
    expected_concepts = {row.concept_id for row in sequences}
    for fold in range(bkt_calibration.FOLD_COUNT):
        train = [row for row in sequences if folds[row.concept_id] != fold]
        validation = [row for row in sequences if folds[row.concept_id] == fold]
        fitted, _, _ = bkt_calibration.fit_parameters(train)
        train_attempts: dict[str, int] = defaultdict(int)
        for row in train:
            train_attempts[row.concept_id] += len(row.outcomes)
        labels = [value for row in train for value in row.outcomes]
        constant = (sum(labels) + 1) / (len(labels) + 2)
        for row in validation:
            prediction_by_method = {
                "original_bkt": [p for _, p in bkt_calibration._sequence_predictions(row, INITIAL_PARAMETERS)],
                "concept_heldout_fit": [p for _, p in bkt_calibration._sequence_predictions(row, fitted)],
                "training_constant": [constant] * len(row.outcomes),
            }
            template = "".join("T" if value else "F" for value in row.outcomes)
            for index, outcome in enumerate(row.outcomes):
                observations.append({"concept_id": row.concept_id, "case_id": row.case_id,
                    "fold": fold + 1, "step": index + 1, "template": template,
                    "sequence_length": len(row.outcomes), "training_concept_attempts": train_attempts.get(row.concept_id, 0),
                    "correct": outcome,
                    "probabilities": {name: values[index] for name, values in prediction_by_method.items()}})
    methods = ("original_bkt", "concept_heldout_fit", "training_constant")
    output: dict[str, Any] = {"schema_version": "deepprof-constructed-bkt-strata-v1",
        "source_sha256": source_hash, "n_sequences": len(sequences),
        "n_attempts": len(observations), "lengths": sorted({len(row.outcomes) for row in sequences}),
        "concepts": len(expected_concepts), "test_concept_training_coverage_bins": ["0"],
        "prediction_timing": "each score is emitted before the current response updates mastery",
        "label_direction": "correct=1; incorrect=0",
        "auc_ties": "Mann-Whitney rank AUC with average ranks for tied scores",
        "folding": "five deterministic concept-held-out folds; no concept appears on both sides",
        "metrics": {}, "by_template": {}, "by_step": {}, "by_training_coverage": {},
        "interpretation": "Constructed three-template data cannot identify sequence-length effects; concept-held-out folds give every held-out concept zero training attempts. This is a diagnostic limitation, not a population estimate."}
    for method in methods:
        output["metrics"][method] = public_bkt._metrics([(r["correct"], r["probabilities"][method]) for r in observations])
    for field, names in (("template", sorted({row["template"] for row in observations})),
                         ("step", sorted({row["step"] for row in observations}))):
        destination = output["by_template"] if field == "template" else output["by_step"]
        for name in names:
            rows = [row for row in observations if row[field] == name]
            destination[str(name)] = {"n": len(rows), "positive": sum(bool(row["correct"]) for row in rows),
                "negative": sum(not bool(row["correct"]) for row in rows),
                "metrics": {method: public_bkt._metrics([(r["correct"], r["probabilities"][method]) for r in rows])
                            for method in methods}}
    for concept in sorted(expected_concepts):
        rows = [row for row in observations if row["concept_id"] == concept]
        sequences_for_concept = [row for row in sequences if row.concept_id == concept]
        template_counts: dict[str, int] = defaultdict(int)
        for sequence in sequences_for_concept:
            template_counts["".join("T" if value else "F" for value in sequence.outcomes)] += 1
        output["by_training_coverage"][concept] = {"training_attempts_in_heldout_fold": 0,
            "total_sequences": len(sequences_for_concept), "template_counts": dict(sorted(template_counts.items())),
            "heldout_sequences": len({row["case_id"] for row in rows}),
            "metrics": {method: public_bkt._metrics([(r["correct"], r["probabilities"][method]) for r in rows])
                        for method in methods}}
    output["exploratory_concept_cluster_auc_delta_ci"] = {
        method: _clustered_auc_delta_ci(observations, method, "training_constant",
            cluster_field="concept_id", seed=public_bkt.SEED + index, samples=1000)
        for index, method in enumerate(("original_bkt", "concept_heldout_fit"))}
    if expected_path and expected_path.is_file():
        frozen = json.loads(expected_path.read_text(encoding="utf-8"))
        output["historical_metric_comparison"] = {
            "source_data_sha256_matches": source_hash == frozen.get("source_data", {}).get("sha256"),
            "stored_fold_count": frozen.get("fold_count"),
            "recomputed_fit_auc": output["metrics"]["concept_heldout_fit"]["auc"],
            "stored_fit_auc": frozen.get("out_of_fold", {}).get("fitted_parameters", {}).get("auc"),
            "recomputed_fit_log_loss": output["metrics"]["concept_heldout_fit"]["log_loss"],
            "stored_fit_log_loss": frozen.get("out_of_fold", {}).get("fitted_parameters", {}).get("log_loss"),
            "stored_constant_auc": frozen.get("out_of_fold", {}).get("constant_baseline", {}).get("auc"),
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="Locally available corrected ASSISTments CSV")
    parser.add_argument("--output", type=Path, help="Public-data diagnostic JSON output")
    parser.add_argument("--constructed-output", type=Path, help="Constructed fixture diagnostic JSON output")
    parser.add_argument("--expected-constructed", type=Path, help="Historical calibration JSON for metric cross-check")
    args = parser.parse_args()
    if bool(args.data) != bool(args.output):
        parser.error("--data and --output must be specified together")
    if not args.data and not args.constructed_output:
        parser.error("supply public data, constructed output, or both")
    if args.data:
        run_public(args.data, args.output)
    if args.constructed_output:
        run_constructed(args.constructed_output, args.expected_constructed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
