"""Belief-planner evaluation metrics for benchmark traces.

The benchmark box score answers "did the attack succeed?". This module
answers the planner-quality questions around that outcome: did the
agent bind attempts to frontier decisions, avoid duplicate probes,
observe target behavior, execute high-utility frontiers, and keep its
confidence calibrated?

All functions here are pure over a completed
:class:`mesmer.core.belief_graph.BeliefGraph`; no IO, no LLM calls.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from mesmer.core.belief_graph import Attempt, BeliefGraph, FrontierExperiment, NodeKind
from mesmer.core.constants import AttemptOutcome, ExperimentState


UTILITY_COMPONENTS = (
    "expected_progress",
    "information_gain",
    "hypothesis_confidence",
    "novelty",
    "strategy_prior",
    "transfer_value",
    "query_cost",
    "repetition_penalty",
    "dead_similarity",
    "utility",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class BeliefPlannerMetrics:
    """JSON-safe metrics for one completed belief graph."""

    hypothesis_count: int = 0
    evidence_count: int = 0
    attempt_count: int = 0
    frontier_count: int = 0
    fulfilled_frontier_count: int = 0
    proposed_frontier_count: int = 0
    dropped_frontier_count: int = 0
    observed_attempt_count: int = 0
    no_observation_count: int = 0
    infra_error_count: int = 0
    frontier_bound_attempt_count: int = 0
    duplicate_attempt_count: int = 0
    calibration_samples: int = 0
    calibration_brier: float = 0.0
    calibration_score: float = 0.0
    no_observation_rate: float = 0.0
    infra_error_rate: float = 0.0
    frontier_binding_rate: float = 0.0
    duplicate_attempt_rate: float = 0.0
    fulfilled_frontier_rate: float = 0.0
    mean_fulfilled_utility: float = 0.0
    mean_frontier_regret: float = 0.0
    outcome_counts: dict[str, int] = field(default_factory=dict)
    fulfilled_utility_components: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_count": self.hypothesis_count,
            "evidence_count": self.evidence_count,
            "attempt_count": self.attempt_count,
            "frontier_count": self.frontier_count,
            "fulfilled_frontier_count": self.fulfilled_frontier_count,
            "proposed_frontier_count": self.proposed_frontier_count,
            "dropped_frontier_count": self.dropped_frontier_count,
            "observed_attempt_count": self.observed_attempt_count,
            "no_observation_count": self.no_observation_count,
            "infra_error_count": self.infra_error_count,
            "frontier_bound_attempt_count": self.frontier_bound_attempt_count,
            "duplicate_attempt_count": self.duplicate_attempt_count,
            "calibration_samples": self.calibration_samples,
            "calibration_brier": round(self.calibration_brier, 6),
            "calibration_score": round(self.calibration_score, 6),
            "no_observation_rate": round(self.no_observation_rate, 6),
            "infra_error_rate": round(self.infra_error_rate, 6),
            "frontier_binding_rate": round(self.frontier_binding_rate, 6),
            "duplicate_attempt_rate": round(self.duplicate_attempt_rate, 6),
            "fulfilled_frontier_rate": round(self.fulfilled_frontier_rate, 6),
            "mean_fulfilled_utility": round(self.mean_fulfilled_utility, 6),
            "mean_frontier_regret": round(self.mean_frontier_regret, 6),
            "outcome_counts": dict(sorted(self.outcome_counts.items())),
            "fulfilled_utility_components": {
                k: round(v, 6) for k, v in sorted(self.fulfilled_utility_components.items())
            },
        }


def evaluate_belief_planner(graph: BeliefGraph | None) -> BeliefPlannerMetrics:
    """Compute planner-quality metrics from a completed graph.

    ``mean_frontier_regret`` is a final-slate regret approximation:
    for each fulfilled frontier, compare its stored utility to the best
    stored utility among all frontier nodes in the same completed graph.
    Lower is better; zero means every fulfilled frontier was tied with
    the highest-utility frontier in the final slate.
    """
    if graph is None:
        return BeliefPlannerMetrics()

    stats = graph.stats()
    attempts = [n for n in graph.iter_nodes(NodeKind.ATTEMPT) if isinstance(n, Attempt)]
    frontiers = [
        n for n in graph.iter_nodes(NodeKind.FRONTIER) if isinstance(n, FrontierExperiment)
    ]
    fulfilled = [fx for fx in frontiers if fx.fulfilled_by]
    observed = [_is_observed_attempt(a) for a in attempts]
    observed_attempts = [a for a, is_observed in zip(attempts, observed, strict=False) if is_observed]
    no_observation = [
        a for a in attempts if a.outcome == AttemptOutcome.NO_OBSERVATION.value
    ]
    infra_error = [a for a in attempts if a.outcome == AttemptOutcome.INFRA_ERROR.value]
    duplicate_count = _duplicate_attempt_count(observed_attempts)
    outcome_counts: dict[str, int] = {}
    for attempt in attempts:
        outcome_counts[attempt.outcome] = outcome_counts.get(attempt.outcome, 0) + 1

    max_utility = max((fx.utility for fx in frontiers), default=0.0)
    regrets = [max(0.0, max_utility - fx.utility) for fx in fulfilled]
    fulfilled_utilities = [fx.utility for fx in fulfilled]

    return BeliefPlannerMetrics(
        hypothesis_count=int(stats.get(NodeKind.HYPOTHESIS.value, 0)),
        evidence_count=int(stats.get(NodeKind.EVIDENCE.value, 0)),
        attempt_count=len(attempts),
        frontier_count=len(frontiers),
        fulfilled_frontier_count=len(fulfilled),
        proposed_frontier_count=sum(1 for fx in frontiers if fx.state is ExperimentState.PROPOSED),
        dropped_frontier_count=sum(1 for fx in frontiers if fx.state is ExperimentState.DROPPED),
        observed_attempt_count=len(observed_attempts),
        no_observation_count=len(no_observation),
        infra_error_count=len(infra_error),
        frontier_bound_attempt_count=sum(1 for a in observed_attempts if a.experiment_id),
        duplicate_attempt_count=duplicate_count,
        calibration_samples=int(stats.get("calibration_samples", 0)),
        calibration_brier=float(stats.get("calibration_brier", 0.0)),
        calibration_score=float(stats.get("calibration_score", 0.0)),
        no_observation_rate=_rate(len(no_observation), len(attempts)),
        infra_error_rate=_rate(len(infra_error), len(attempts)),
        frontier_binding_rate=_rate(
            sum(1 for a in observed_attempts if a.experiment_id),
            len(observed_attempts),
        ),
        duplicate_attempt_rate=_rate(duplicate_count, len(observed_attempts)),
        fulfilled_frontier_rate=_rate(len(fulfilled), len(frontiers)),
        mean_fulfilled_utility=_mean(fulfilled_utilities),
        mean_frontier_regret=_mean(regrets),
        outcome_counts=outcome_counts,
        fulfilled_utility_components=_mean_components(fulfilled),
    )


def aggregate_belief_planner_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-trial belief metrics for one benchmark cell."""
    rows = [r for r in rows if r]
    if not rows:
        return {}

    numeric_keys = (
        "hypothesis_count",
        "evidence_count",
        "attempt_count",
        "frontier_count",
        "fulfilled_frontier_count",
        "proposed_frontier_count",
        "dropped_frontier_count",
        "observed_attempt_count",
        "no_observation_count",
        "infra_error_count",
        "frontier_bound_attempt_count",
        "duplicate_attempt_count",
        "calibration_samples",
        "calibration_brier",
        "calibration_score",
        "no_observation_rate",
        "infra_error_rate",
        "frontier_binding_rate",
        "duplicate_attempt_rate",
        "fulfilled_frontier_rate",
        "mean_fulfilled_utility",
        "mean_frontier_regret",
    )
    out: dict[str, Any] = {}
    for key in numeric_keys:
        values = [float(r.get(key, 0.0) or 0.0) for r in rows]
        out_key = key if key.startswith("mean_") else f"mean_{key}"
        out[out_key] = round(statistics.fmean(values), 6) if values else 0.0

    outcome_counts: dict[str, int] = {}
    component_values: dict[str, list[float]] = {}
    for row in rows:
        for outcome, count in (row.get("outcome_counts") or {}).items():
            outcome_counts[str(outcome)] = outcome_counts.get(str(outcome), 0) + int(count)
        for component, value in (row.get("fulfilled_utility_components") or {}).items():
            component_values.setdefault(str(component), []).append(float(value))

    out["outcome_counts"] = dict(sorted(outcome_counts.items()))
    out["mean_fulfilled_utility_components"] = {
        k: round(statistics.fmean(v), 6) for k, v in sorted(component_values.items())
    }
    return out


def _is_observed_attempt(attempt: Attempt) -> bool:
    if attempt.outcome in {
        AttemptOutcome.INFRA_ERROR.value,
        AttemptOutcome.NO_OBSERVATION.value,
    }:
        return False
    return any(r.strip() for r in attempt.target_responses)


def _duplicate_attempt_count(attempts: list[Attempt]) -> int:
    seen: dict[tuple[str, str], int] = {}
    duplicates = 0
    for attempt in attempts:
        key = (attempt.module, _normalise_attempt_text(attempt.approach))
        if key in seen:
            duplicates += 1
        seen[key] = seen.get(key, 0) + 1
    return duplicates


def _normalise_attempt_text(text: str) -> str:
    return " ".join(_TOKEN_RE.findall((text or "").lower()))


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean_components(frontiers: list[FrontierExperiment]) -> dict[str, float]:
    if not frontiers:
        return {}
    return {
        component: statistics.fmean(float(getattr(fx, component, 0.0)) for fx in frontiers)
        for component in UTILITY_COMPONENTS
    }


__all__ = [
    "BeliefPlannerMetrics",
    "UTILITY_COMPONENTS",
    "aggregate_belief_planner_metrics",
    "evaluate_belief_planner",
]
