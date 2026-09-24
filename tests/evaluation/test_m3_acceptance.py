import json

from evaluation.m3_acceptance import _bkt_metrics, _ratio, _validate_resume_config, audit_scores


def test_zero_denominators_and_single_class_auc_are_explicitly_not_computable():
    assert _ratio(0, 0) == {
        "numerator": 0, "denominator": 0, "value": None, "status": "not_computable"
    }

    metrics = _bkt_metrics([{"bkt_observations": [
        {"eligible": True, "correct": True, "predicted_correct": 0.7},
        {"eligible": True, "correct": True, "predicted_correct": 0.8},
    ]}])
    assert metrics["n"] == 2
    assert metrics["positive_count"] == 2
    assert metrics["negative_count"] == 0
    assert metrics["auc"] == {"value": None, "status": "not_computable_single_class"}


def test_empty_and_missing_blind_scores_remain_pending(tmp_path):
    scoring = tmp_path / "scoring"
    scoring.mkdir()
    template = {"schema_version": "m3-blind-rating-v1", "group_labels_hidden": True, "items": [
        {"blind_id": "one", "action_appropriateness": None, "answer_leakage": None,
         "citation_support": None, "uncertainty_handling": None},
        {"blind_id": "two", "action_appropriateness": 4, "answer_leakage": None,
         "citation_support": None, "uncertainty_handling": None},
    ]}
    (scoring / "rater-01.json").write_text(json.dumps(template), encoding="utf-8")
    second = json.loads(json.dumps(template))
    second["items"][1]["action_appropriateness"] = 5
    (scoring / "rater-02.json").write_text(json.dumps(second), encoding="utf-8")

    result = audit_scores(tmp_path)
    assert result["fields"]["action_appropriateness"]["n"] == 1
    assert result["fields"]["action_appropriateness"]["raw_agreement"] == 0.0
    assert result["fields"]["action_appropriateness"]["cohen_kappa"] == 0.0
    assert result["fields"]["answer_leakage"]["status"] == "pending_human_review"
    assert result["fields"]["answer_leakage"]["n"] == 0
    assert (tmp_path / "rater-agreement.json").exists()


def test_resume_rejects_configuration_drift():
    frozen = {"case_version": "v1", "retrieval": {"top_k": 5}, "source_fingerprint": {"hash": "abc"}}
    _validate_resume_config(frozen, dict(frozen))

    changed = {**frozen, "retrieval": {"top_k": 4}}
    try:
        _validate_resume_config(frozen, changed)
    except ValueError as exc:
        assert str(exc) == "resume_config_mismatch:retrieval"
    else:
        raise AssertionError("resume must reject retrieval configuration drift")


def test_score_import_rejects_invalid_rating_values(tmp_path):
    scoring = tmp_path / "scoring"
    scoring.mkdir()
    template = {"schema_version": "m3-blind-rating-v1", "group_labels_hidden": True, "items": [
        {"blind_id": "one", "action_appropriateness": 6, "answer_leakage": None,
         "citation_support": None, "uncertainty_handling": None}
    ]}
    (scoring / "rater-01.json").write_text(json.dumps(template), encoding="utf-8")
    valid = json.loads(json.dumps(template))
    valid["items"][0]["action_appropriateness"] = 4
    (scoring / "rater-02.json").write_text(json.dumps(valid), encoding="utf-8")

    try:
        audit_scores(tmp_path)
    except ValueError as exc:
        assert str(exc) == "invalid_rating:rater-01:action_appropriateness:one"
    else:
        raise AssertionError("out-of-range human ratings must be rejected")
