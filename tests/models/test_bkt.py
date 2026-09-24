from __future__ import annotations

import pytest

from models.learner.bkt import DEFAULT_PARAMETERS, binary_entropy, predict, update


def test_standard_four_parameter_bkt_uses_observation_then_learning_transition():
    mastery = DEFAULT_PARAMETERS.p_l0
    predicted, mastery = update(mastery, True)
    assert predicted == pytest.approx(0.34)
    assert mastery == pytest.approx((0.2 * 0.9 / 0.34) + (1 - (0.2 * 0.9 / 0.34)) * 0.1)

    before_wrong = mastery
    predicted, mastery = update(before_wrong, False)
    posterior = before_wrong * 0.1 / (1 - predict(before_wrong))
    assert predicted == pytest.approx(predict(before_wrong))
    assert mastery == pytest.approx(posterior + (1 - posterior) * 0.1)


def test_bkt_boundaries_replay_and_entropy_semantics():
    assert predict(0.0) == pytest.approx(0.2)
    assert predict(1.0) == pytest.approx(0.9)
    assert update(0.0, True)[1] == pytest.approx(0.1)
    assert update(1.0, False)[1] == pytest.approx(1.0)
    assert binary_entropy(0.0) == 0.0
    assert binary_entropy(0.5) == pytest.approx(1.0)

    sequence = [True, False, True, True, False]
    first = DEFAULT_PARAMETERS.p_l0
    second = DEFAULT_PARAMETERS.p_l0
    for correct in sequence:
        _, first = update(first, correct)
    for correct in sequence:
        _, second = update(second, correct)
    assert first == pytest.approx(second)
    assert DEFAULT_PARAMETERS.to_dict() == {
        "p_l0": 0.2, "p_t": 0.1, "p_g": 0.2, "p_s": 0.1,
        "source": "development_initial_values; not fitted; teacher review pending",
        "model_version": "bkt-four-parameter-dev-v1", "fitted": False, "teacher_review": "pending",
    }


@pytest.mark.parametrize("field,value", [("p_l0", -0.1), ("p_t", 1.1), ("p_g", 1.0), ("p_s", 1.0)])
def test_bkt_rejects_invalid_parameters(field, value):
    parameters = DEFAULT_PARAMETERS.to_dict()
    parameters[field] = value
    with pytest.raises(ValueError):
        type(DEFAULT_PARAMETERS)(**{key: parameters[key] for key in ("p_l0", "p_t", "p_g", "p_s")})
