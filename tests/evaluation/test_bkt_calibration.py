from evaluation.bkt_calibration import (
    Sequence, _curriculum_promotion_eligible, concept_folds, cross_validate, fit_parameters, load_sequences,
)
from models.learner.bkt import INITIAL_PARAMETERS, parameters_from_snapshot


def test_concept_folds_are_deterministic_and_keep_each_concept_together():
    rows = [Sequence(f"case-{index}", concept, (True, False, True))
            for index, concept in enumerate(("c1", "c1", "c2", "c3", "c4", "c5", "c6"))]

    first = concept_folds(rows)
    second = concept_folds(rows)

    assert first == second
    assert len(set(first.values())) == 5
    for concept, fold in first.items():
        assert all(first[row.concept_id] == fold for row in rows if row.concept_id == concept)


def test_calibration_preserves_legacy_snapshot_hash_and_new_provenance():
    snapshot = {**INITIAL_PARAMETERS.to_dict(), "calibration": {"sha256": "safe-metadata"}}

    restored = parameters_from_snapshot(snapshot)

    assert restored == INITIAL_PARAMETERS
    assert INITIAL_PARAMETERS.config_hash == "51340c493ab91337ea65b92535dc82237a6e1209ad23a985506e5090f4e8126f"


def test_constructed_or_public_data_cannot_promote_course_parameters_without_teacher_review():
    assert not _curriculum_promotion_eligible("constructed_developer_fixture", "pending")
    assert not _curriculum_promotion_eligible("assistments_public_math", "approved")
    assert not _curriculum_promotion_eligible("approved_course_attempts", "pending")
    assert _curriculum_promotion_eligible("approved_course_attempts", "approved")


def test_fit_parameters_uses_only_valid_bkt_probabilities():
    rows, _ = load_sequences()

    fitted, loss, detail = fit_parameters(rows[:5])

    assert loss > 0
    assert 0.05 <= fitted.p_l0 <= 0.95
    assert 0.0 <= fitted.p_t <= 0.30
    assert 0.05 <= fitted.p_g <= 0.45
    assert 0.05 <= fitted.p_s <= 0.45
    assert fitted.fitted is True
    assert fitted.teacher_review == "pending"
    assert set(detail["boundary_solution"]) == {"p_l0", "p_t", "p_g", "p_s"}


def test_cross_validation_exports_each_attempt_once_and_never_splits_concepts():
    rows, _ = load_sequences()
    result = cross_validate(rows)

    assert result["case_count"] == 40
    assert result["attempt_count"] == 120
    assert len(result["out_of_fold_predictions"]) == 120
    for prediction in result["out_of_fold_predictions"]:
        assert result["concept_to_fold"][prediction["concept_id"]] + 1 == prediction["fold"]
        assert 0.0 <= prediction["fitted_probability"] <= 1.0
