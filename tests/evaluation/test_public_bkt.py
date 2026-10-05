from __future__ import annotations

import csv
import hashlib

from evaluation import public_bkt
from evaluation.public_bkt import (FOLDS, AttemptSequence, _constant_probabilities, _metrics,
                                   development_fold, load_assistments, split_outer)


def _write_dataset(path, rows):
    columns = ["order_id", "user_id", "problem_id", "correct", "list_skill_ids", "answer_type", "hint_count"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_public_loader_filters_ambiguous_open_and_missing_and_only_deduplicates_duplicate_logs(tmp_path):
    path = tmp_path / "assistments.csv"
    _write_dataset(path, [
        {"order_id": 1, "user_id": "u1", "problem_id": "p1", "correct": 1, "list_skill_ids": "k1", "answer_type": "choose_1", "hint_count": 0},
        {"order_id": 1, "user_id": "u1", "problem_id": "p1", "correct": 1, "list_skill_ids": "k1", "answer_type": "choose_1", "hint_count": 0},
        {"order_id": 2, "user_id": "u1", "problem_id": "p1", "correct": 0, "list_skill_ids": "k1", "answer_type": "fill_in", "hint_count": 1},
        {"order_id": 3, "user_id": "u1", "problem_id": "p3", "correct": 1, "list_skill_ids": "k1_k2", "answer_type": "choose_1", "hint_count": "bad"},
        {"order_id": 4, "user_id": "u1", "problem_id": "p4", "correct": 1, "list_skill_ids": "k1", "answer_type": "open_response", "hint_count": 0},
        {"order_id": 5, "user_id": "u1", "problem_id": "p5", "correct": "", "list_skill_ids": "k1", "answer_type": "choose_1", "hint_count": 0},
        {"order_id": 6, "user_id": "u1", "problem_id": "p6", "correct": 1, "list_skill_ids": "NA", "answer_type": "choose_1", "hint_count": 0},
    ])

    dataset = load_assistments(path)

    assert dataset.rows_seen == 7
    assert dataset.rows_used == 2
    assert dataset.rows_deduplicated == 1
    assert dataset.rows_dropped == {"missing_or_ambiguous_skill": 2, "missing_or_invalid_label": 1, "open_response": 1}
    assert dataset.rows_with_tutoring == 1
    assert dataset.rows_correct_with_tutoring == 0
    assert dataset.rows_missing_hint_metadata == 1
    assert dataset.sequences[0].outcomes == (True, False)


def test_public_split_is_reproducible_and_never_splits_a_learner(tmp_path):
    path = tmp_path / "assistments.csv"
    _write_dataset(path, [{"order_id": i, "user_id": f"u{i}", "problem_id": f"p{i}",
                           "correct": i % 2, "list_skill_ids": "k1", "answer_type": "choose_1"}
                          for i in range(1, 101)])
    dataset = load_assistments(path)

    development, test = split_outer(dataset.sequences)

    assert set(row.learner_id for row in development).isdisjoint(row.learner_id for row in test)
    assert (len(development), len(test)) == (80, 20)
    fold_assignments = [development_fold(row.learner_id) for row in development]
    assert fold_assignments == [development_fold(row.learner_id) for row in development]
    assert set(fold_assignments) == set(range(FOLDS))


def test_auc_uses_rank_ties_and_probability_metrics_are_finite():
    result = _metrics([(True, 0.8), (False, 0.8), (True, 0.7), (False, 0.2)])

    assert result["auc"] == 0.625
    assert 0 <= result["brier"] <= 1
    assert result["log_loss"] > 0
    assert 0 <= result["ece_10_bins"] <= 1


def test_concept_laplace_baseline_falls_back_to_global_for_low_evidence():
    train = [AttemptSequence("high", "eligible", tuple([True] * 20 + [False] * 20), tuple(range(40))),
             AttemptSequence("low", "rare", (True, True), (1, 2))]

    global_probability, by_concept = _constant_probabilities(train)

    assert set(by_concept) == {"eligible"}
    assert by_concept.get("rare", global_probability) == global_probability


def test_default_evaluation_mode_reports_development_only(monkeypatch, tmp_path):
    path = tmp_path / "assistments.csv"
    _write_dataset(path, [{"order_id": attempt, "user_id": f"u{learner}", "problem_id": f"p{learner}-{attempt}",
                           "correct": (learner + attempt) % 2, "list_skill_ids": f"k{learner % 6}",
                           "answer_type": "choose_1"}
                          for learner in range(120) for attempt in range(1, 4)])

    def development_score(_train, validation, fitted=None):
        return {"bkt": {"auc": 0.61, "log_loss": 0.6, "brier": 0.2, "ece_10_bins": 0.02},
                "original": {"log_loss": 0.7}, "global_constant": {"log_loss": 0.68, "brier": 0.23},
                "concept_constant": {"log_loss": 0.67, "brier": 0.22}, "log_loss_ratio": 0.9,
                "parameter_summary": {},
                "_learner_losses": {row.learner_id: ([0.5] * len(row.outcomes),
                    [0.6] * len(row.outcomes), [0.7] * len(row.outcomes)) for row in validation},
                "_oof_rows": {name: [(outcome, probability) for row in validation for outcome in row.outcomes
                    for probability in [score]] for name, score in {
                        "global_em": 0.6, "concept_em": 0.61, "hierarchical_shrinkage": 0.62,
                        "original": 0.5, "global_constant": 0.6, "concept_constant": 0.61}.items()}}

    monkeypatch.setattr(public_bkt, "_score_fold", development_score)
    monkeypatch.setattr(public_bkt, "_paired_bootstrap", lambda *_args, **_kwargs: {
        "lower_95": 0.1, "median": 0.2, "upper_95": 0.3})

    result = public_bkt.evaluate(path)

    assert result["evaluation_mode"] == "tune"
    assert "final_test" not in result
    assert "candidate_global_parameters" not in result
    assert result["research_eligible"] is False
    assert result["curriculum_promotion_eligible"] is False
    _, locked_test = split_outer(load_assistments(path).sequences)
    membership = sorted(hashlib.sha256(row.learner_id.encode("utf-8")).hexdigest() for row in locked_test)
    expected_split_hash = hashlib.sha256(("20260927|" + "\n".join(membership)).encode("ascii")).hexdigest()
    assert result["split_sha256"] == expected_split_hash
