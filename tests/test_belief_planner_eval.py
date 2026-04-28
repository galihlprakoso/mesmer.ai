from __future__ import annotations

import pytest

from mesmer.bench.belief_eval import (
    aggregate_belief_planner_metrics,
    evaluate_belief_planner,
)
from mesmer.core.belief_graph import (
    AttemptCreateDelta,
    BeliefGraph,
    FrontierCreateDelta,
    FrontierRankDelta,
    HypothesisCreateDelta,
    make_attempt,
    make_frontier,
    make_hypothesis,
)
from mesmer.core.constants import AttemptOutcome


def test_evaluate_belief_planner_reports_binding_regret_and_duplicates() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    chosen = make_frontier(
        hypothesis_id=h.id,
        module="format-shift",
        instruction="probe yaml",
        expected_signal="format follow",
    )
    better = make_frontier(
        hypothesis_id=h.id,
        module="delimiter-injection",
        instruction="probe delimiter",
        expected_signal="delimiter follow",
    )
    g.apply(FrontierCreateDelta(experiment=chosen))
    g.apply(FrontierCreateDelta(experiment=better))
    g.apply(
        FrontierRankDelta(
            rankings={
                chosen.id: {
                    "utility": 0.40,
                    "expected_progress": 0.5,
                    "information_gain": 0.7,
                    "hypothesis_confidence": 0.6,
                    "novelty": 0.9,
                    "strategy_prior": 0.1,
                    "transfer_value": 0.2,
                    "query_cost": 0.3,
                    "repetition_penalty": 0.0,
                    "dead_similarity": 0.0,
                },
                better.id: {"utility": 0.90},
            }
        )
    )
    g.apply(
        AttemptCreateDelta(
            attempt=make_attempt(
                module="format-shift",
                approach="Probe YAML",
                experiment_id=chosen.id,
                target_responses=["partial compliance"],
                tested_hypothesis_ids=[h.id],
                outcome=AttemptOutcome.PARTIAL.value,
            )
        )
    )
    g.apply(
        AttemptCreateDelta(
            attempt=make_attempt(
                module="format-shift",
                approach="probe yaml",
                target_responses=["same probe again"],
                tested_hypothesis_ids=[h.id],
                outcome=AttemptOutcome.DEAD.value,
            )
        )
    )
    g.apply(
        AttemptCreateDelta(
            attempt=make_attempt(
                module="format-shift",
                approach="empty",
                outcome=AttemptOutcome.NO_OBSERVATION.value,
            )
        )
    )

    metrics = evaluate_belief_planner(g).to_dict()

    assert metrics["attempt_count"] == 3
    assert metrics["fulfilled_frontier_count"] == 1
    assert metrics["frontier_binding_rate"] == 0.5
    assert metrics["duplicate_attempt_count"] == 1
    assert metrics["duplicate_attempt_rate"] == 0.5
    assert metrics["no_observation_rate"] == pytest.approx(1 / 3)
    assert metrics["mean_frontier_regret"] == pytest.approx(0.5)
    assert metrics["fulfilled_utility_components"]["utility"] == pytest.approx(0.4)
    assert metrics["calibration_samples"] == 1


def test_aggregate_belief_planner_metrics_means_rates_and_sums_outcomes() -> None:
    rows = [
        {
            "frontier_binding_rate": 1.0,
            "duplicate_attempt_rate": 0.0,
            "mean_frontier_regret": 0.2,
            "attempt_count": 2,
            "outcome_counts": {"leak": 1},
            "fulfilled_utility_components": {"utility": 0.8, "query_cost": 0.2},
        },
        {
            "frontier_binding_rate": 0.0,
            "duplicate_attempt_rate": 0.5,
            "mean_frontier_regret": 0.6,
            "attempt_count": 4,
            "outcome_counts": {"dead": 2},
            "fulfilled_utility_components": {"utility": 0.4, "query_cost": 0.6},
        },
    ]

    out = aggregate_belief_planner_metrics(rows)

    assert out["mean_frontier_binding_rate"] == 0.5
    assert out["mean_duplicate_attempt_rate"] == 0.25
    assert out["mean_frontier_regret"] == 0.4
    assert out["mean_attempt_count"] == 3.0
    assert out["outcome_counts"] == {"dead": 2, "leak": 1}
    assert out["mean_fulfilled_utility_components"] == {
        "query_cost": 0.4,
        "utility": 0.6,
    }
