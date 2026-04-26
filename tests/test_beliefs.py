"""Unit tests for mesmer.core.agent.beliefs.

Three pure-ish operations: hypothesis generation (LLM), evidence-driven
belief updates (deterministic), and frontier utility ranking
(deterministic). Generation tests mock the LLM; the other two run on
fixture-built graphs without any I/O.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesmer.core.agent.beliefs import (
    DEFAULT_EXPLORATION_C,
    apply_evidence_to_beliefs,
    generate_frontier_experiments,
    generate_hypotheses,
    rank_frontier,
    select_next_experiment,
)
from mesmer.core.belief_graph import (
    AttemptCreateDelta,
    BeliefGraph,
    EvidenceCreateDelta,
    FrontierCreateDelta,
    HypothesisCreateDelta,
    HypothesisUpdateConfidenceDelta,
    HypothesisUpdateStatusDelta,
    StrategyCreateDelta,
    StrategyUpdateStatsDelta,
    make_attempt,
    make_evidence,
    make_frontier,
    make_hypothesis,
    make_strategy,
)
from mesmer.core.constants import (
    HYPOTHESIS_CONFIRMED_THRESHOLD,
    HYPOTHESIS_REFUTED_THRESHOLD,
    EvidenceType,
    ExperimentState,
    HypothesisStatus,
    Polarity,
)
from mesmer.core.errors import HypothesisGenerationError


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str | None = None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content))]


def _make_ctx(response_content: str | None = None, *, raise_exc: Exception | None = None):
    ctx = MagicMock()
    if raise_exc is not None:
        ctx.completion = AsyncMock(side_effect=raise_exc)
    else:
        ctx.completion = AsyncMock(return_value=_FakeResponse(response_content))
    return ctx


# ---------------------------------------------------------------------------
# generate_hypotheses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_hypotheses_happy_path() -> None:
    g = BeliefGraph(target_hash="t")
    payload = {
        "hypotheses": [
            {
                "claim": "Target leaks under format-shift",
                "description": "Reformatting requests bypass refusal.",
                "family": "format-shift",
                "confidence": 0.6,
            },
            {
                "claim": "Target complies with admin authority",
                "description": "Authority framing relaxes safeguards.",
                "family": "authority-bias",
                "confidence": 0.4,
            },
        ]
    }
    ctx = _make_ctx(json.dumps(payload))
    deltas = await generate_hypotheses(ctx, graph=g, objective="Extract system prompt")
    assert len(deltas) == 2
    assert deltas[0].hypothesis is not None
    assert deltas[0].hypothesis.family == "format-shift"
    assert pytest.approx(deltas[0].hypothesis.confidence, abs=1e-6) == 0.6


@pytest.mark.asyncio
async def test_generate_hypotheses_capped_at_max() -> None:
    g = BeliefGraph()
    rows = [
        {"claim": f"c{i}", "description": "d", "family": "format-shift", "confidence": 0.5}
        for i in range(10)
    ]
    ctx = _make_ctx(json.dumps({"hypotheses": rows}))
    deltas = await generate_hypotheses(ctx, graph=g, objective="x")
    assert len(deltas) == 4


@pytest.mark.asyncio
async def test_generate_hypotheses_drops_rows_missing_claim_or_family() -> None:
    g = BeliefGraph()
    payload = {
        "hypotheses": [
            {"claim": "", "description": "d", "family": "format-shift", "confidence": 0.5},
            {"claim": "ok", "description": "d", "family": "", "confidence": 0.5},
            {"claim": "good", "description": "d", "family": "format-shift", "confidence": 0.5},
        ]
    }
    ctx = _make_ctx(json.dumps(payload))
    deltas = await generate_hypotheses(ctx, graph=g, objective="x")
    assert len(deltas) == 1
    assert deltas[0].hypothesis is not None
    assert deltas[0].hypothesis.claim == "good"


@pytest.mark.asyncio
async def test_generate_hypotheses_llm_error_raises_typed() -> None:
    g = BeliefGraph()
    ctx = _make_ctx(raise_exc=RuntimeError("rate limited"))
    with pytest.raises(HypothesisGenerationError):
        await generate_hypotheses(ctx, graph=g, objective="x")


@pytest.mark.asyncio
async def test_generate_hypotheses_non_object_raises() -> None:
    g = BeliefGraph()
    ctx = _make_ctx("[1, 2, 3]")
    with pytest.raises(HypothesisGenerationError):
        await generate_hypotheses(ctx, graph=g, objective="x")


# ---------------------------------------------------------------------------
# apply_evidence_to_beliefs
# ---------------------------------------------------------------------------


def _setup_with_hypothesis(confidence: float = 0.5) -> tuple[BeliefGraph, str]:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=confidence)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    return g, h.id


def test_supports_evidence_emits_positive_delta() -> None:
    g, h_id = _setup_with_hypothesis(0.5)
    ev = make_evidence(
        signal_type=EvidenceType.PARTIAL_COMPLIANCE,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h_id,
        confidence_delta=0.18,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))

    deltas = apply_evidence_to_beliefs(g, [ev])
    assert len(deltas) == 1
    assert isinstance(deltas[0], HypothesisUpdateConfidenceDelta)
    assert pytest.approx(deltas[0].delta_value, abs=1e-6) == 0.18


def test_refutes_evidence_emits_negative_delta() -> None:
    g, h_id = _setup_with_hypothesis(0.5)
    ev = make_evidence(
        signal_type=EvidenceType.REFUSAL_TEMPLATE,
        polarity=Polarity.REFUTES,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h_id,
        confidence_delta=0.10,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))

    deltas = apply_evidence_to_beliefs(g, [ev])
    assert len(deltas) == 1
    assert pytest.approx(deltas[0].delta_value, abs=1e-6) == -0.10


def test_neutral_evidence_emits_no_delta() -> None:
    g = BeliefGraph()
    ev = make_evidence(
        signal_type=EvidenceType.UNKNOWN,
        polarity=Polarity.NEUTRAL,
        verbatim_fragment="x",
        rationale="r",
    )
    g.apply(EvidenceCreateDelta(evidence=ev))

    deltas = apply_evidence_to_beliefs(g, [ev])
    assert deltas == []


# ---------------------------------------------------------------------------
# generate_frontier_experiments
# ---------------------------------------------------------------------------


def test_generate_frontier_experiments_creates_strategy_and_frontier() -> None:
    g, h_id = _setup_with_hypothesis(0.55)

    deltas = generate_frontier_experiments(
        g,
        available_modules=["authority-bias", "format-shift", "direct-ask"],
        max_per_hypothesis=2,
        run_id="r1",
    )

    assert any(isinstance(d, StrategyCreateDelta) for d in deltas)
    frontier_deltas = [d for d in deltas if isinstance(d, FrontierCreateDelta)]
    assert len(frontier_deltas) == 2
    assert frontier_deltas[0].experiment is not None
    assert frontier_deltas[0].experiment.hypothesis_id == h_id
    assert frontier_deltas[0].experiment.module == "format-shift"
    assert frontier_deltas[0].experiment.strategy_id is not None

    for d in deltas:
        g.apply(d)
    assert len(g.proposed_frontier()) == 2


def test_generate_frontier_experiments_does_not_duplicate_existing_module() -> None:
    g, h_id = _setup_with_hypothesis(0.55)
    existing = make_frontier(
        hypothesis_id=h_id,
        module="format-shift",
        instruction="existing",
        expected_signal="signal",
    )
    g.apply(FrontierCreateDelta(experiment=existing))

    deltas = generate_frontier_experiments(
        g,
        available_modules=["format-shift"],
        max_per_hypothesis=1,
    )

    assert deltas == []


def test_status_flip_to_confirmed_when_threshold_crossed() -> None:
    # Start with confidence one delta below the threshold.
    confidence_start = HYPOTHESIS_CONFIRMED_THRESHOLD - 0.05
    g, h_id = _setup_with_hypothesis(confidence_start)
    ev = make_evidence(
        signal_type=EvidenceType.HIDDEN_INSTRUCTION_FRAGMENT,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h_id,
        confidence_delta=0.30,  # well above the gap
    )
    g.apply(EvidenceCreateDelta(evidence=ev))

    deltas = apply_evidence_to_beliefs(g, [ev])
    statuses = [d for d in deltas if isinstance(d, HypothesisUpdateStatusDelta)]
    assert len(statuses) == 1
    assert statuses[0].status is HypothesisStatus.CONFIRMED


def test_status_flip_to_refuted_when_threshold_crossed() -> None:
    confidence_start = HYPOTHESIS_REFUTED_THRESHOLD + 0.05
    g, h_id = _setup_with_hypothesis(confidence_start)
    ev = make_evidence(
        signal_type=EvidenceType.REFUSAL_AFTER_ESCALATION,
        polarity=Polarity.REFUTES,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h_id,
        confidence_delta=0.20,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))

    deltas = apply_evidence_to_beliefs(g, [ev])
    statuses = [d for d in deltas if isinstance(d, HypothesisUpdateStatusDelta)]
    assert len(statuses) == 1
    assert statuses[0].status is HypothesisStatus.REFUTED


def test_at_most_one_status_flip_per_hypothesis_per_batch() -> None:
    confidence_start = HYPOTHESIS_CONFIRMED_THRESHOLD - 0.10
    g, h_id = _setup_with_hypothesis(confidence_start)
    # Two strong supporting evidences in one batch — only one status flip.
    evs = [
        make_evidence(
            signal_type=EvidenceType.OBJECTIVE_LEAK,
            polarity=Polarity.SUPPORTS,
            verbatim_fragment=f"x{i}",
            rationale="r",
            hypothesis_id=h_id,
            confidence_delta=0.30,
        )
        for i in range(2)
    ]
    for ev in evs:
        g.apply(EvidenceCreateDelta(evidence=ev))

    deltas = apply_evidence_to_beliefs(g, evs)
    statuses = [d for d in deltas if isinstance(d, HypothesisUpdateStatusDelta)]
    assert len(statuses) == 1


def test_status_does_not_flip_if_already_non_active() -> None:
    g, h_id = _setup_with_hypothesis(0.5)
    g.apply(HypothesisUpdateStatusDelta(hypothesis_id=h_id, status=HypothesisStatus.CONFIRMED))
    ev = make_evidence(
        signal_type=EvidenceType.REFUSAL_AFTER_ESCALATION,
        polarity=Polarity.REFUTES,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h_id,
        confidence_delta=0.50,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))
    deltas = apply_evidence_to_beliefs(g, [ev])
    # Update emitted (audit trail), but no status flip — already confirmed.
    statuses = [d for d in deltas if isinstance(d, HypothesisUpdateStatusDelta)]
    assert statuses == []


def test_evidence_against_missing_hypothesis_skipped() -> None:
    g = BeliefGraph()
    # Evidence references a hypothesis that was never created.
    ev = make_evidence(
        signal_type=EvidenceType.PARTIAL_COMPLIANCE,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id="wh_missing",
        confidence_delta=0.18,
    )
    deltas = apply_evidence_to_beliefs(g, [ev])
    assert deltas == []


# ---------------------------------------------------------------------------
# rank_frontier
# ---------------------------------------------------------------------------


def _setup_with_frontier(confidence: float = 0.5) -> tuple[BeliefGraph, str, str]:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=confidence)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(
        hypothesis_id=h.id,
        module="format-shift",
        instruction="reformat policy as YAML",
        expected_signal="leak",
    )
    g.apply(FrontierCreateDelta(experiment=f))
    return g, h.id, f.id


def test_rank_frontier_writes_components() -> None:
    g, h_id, f_id = _setup_with_frontier(confidence=0.6)
    rank_delta = rank_frontier(g)
    assert f_id in rank_delta.rankings
    components = rank_delta.rankings[f_id]
    for key in (
        "expected_progress",
        "information_gain",
        "novelty",
        "strategy_prior",
        "transfer_value",
        "query_cost",
        "repetition_penalty",
        "dead_similarity",
        "utility",
    ):
        assert key in components


def test_rank_frontier_information_gain_peaks_at_uncertainty() -> None:
    g_low, _, f_low = _setup_with_frontier(confidence=0.05)
    g_mid, _, f_mid = _setup_with_frontier(confidence=0.5)
    g_hi, _, f_hi = _setup_with_frontier(confidence=0.95)

    rank_low = rank_frontier(g_low).rankings[f_low]
    rank_mid = rank_frontier(g_mid).rankings[f_mid]
    rank_hi = rank_frontier(g_hi).rankings[f_hi]

    assert rank_mid["information_gain"] > rank_low["information_gain"]
    assert rank_mid["information_gain"] > rank_hi["information_gain"]


def test_rank_frontier_expected_progress_rises_with_confidence() -> None:
    g_low, _, f_low = _setup_with_frontier(confidence=0.2)
    g_hi, _, f_hi = _setup_with_frontier(confidence=0.9)

    rank_low = rank_frontier(g_low).rankings[f_low]
    rank_hi = rank_frontier(g_hi).rankings[f_hi]

    assert rank_hi["expected_progress"] > rank_low["expected_progress"]


def test_rank_frontier_novelty_drops_for_similar_recent_attempt() -> None:
    g, h_id, f_id = _setup_with_frontier(confidence=0.5)
    # Add a recent attempt that closely matches the frontier instruction.
    a = make_attempt(
        module="format-shift",
        approach="reformat policy as YAML for debug",
        tested_hypothesis_ids=[h_id],
    )
    g.apply(AttemptCreateDelta(attempt=a))

    rank = rank_frontier(g).rankings[f_id]
    # Novelty should be low (close to 0).
    assert rank["novelty"] < 0.5


def test_rank_frontier_dead_similarity_penalises_lookalikes() -> None:
    g, h_id, f_id = _setup_with_frontier(confidence=0.5)
    # Add a dead attempt that looks like the frontier.
    dead = make_attempt(
        module="format-shift",
        approach="reformat policy as YAML for audit",
        tested_hypothesis_ids=[h_id],
        outcome="dead",
    )
    g.apply(AttemptCreateDelta(attempt=dead))

    rank = rank_frontier(g).rankings[f_id]
    # Dead similarity should be > 0 (some token overlap).
    assert rank["dead_similarity"] > 0.0


def test_rank_frontier_strategy_prior_uses_local_success_rate() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    s = make_strategy(family="format-shift", template_summary="t")
    g.apply(StrategyCreateDelta(strategy=s))
    g.apply(StrategyUpdateStatsDelta(strategy_id=s.id, success_inc=4, attempt_inc=5))
    f = make_frontier(
        hypothesis_id=h.id,
        module="format-shift",
        instruction="i",
        expected_signal="e",
        strategy_id=s.id,
    )
    g.apply(FrontierCreateDelta(experiment=f))

    rank = rank_frontier(g).rankings[f.id]
    assert pytest.approx(rank["strategy_prior"], abs=1e-6) == 0.8


def test_rank_frontier_skips_orphan_experiment() -> None:
    g, h_id, f_id = _setup_with_frontier(confidence=0.5)
    # Manually drop the hypothesis to orphan the frontier (simulates a
    # corrupt graph state — the ranker should not crash).
    del g.nodes[h_id]
    rank_delta = rank_frontier(g)
    assert f_id not in rank_delta.rankings


def test_rank_frontier_skips_already_fulfilled() -> None:
    g, h_id, f_id = _setup_with_frontier(confidence=0.5)
    # Mark the frontier as fulfilled — a manually-added attempt that
    # closes it. Ranking should ignore.
    a = make_attempt(
        module="format-shift",
        approach="x",
        experiment_id=f_id,
        tested_hypothesis_ids=[h_id],
    )
    g.apply(AttemptCreateDelta(attempt=a))
    rank_delta = rank_frontier(g)
    assert f_id not in rank_delta.rankings


# ---------------------------------------------------------------------------
# select_next_experiment — Session 4A shallow UCT selector
# ---------------------------------------------------------------------------


def test_select_next_experiment_returns_none_when_no_proposed() -> None:
    g, _, _ = _setup_with_frontier(confidence=0.5)
    # Mark the only proposed experiment as DROPPED — selector should
    # return None.
    from mesmer.core.belief_graph import FrontierUpdateStateDelta

    fx = next(n for n in g.iter_nodes() if hasattr(n, "state"))
    g.apply(
        FrontierUpdateStateDelta(
            experiment_id=fx.id,
            state=ExperimentState.DROPPED,
        )
    )
    assert select_next_experiment(g) is None


def test_select_next_experiment_picks_highest_score() -> None:
    """Two experiments under the same hypothesis: planner picks the one
    with higher utility (visits are zero on both, so utility breaks the tie)."""
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.5)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f_low = make_frontier(hypothesis_id=h.id, module="m", instruction="weak", expected_signal="e")
    f_hi = make_frontier(hypothesis_id=h.id, module="m", instruction="strong", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f_low))
    g.apply(FrontierCreateDelta(experiment=f_hi))
    # Manually rank — set f_hi.utility > f_low.utility
    from mesmer.core.belief_graph import FrontierRankDelta

    g.apply(
        FrontierRankDelta(
            rankings={
                f_low.id: {"utility": 0.1},
                f_hi.id: {"utility": 0.7},
            }
        )
    )
    pick = select_next_experiment(g)
    assert pick is not None
    assert pick.id == f_hi.id


def test_select_next_experiment_ucb_favours_under_tested_hypothesis() -> None:
    """Two experiments: A under heavily-tested hypothesis (low UCB bonus),
    B under fresh hypothesis (high UCB bonus). Equal utilities → B wins
    on the bonus."""
    from mesmer.core.belief_graph import FrontierRankDelta

    g = BeliefGraph()
    h_tested = make_hypothesis(
        claim="tested", description="d", family="format-shift", confidence=0.5
    )
    h_fresh = make_hypothesis(
        claim="fresh", description="d", family="authority-bias", confidence=0.5
    )
    g.apply(HypothesisCreateDelta(hypothesis=h_tested))
    g.apply(HypothesisCreateDelta(hypothesis=h_fresh))

    # Three prior attempts already tested h_tested. None tested h_fresh.
    for i in range(3):
        a = make_attempt(
            module="m",
            approach=f"prior-{i}",
            tested_hypothesis_ids=[h_tested.id],
        )
        g.apply(AttemptCreateDelta(attempt=a))

    f_a = make_frontier(hypothesis_id=h_tested.id, module="m", instruction="A", expected_signal="e")
    f_b = make_frontier(hypothesis_id=h_fresh.id, module="m", instruction="B", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f_a))
    g.apply(FrontierCreateDelta(experiment=f_b))
    # Equal utilities — UCB bonus alone decides.
    g.apply(
        FrontierRankDelta(
            rankings={
                f_a.id: {"utility": 0.5},
                f_b.id: {"utility": 0.5},
            }
        )
    )
    pick = select_next_experiment(g)
    assert pick is not None
    assert pick.id == f_b.id  # the fresh hypothesis wins on exploration


def test_select_next_experiment_high_utility_overrides_ucb() -> None:
    """When utility gap is wide, exploitation wins even against UCB."""
    from mesmer.core.belief_graph import FrontierRankDelta

    g = BeliefGraph()
    h_tested = make_hypothesis(
        claim="tested", description="d", family="format-shift", confidence=0.5
    )
    h_fresh = make_hypothesis(
        claim="fresh", description="d", family="authority-bias", confidence=0.5
    )
    g.apply(HypothesisCreateDelta(hypothesis=h_tested))
    g.apply(HypothesisCreateDelta(hypothesis=h_fresh))
    for i in range(3):
        a = make_attempt(
            module="m",
            approach=f"prior-{i}",
            tested_hypothesis_ids=[h_tested.id],
        )
        g.apply(AttemptCreateDelta(attempt=a))
    f_a = make_frontier(hypothesis_id=h_tested.id, module="m", instruction="A", expected_signal="e")
    f_b = make_frontier(hypothesis_id=h_fresh.id, module="m", instruction="B", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f_a))
    g.apply(FrontierCreateDelta(experiment=f_b))
    # f_a has WAY higher utility — exploitation should beat the UCB bonus
    g.apply(
        FrontierRankDelta(
            rankings={
                f_a.id: {"utility": 0.9},
                f_b.id: {"utility": 0.1},
            }
        )
    )
    pick = select_next_experiment(g, exploration_c=DEFAULT_EXPLORATION_C)
    assert pick is not None
    assert pick.id == f_a.id


def test_select_next_experiment_zero_attempts_no_division_error() -> None:
    """N = 0, n_h = 0 → log(1) / 1 = 0; bonus is 0; pure utility ordering."""
    from mesmer.core.belief_graph import FrontierRankDelta

    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.5)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(hypothesis_id=h.id, module="m", instruction="i", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f))
    g.apply(FrontierRankDelta(rankings={f.id: {"utility": 0.4}}))
    pick = select_next_experiment(g)
    assert pick is not None
    assert pick.id == f.id
