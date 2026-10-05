from __future__ import annotations

import random
import pytest

from evaluation.bkt_fitting import _em, _forward_transition, fit_hierarchical, predict_sequence
from evaluation.public_bkt import AttemptSequence
from models.learner.bkt import BKTParameters, update


def test_hierarchical_fit_is_deterministic_and_falls_back_for_small_concepts():
    randomizer = random.Random(551)
    rows = []
    for learner in range(80):
        mastery = 0.25
        outcomes = []
        for index in range(15):
            correct = randomizer.random() < (0.8 if mastery else 0.25)
            outcomes.append(correct)
            if not mastery and randomizer.random() < 0.12:
                mastery = 1.0
        rows.append(AttemptSequence(f"l{learner}", "k1", tuple(outcomes), tuple(range(15))))
    rows.append(AttemptSequence("rare", "k-rare", (True, False), (1, 2)))

    first = fit_hierarchical(rows, min_concept_attempts=40)
    second = fit_hierarchical(rows, min_concept_attempts=40)

    assert first == second
    assert "k1" in first.by_concept
    assert "k-rare" not in first.by_concept
    assert set(first.summary()["parameter_boundaries"]) == {"p_l0", "p_t", "p_g", "p_s"}
    for parameters in (first.global_parameters, first.by_concept["k1"]):
        assert all(0 <= getattr(parameters, field) <= 1 for field in ("p_l0", "p_t", "p_g", "p_s"))
        assert parameters.p_g + parameters.p_s < 1


def test_bkt_prediction_for_current_attempt_precedes_learning_transition():
    parameters = BKTParameters(p_l0=0.2, p_t=0.1, p_g=0.2, p_s=0.1)
    predictions = predict_sequence((True, False), parameters)

    assert predictions[0] == pytest.approx(0.34)
    assert predictions[1] > predictions[0]


@pytest.mark.parametrize("correct", [True, False])
def test_em_forward_transition_matches_product_one_way_learning_semantics(correct):
    parameters = BKTParameters(p_l0=0.2, p_t=0.1, p_g=0.2, p_s=0.1)
    predicted_correct, mastery_after = update(parameters.p_l0, correct, parameters)
    posterior_mastered = (parameters.p_l0 * (1 - parameters.p_s) / predicted_correct if correct else
                          parameters.p_l0 * parameters.p_s / (1 - predicted_correct))

    mastered, unmastered = _forward_transition(posterior_mastered, 1 - posterior_mastered, parameters.p_t)

    assert mastered == pytest.approx(mastery_after)
    assert unmastered == pytest.approx(1 - mastery_after)


def test_em_uses_product_mastery_prior_guess_and_slip_semantics():
    initial = BKTParameters(p_l0=0.8, p_t=0.1, p_g=0.1, p_s=0.2)
    sequences = [(True,)] * 660 + [(False,)] * 340

    fitted, iterations = _em(sequences, initial, max_iterations=1)

    assert iterations == 1
    assert fitted.p_l0 == pytest.approx(0.8)
    assert fitted.p_g == pytest.approx(0.1)
    assert fitted.p_s == pytest.approx(0.2)
