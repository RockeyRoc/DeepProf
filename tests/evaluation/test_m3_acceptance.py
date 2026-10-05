import json

from evaluation.m3_acceptance import (_bkt_metrics, _ratio, _score_templates,
    _validate_resume_config, audit_scores)


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
         "accuracy": None, "clarity": None, "coherence": None, "engagement": None,
         "naturalness": None, "personalization_relevance": None,
         "citation_support": None, "uncertainty_handling": None},
        {"blind_id": "two", "action_appropriateness": 4, "answer_leakage": None,
         "accuracy": None, "clarity": None, "coherence": None, "engagement": None,
         "naturalness": None, "personalization_relevance": None,
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
    for field in ("accuracy", "clarity", "coherence", "engagement", "naturalness",
                  "personalization_relevance"):
        assert result["fields"][field]["status"] == "pending_human_review"
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
        {"blind_id": "one", "action_appropriateness": 6, "accuracy": None, "clarity": None,
         "coherence": None, "engagement": None, "naturalness": None,
         "personalization_relevance": None, "answer_leakage": None,
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


def test_score_import_rejects_exposed_case_or_group_identifiers(tmp_path):
    scoring = tmp_path / "scoring"
    scoring.mkdir()
    template = {"schema_version": "m3-blind-rating-v1", "group_labels_hidden": True, "items": [
        {"blind_id": "one", "case_id": "DSDEV-001", "action_appropriateness": None,
         "accuracy": None, "clarity": None, "coherence": None, "engagement": None,
         "naturalness": None, "personalization_relevance": None,
         "answer_leakage": None, "citation_support": None, "uncertainty_handling": None}
    ]}
    (scoring / "rater-01.json").write_text(json.dumps(template), encoding="utf-8")
    (scoring / "rater-02.json").write_text(json.dumps({**template, "items": [
        {key: value for key, value in template["items"][0].items() if key != "case_id"}
    ]}), encoding="utf-8")

    try:
        audit_scores(tmp_path)
    except ValueError as exc:
        assert str(exc) == "blind_identifiers_exposed:rater-01"
    else:
        raise AssertionError("case identifiers must not be exposed to human raters")


def test_real_provider_rating_packets_hide_matrix_labels_and_survive_resume(tmp_path):
    cells = [{"phase": "main", "case_id": "DSDEV-001", "group": "C",
        "rating_context": {"learner_message": "请解释这个概念", "learning_goal": "理解算法"},
        "response_text": "回答文本"}]
    paths = _score_templates(tmp_path, cells)
    first, second = [json.loads(open(path, encoding="utf-8").read()) for path in paths]
    assert first["items"][0]["blind_id"] == second["items"][0]["blind_id"]
    for packet in (first, second):
        item = packet["items"][0]
        assert "case_id" not in item and "group" not in item
        assert item["context"]["learner_message"] == "请解释这个概念"
        for field in ("accuracy", "clarity", "coherence", "engagement", "naturalness",
                      "personalization_relevance"):
            assert item[field] is None
    first["items"][0]["accuracy"] = 5
    open(paths[0], "w", encoding="utf-8").write(json.dumps(first, ensure_ascii=False))
    _score_templates(tmp_path, cells)
    assert json.loads(open(paths[0], encoding="utf-8").read())["items"][0]["accuracy"] == 5
