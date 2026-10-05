"""Research-only BKT variants; never changes the course's deployed defaults."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Iterable

from evaluation.bkt_fitting import BKTFit
from evaluation.public_bkt import AttemptSequence
from models.learner.bkt import BKTParameters, INITIAL_PARAMETERS, update


@dataclass(frozen=True, slots=True)
class ItemDifficultyFit:
    """Per-problem guess/slip emissions with concept-model backoff."""

    global_parameters: BKTParameters
    concept_parameters: dict[str, BKTParameters]
    item_emissions: dict[tuple[str, str], tuple[float, float]]
    item_attempt_counts: dict[tuple[str, str], int]
    min_item_attempts: int
    prior_strength: float
    iterations: int
    objective_history: dict[str, tuple[float, ...]]
    parameter_delta_history: dict[str, tuple[float, ...]]
    converged_by_concept: dict[str, bool]
    boundary_parameter_count: int


def _base_parameters(concept_id: str, fit: BKTFit | None,
                     global_parameters: BKTParameters) -> BKTParameters:
    if fit is None:
        return global_parameters
    return fit.concept_parameters.get(concept_id, fit.global_parameters)


def predict_with_forgetting(outcomes: Iterable[bool], parameters: BKTParameters,
                            *, forget_probability: float) -> list[float]:
    """Predict online with opportunity-based forgetting after each scored item.

    This intentionally has no elapsed-time input. Forgetting is applied after
    the response's posterior and learning transition, before the next attempt.
    """
    if not 0.0 <= float(forget_probability) <= 1.0:
        raise ValueError("forget_probability_must_be_in_0_1")
    mastery = parameters.p_l0
    probabilities: list[float] = []
    for outcome in outcomes:
        probability, learned_mastery = update(mastery, bool(outcome), parameters)
        probabilities.append(probability)
        mastery = learned_mastery * (1.0 - float(forget_probability))
    return probabilities


def _emission(probability: float, correct: bool) -> float:
    return probability if correct else 1.0 - probability


def _posterior_item_counts(rows: list[AttemptSequence], base: BKTParameters,
                           item_emissions: dict[str, tuple[float, float]]) -> dict[str, list[float]]:
    expected: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for row in rows:
        if not row.outcomes:
            continue
        if len(row.problem_ids) != len(row.outcomes):
            raise ValueError("problem_ids_must_align_with_attempts")
        emissions: list[tuple[float, float]] = []
        for item, correct in zip(row.problem_ids, row.outcomes):
            guess, slip = item_emissions.get(item, (base.p_g, base.p_s))
            emissions.append((_emission(1.0 - slip, correct), _emission(guess, correct)))

        alpha: list[list[float]] = []
        initial = [base.p_l0 * emissions[0][0], (1.0 - base.p_l0) * emissions[0][1]]
        scale = max(1e-300, sum(initial))
        alpha.append([value / scale for value in initial])
        for index in range(1, len(row.outcomes)):
            mastered, unmastered = alpha[-1]
            predicted = (mastered + unmastered * base.p_t, unmastered * (1.0 - base.p_t))
            current = [predicted[0] * emissions[index][0], predicted[1] * emissions[index][1]]
            scale = max(1e-300, sum(current))
            alpha.append([value / scale for value in current])

        beta = [[0.0, 0.0] for _ in row.outcomes]
        beta[-1] = [1.0, 1.0]
        for index in range(len(row.outcomes) - 2, -1, -1):
            next_mastered, next_unmastered = emissions[index + 1]
            beta[index] = [
                next_mastered * beta[index + 1][0],
                base.p_t * next_mastered * beta[index + 1][0] +
                (1.0 - base.p_t) * next_unmastered * beta[index + 1][1],
            ]
            normalization = max(1e-300, sum(alpha[index][state] * beta[index][state] for state in (0, 1)))
            beta[index] = [value / normalization for value in beta[index]]

        for index, (item, correct) in enumerate(zip(row.problem_ids, row.outcomes)):
            normalization = max(1e-300, sum(alpha[index][state] * beta[index][state] for state in (0, 1)))
            unmastered = alpha[index][1] * beta[index][1] / normalization
            mastered = 1.0 - unmastered
            record = expected[item]
            record[0] += unmastered
            record[1] += unmastered * int(bool(correct))
            record[2] += mastered
            record[3] += mastered * int(not bool(correct))
    return expected


def _item_log_likelihood(rows: list[AttemptSequence], base: BKTParameters,
                         item_emissions: dict[str, tuple[float, float]]) -> float:
    total = 0.0
    for row in rows:
        if not row.outcomes:
            continue
        state_mastered = float(base.p_l0)
        state_unmastered = 1.0 - state_mastered
        for index, (item, correct) in enumerate(zip(row.problem_ids, row.outcomes)):
            guess, slip = item_emissions.get(item, (base.p_g, base.p_s))
            mastered_emission = _emission(1.0 - slip, bool(correct))
            unmastered_emission = _emission(guess, bool(correct))
            if index:
                state_mastered, state_unmastered = (
                    state_mastered + state_unmastered * base.p_t,
                    state_unmastered * (1.0 - base.p_t),
                )
            state_mastered *= mastered_emission
            state_unmastered *= unmastered_emission
            scale = max(1e-300, state_mastered + state_unmastered)
            total += math.log(scale)
            state_mastered /= scale
            state_unmastered /= scale
    return total


def fit_item_guess_slip(rows: Iterable[AttemptSequence], base_fit: BKTFit, *,
                        min_item_attempts: int = 20, prior_strength: float = 50.0,
                        max_iterations: int = 100, tolerance: float = 1e-5,
                        consecutive_tolerance_rounds: int = 3) -> ItemDifficultyFit:
    """Fit item emissions using training-fold sequences and concept BKT backoff.

    Each eligible item's guess/slip is an EM emission estimate regularized
    toward its concept model. Items below the threshold never get a local
    estimate, so prediction falls back to the concept parameters.
    """
    if (min_item_attempts < 1 or prior_strength < 0 or max_iterations < 1
            or tolerance <= 0 or consecutive_tolerance_rounds < 1):
        raise ValueError("invalid_item_difficulty_fit_configuration")
    grouped: dict[str, list[AttemptSequence]] = defaultdict(list)
    item_counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if len(row.problem_ids) != len(row.outcomes):
            raise ValueError("problem_ids_must_align_with_attempts")
        if row.outcomes:
            grouped[row.concept_id].append(row)
            item_counts.update((row.concept_id, item) for item in row.problem_ids)

    item_emissions: dict[tuple[str, str], tuple[float, float]] = {}
    iterations_used = 0
    objective_history: dict[str, tuple[float, ...]] = {}
    delta_history: dict[str, tuple[float, ...]] = {}
    converged_by_concept: dict[str, bool] = {}
    for concept_id, concept_rows in sorted(grouped.items()):
        base = _base_parameters(concept_id, base_fit, base_fit.global_parameters)
        eligible = {item for (concept, item), count in item_counts.items()
                    if concept == concept_id and count >= min_item_attempts}
        local = {item: (base.p_g, base.p_s) for item in eligible}
        if not local:
            converged_by_concept[concept_id] = True
            objective_history[concept_id] = ()
            delta_history[concept_id] = ()
            continue
        objectives: list[float] = [_item_log_likelihood(concept_rows, base, local)]
        deltas: list[float] = []
        stable_rounds = 0
        converged = False
        for iteration in range(1, max_iterations + 1):
            expected = _posterior_item_counts(concept_rows, base, local)
            updated: dict[str, tuple[float, float]] = {}
            for item in sorted(eligible):
                unmastered, correct_unmastered, mastered, wrong_mastered = expected.get(
                    item, [0.0, 0.0, 0.0, 0.0])
                guess = ((correct_unmastered + prior_strength * base.p_g) /
                         (unmastered + prior_strength) if unmastered + prior_strength else base.p_g)
                slip = ((wrong_mastered + prior_strength * base.p_s) /
                        (mastered + prior_strength) if mastered + prior_strength else base.p_s)
                updated[item] = (min(0.5, max(0.0, guess)), min(0.5, max(0.0, slip)))
            delta = max((abs(updated[item][axis] - local[item][axis])
                         for item in eligible for axis in (0, 1)), default=0.0)
            local = updated
            iterations_used = max(iterations_used, iteration)
            deltas.append(delta)
            objectives.append(_item_log_likelihood(concept_rows, base, local))
            stable_rounds = stable_rounds + 1 if delta < tolerance else 0
            if stable_rounds >= consecutive_tolerance_rounds:
                converged = True
                break
        item_emissions.update({(concept_id, item): value for item, value in local.items()})
        objective_history[concept_id] = tuple(objectives)
        delta_history[concept_id] = tuple(deltas)
        converged_by_concept[concept_id] = converged
    boundaries = sum(value <= 0.0 or value >= 0.5
                     for guess, slip in item_emissions.values() for value in (guess, slip))
    return ItemDifficultyFit(base_fit.global_parameters, dict(base_fit.concept_parameters), item_emissions,
        dict(item_counts), min_item_attempts, float(prior_strength), iterations_used,
        objective_history, delta_history, converged_by_concept, boundaries)


def predict_with_item_guess_slip(row: AttemptSequence, fit: ItemDifficultyFit) -> list[float]:
    if len(row.problem_ids) != len(row.outcomes):
        raise ValueError("problem_ids_must_align_with_attempts")
    base = fit.concept_parameters.get(row.concept_id, fit.global_parameters)
    mastery = base.p_l0
    probabilities: list[float] = []
    for item, correct in zip(row.problem_ids, row.outcomes):
        guess, slip = fit.item_emissions.get((row.concept_id, item), (base.p_g, base.p_s))
        probability = mastery * (1.0 - slip) + (1.0 - mastery) * guess
        probabilities.append(probability)
        if correct:
            denominator = probability
            posterior = mastery * (1.0 - slip) / denominator if denominator else mastery
        else:
            denominator = 1.0 - probability
            posterior = mastery * slip / denominator if denominator else mastery
        mastery = posterior + (1.0 - posterior) * base.p_t
    return probabilities


__all__ = ["ItemDifficultyFit", "fit_item_guess_slip", "predict_with_forgetting",
           "predict_with_item_guess_slip"]
