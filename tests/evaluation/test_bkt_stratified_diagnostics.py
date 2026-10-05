from __future__ import annotations

from evaluation import bkt_stratified_diagnostics as diagnostics
from evaluation import public_bkt
from models.learner.bkt import INITIAL_PARAMETERS


def test_auc_uses_average_ranks_for_ties_and_single_class_is_na():
    tied = public_bkt._metrics([(True, 0.5), (False, 0.5)])
    assert tied["auc"] == 0.5
    single_class = public_bkt._metrics([(True, 0.4), (True, 0.8)])
    assert single_class["auc"] is None


def test_prediction_is_emitted_before_current_response_updates_mastery():
    baseline = diagnostics.predict_sequence((False, True), INITIAL_PARAMETERS)
    changed_current = diagnostics.predict_sequence((True, True), INITIAL_PARAMETERS)
    assert baseline[0] == changed_current[0]
    assert baseline[1] != changed_current[1]


def test_constructed_report_reproduces_locked_historical_metrics(tmp_path):
    from pathlib import Path

    project = Path(__file__).resolve().parents[2]
    expected = project / "tests" / "fixtures" / "constructed-bkt-historical-metrics.json"
    output = tmp_path / "constructed.json"
    report = diagnostics.run_constructed(output, expected)
    assert report["n_sequences"] == 40
    assert report["n_attempts"] == 120
    assert report["lengths"] == [3]
    assert report["historical_metric_comparison"]["source_data_sha256_matches"] is True
    assert report["historical_metric_comparison"]["recomputed_fit_auc"] == \
        report["historical_metric_comparison"]["stored_fit_auc"]
    assert all(row["training_attempts_in_heldout_fold"] == 0
        for row in report["by_training_coverage"].values())


def test_citation_claim_extraction_links_trailing_reference_line():
    from evaluation.rag_citation_audit import _claims

    rows = _claims("第一条事实说明线性表具有元素之间的线性关系。\n"
                   "第二条事实说明顺序表采用连续存储位置。\n"
                   "→ 对应片段2。\n\n这个问题应该如何理解？")
    assert len(rows) == 2
    assert rows[0]["citation_selectors"] == [2]
    assert rows[1]["citation_selectors"] == [2]


def test_citation_claim_extraction_skips_headings_and_review_prompts():
    from evaluation.rag_citation_audit import _claims

    rows = _claims("第1步：先抓住关键条件\n"
                   "线性表采用顺序存储时称为顺序表。\n"
                   "请至少用两个点来说明。\n"
                   "→ 对应片段1。")
    assert [row["text"] for row in rows] == ["线性表采用顺序存储时称为顺序表。"]
    assert rows[0]["citation_selectors"] == [1]


def test_paired_metric_intervals_use_learner_clusters_and_treat_ties_as_valid():
    observations = [
        {"learner_id": "a", "correct": True,
         "probabilities": {"model": 0.8, "baseline": 0.5}},
        {"learner_id": "a", "correct": False,
         "probabilities": {"model": 0.2, "baseline": 0.5}},
        {"learner_id": "b", "correct": True,
         "probabilities": {"model": 0.7, "baseline": 0.5}},
        {"learner_id": "b", "correct": False,
         "probabilities": {"model": 0.3, "baseline": 0.5}},
    ]
    loss = diagnostics._clustered_mean_score_ci(observations, "model", "baseline", "brier",
                                                seed=1, samples=40)
    ece = diagnostics._clustered_ece_ci(observations, "model", "baseline", seed=1, samples=40)
    auc = diagnostics._clustered_auc_delta_ci(observations, "model", "baseline",
        cluster_field="learner_id", seed=1, samples=40)
    assert loss["learners"] == ece["learners"] == auc["clusters"] == 2
    assert loss["lower_95"] is not None
    assert ece["lower_95"] is not None
    assert auc["median"] == 0.5


def test_nested_monotone_logistic_calibration_keeps_score_order():
    from evaluation.bkt_nested_selection import apply_monotone_logistic, fit_monotone_logistic

    labels = [False, True, False, True, True, False]
    probabilities = [0.12, 0.82, 0.31, 0.69, 0.91, 0.42]
    calibrator = fit_monotone_logistic(labels, probabilities)
    assert calibrator["slope"] >= 0.0
    calibrated = [apply_monotone_logistic(value, calibrator) for value in probabilities]
    assert calibrated[1] >= calibrated[0]
    assert calibrated[4] >= calibrated[3]


def test_nested_interval_report_pairs_item_scores_with_concept_em():
    from evaluation.bkt_nested_comparison_intervals import PAIRED_COMPARISONS

    assert PAIRED_COMPARISONS["item_difficulty_vs_concept_em"] == ("item_difficulty", "concept_em")
    assert PAIRED_COMPARISONS["selected_calibrated_vs_historical_shrinkage_200"] == (
        "nested_selected_calibrated", "historical_shrinkage_200")
