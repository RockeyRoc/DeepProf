from evaluation.bkt_fitting import BKTFit, predict_sequence
from evaluation.bkt_research_adapter import (fit_item_guess_slip, predict_with_forgetting,
                                             predict_with_item_guess_slip)
from evaluation.public_bkt import AttemptSequence
from models.learner.bkt import INITIAL_PARAMETERS


def test_opportunity_forgetting_zero_matches_baseline_and_positive_changes_later_scores():
    outcomes = (True, True, False)
    assert predict_with_forgetting(outcomes, INITIAL_PARAMETERS, forget_probability=0.0) == \
        predict_sequence(outcomes, INITIAL_PARAMETERS)
    forgotten = predict_with_forgetting(outcomes, INITIAL_PARAMETERS, forget_probability=0.2)
    assert forgotten[0] == predict_sequence(outcomes, INITIAL_PARAMETERS)[0]
    assert forgotten[2] != predict_sequence(outcomes, INITIAL_PARAMETERS)[2]


def test_item_emissions_only_fit_sufficiently_observed_items_and_keep_concept_backoff():
    rows = [AttemptSequence("learner-a", "concept-a", (True, False), (1, 2), ("common", "rare")),
            AttemptSequence("learner-b", "concept-a", (True, True), (1, 2), ("common", "common")),
            AttemptSequence("learner-c", "concept-a", (False, True), (1, 2), ("common", "common"))]
    base = BKTFit(INITIAL_PARAMETERS, {"concept-a": INITIAL_PARAMETERS},
                  {"concept-a": INITIAL_PARAMETERS}, {"concept-a": 6}, 1)
    fitted = fit_item_guess_slip(rows, base, min_item_attempts=3, prior_strength=2.0)
    assert ("concept-a", "common") in fitted.item_emissions
    assert ("concept-a", "rare") not in fitted.item_emissions
    sparse_fit = fit_item_guess_slip(rows, base, min_item_attempts=6, prior_strength=2.0)
    assert sparse_fit.item_emissions == {}
    assert fitted.item_attempt_counts[("concept-a", "common")] == 5
    prediction = predict_with_item_guess_slip(
        AttemptSequence("learner-test", "concept-a", (True,), (1,), ("not-seen",)), fitted)
    assert prediction == predict_sequence((True,), INITIAL_PARAMETERS)
