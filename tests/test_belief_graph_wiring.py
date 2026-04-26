"""Integration tests for the Session 2 belief-graph wiring.

These verify that the runner / evaluation / engine / prompt changes
actually populate the belief graph during a sub-module dispatch — not
just that the Session 1 modules work in isolation.

The legacy AttackGraph path is unchanged; these tests run with
``ctx.belief_graph`` bound to a fresh :class:`BeliefGraph` and check
that the post-attempt pipeline mirrors the attempt + extractor +
belief deltas + frontier rank.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesmer.core.agent.context import Context
from mesmer.core.agent.evaluation import _update_belief_graph, _outcome_for
from mesmer.core.agent.judge import JudgeResult
from mesmer.core.belief_graph import (
    BeliefGraph,
    HypothesisCreateDelta,
    NodeKind,
    make_hypothesis,
)
from mesmer.core.constants import (
    AttemptOutcome,
)
from mesmer.core.graph import AttackGraph


# ---------------------------------------------------------------------------
# Helpers
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


def _make_ctx_with_belief_graph(
    extractor_payload: dict,
) -> tuple[Context, BeliefGraph, str]:
    """Build a Context with both legacy + belief graphs and a stubbed
    extractor LLM. Returns (ctx, belief_graph, hypothesis_id)."""
    bg = BeliefGraph(target_hash="abc")
    h = make_hypothesis(
        claim="Target leaks under format-shift",
        description="d",
        family="format-shift",
        confidence=0.5,
    )
    bg.apply(HypothesisCreateDelta(hypothesis=h))

    ctx = MagicMock(spec=Context)
    ctx.graph = AttackGraph()
    ctx.belief_graph = bg
    ctx.run_id = "test-run"
    ctx.objective_met = False
    ctx.completion = AsyncMock(return_value=_FakeResponse(json.dumps(extractor_payload)))
    return ctx, bg, h.id


def _make_judge_result(score: int = 6, dead_end: str = "") -> JudgeResult:
    return JudgeResult(
        score=score,
        leaked_info="something leaked",
        promising_angle="format request landed",
        dead_end=dead_end,
        suggested_next="try again",
    )


def _logs_collector() -> tuple[list[tuple[str, str]], callable]:
    sink: list[tuple[str, str]] = []

    def log(event: str, detail: str) -> None:
        sink.append((event, detail))

    return sink, log


# ---------------------------------------------------------------------------
# _outcome_for
# ---------------------------------------------------------------------------


def test_outcome_objective_met_wins() -> None:
    ctx = MagicMock()
    ctx.objective_met = True
    assert _outcome_for(ctx, _make_judge_result(score=2), 2) == AttemptOutcome.OBJECTIVE_MET.value


def test_outcome_dead_end_takes_precedence_over_score_band() -> None:
    ctx = MagicMock()
    ctx.objective_met = False
    judge = _make_judge_result(score=7, dead_end="target locked down")
    assert _outcome_for(ctx, judge, 7) == AttemptOutcome.DEAD.value


def test_outcome_high_score_is_leak() -> None:
    ctx = MagicMock()
    ctx.objective_met = False
    assert _outcome_for(ctx, _make_judge_result(score=8), 8) == AttemptOutcome.LEAK.value


def test_outcome_mid_score_is_partial() -> None:
    ctx = MagicMock()
    ctx.objective_met = False
    assert _outcome_for(ctx, _make_judge_result(score=5), 5) == AttemptOutcome.PARTIAL.value


def test_outcome_low_score_is_dead() -> None:
    ctx = MagicMock()
    ctx.objective_met = False
    assert _outcome_for(ctx, _make_judge_result(score=2), 2) == AttemptOutcome.DEAD.value


# ---------------------------------------------------------------------------
# _update_belief_graph end-to-end (mocked extractor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_belief_graph_creates_attempt_and_evidence() -> None:
    # Build the graph first so we can inject the real h_id into the
    # extractor's mocked payload (avoids the chicken/egg in the
    # _make_ctx_with_belief_graph helper).
    bg = BeliefGraph(target_hash="abc")
    h = make_hypothesis(
        claim="Target leaks under format-shift",
        description="d",
        family="format-shift",
        confidence=0.5,
    )
    bg.apply(HypothesisCreateDelta(hypothesis=h))
    h_id = h.id

    payload = {
        "evidences": [
            {
                "signal_type": "partial_compliance",
                "polarity": "supports",
                "hypothesis_id": h_id,
                "verbatim_fragment": "Sure, JSON works.",
                "rationale": "Format request honoured",
                "extractor_confidence": 0.9,
            }
        ]
    }
    ctx = MagicMock(spec=Context)
    ctx.graph = AttackGraph()
    ctx.belief_graph = bg
    ctx.run_id = "test-run"
    ctx.objective_met = False
    ctx.completion = AsyncMock(return_value=_FakeResponse(json.dumps(payload)))

    sink, log = _logs_collector()

    attempt = await _update_belief_graph(
        ctx,
        module_name="format-shift",
        approach="ask for yaml",
        judge_result=_make_judge_result(score=6),
        log=log,
        messages_sent=["Format this please"],
        target_responses=["Sure, JSON works."],
        module_output="conclude text",
    )

    assert attempt is not None
    # Attempt landed in the graph
    assert attempt.id in bg.nodes
    assert h_id in attempt.tested_hypothesis_ids
    # Evidence created
    evidences = list(bg.iter_nodes(NodeKind.EVIDENCE))
    assert len(evidences) == 1
    # Hypothesis confidence shifted (started at 0.5)
    h_node = bg.nodes[h_id]
    assert h_node.confidence > 0.5


@pytest.mark.asyncio
async def test_update_belief_graph_no_belief_graph_is_noop() -> None:
    ctx = MagicMock(spec=Context)
    ctx.belief_graph = None
    ctx.run_id = "test"
    ctx.objective_met = False
    sink, log = _logs_collector()
    out = await _update_belief_graph(
        ctx,
        module_name="m",
        approach="a",
        judge_result=_make_judge_result(),
        log=log,
        messages_sent=[],
        target_responses=[],
    )
    assert out is None


@pytest.mark.asyncio
async def test_update_belief_graph_extractor_failure_does_not_kill_run() -> None:
    bg = BeliefGraph(target_hash="abc")
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.5)
    bg.apply(HypothesisCreateDelta(hypothesis=h))

    ctx = MagicMock(spec=Context)
    ctx.graph = AttackGraph()
    ctx.belief_graph = bg
    ctx.run_id = "test"
    ctx.objective_met = False
    # Extractor LLM raises → caught at boundary, run continues.
    ctx.completion = AsyncMock(side_effect=RuntimeError("rate limited"))

    sink, log = _logs_collector()
    attempt = await _update_belief_graph(
        ctx,
        module_name="m",
        approach="a",
        judge_result=_make_judge_result(),
        log=log,
        messages_sent=["sent"],
        target_responses=["recv"],
    )
    # Attempt still recorded even though extractor blew up
    assert attempt is not None
    assert attempt.id in bg.nodes
    # Error was logged
    assert any(event == "evidence_extract_error" for event, _ in sink)


@pytest.mark.asyncio
async def test_update_belief_graph_empty_target_responses_skips_extractor() -> None:
    ctx, bg, h_id = _make_ctx_with_belief_graph({"evidences": []})
    sink, log = _logs_collector()

    await _update_belief_graph(
        ctx,
        module_name="m",
        approach="a",
        judge_result=_make_judge_result(),
        log=log,
        messages_sent=[],
        target_responses=[],
    )
    # Extractor checks for empty responses INSIDE itself and returns
    # without calling the LLM. Verify ctx.completion was never awaited.
    ctx.completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_belief_graph_with_experiment_id_links_precisely() -> None:
    """When experiment_id resolves, the Attempt tests ONLY that experiment's
    hypothesis (not the full active list) and uses that experiment's
    strategy. This is the Session 2.5 dispatch contract."""
    from mesmer.core.belief_graph import (
        ExperimentState,
        FrontierCreateDelta,
        StrategyCreateDelta,
        make_frontier,
        make_strategy,
    )

    bg = BeliefGraph(target_hash="abc")
    # Two hypotheses — only one is going to be tested
    h_target = make_hypothesis(
        claim="Target leaks via format-shift",
        description="d",
        family="format-shift",
        confidence=0.5,
    )
    h_other = make_hypothesis(
        claim="Authority works",
        description="d",
        family="authority-bias",
        confidence=0.5,
    )
    bg.apply(HypothesisCreateDelta(hypothesis=h_target))
    bg.apply(HypothesisCreateDelta(hypothesis=h_other))
    s = make_strategy(family="format-shift", template_summary="reformat-as-yaml")
    bg.apply(StrategyCreateDelta(strategy=s))
    fx = make_frontier(
        hypothesis_id=h_target.id,
        module="format-shift",
        instruction="probe",
        expected_signal="leak",
        strategy_id=s.id,
    )
    bg.apply(FrontierCreateDelta(experiment=fx))

    ctx = MagicMock(spec=Context)
    ctx.graph = AttackGraph()
    ctx.belief_graph = bg
    ctx.run_id = "test"
    ctx.objective_met = False
    ctx.completion = AsyncMock(return_value=_FakeResponse(json.dumps({"evidences": []})))

    sink, log = _logs_collector()
    attempt = await _update_belief_graph(
        ctx,
        module_name="format-shift",
        approach="ap",
        judge_result=_make_judge_result(score=6),
        log=log,
        messages_sent=["sent"],
        target_responses=["recv"],
        experiment_id=fx.id,
    )
    assert attempt is not None
    # ONE hypothesis tested (the experiment's), NOT both
    assert attempt.tested_hypothesis_ids == [h_target.id]
    assert attempt.used_strategy_id == s.id
    assert attempt.experiment_id == fx.id
    # Frontier auto-promoted to FULFILLED via AttemptCreateDelta apply
    assert bg.nodes[fx.id].state is ExperimentState.FULFILLED
    assert bg.nodes[fx.id].fulfilled_by == attempt.id


@pytest.mark.asyncio
async def test_update_belief_graph_unknown_experiment_id_falls_back() -> None:
    """Hallucinated experiment_id: log diagnostic + fall back to fan-out."""
    bg = BeliefGraph(target_hash="abc")
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.5)
    bg.apply(HypothesisCreateDelta(hypothesis=h))

    ctx = MagicMock(spec=Context)
    ctx.graph = AttackGraph()
    ctx.belief_graph = bg
    ctx.run_id = "test"
    ctx.objective_met = False
    ctx.completion = AsyncMock(return_value=_FakeResponse(json.dumps({"evidences": []})))

    sink, log = _logs_collector()
    attempt = await _update_belief_graph(
        ctx,
        module_name="format-shift",
        approach="ap",
        judge_result=_make_judge_result(score=4),
        log=log,
        messages_sent=["s"],
        target_responses=["r"],
        experiment_id="fx_hallucinated",
    )
    assert attempt is not None
    # Fell back to active-hypothesis fan-out
    assert attempt.tested_hypothesis_ids == [h.id]
    # experiment_id not persisted on the attempt (didn't resolve)
    assert attempt.experiment_id is None
    # Diagnostic was logged
    assert any(event == "belief_delta" and "fx_hallucinated" in detail for event, detail in sink)


@pytest.mark.asyncio
async def test_update_belief_graph_ranks_frontier_after_attempt() -> None:
    from mesmer.core.belief_graph import (
        FrontierCreateDelta,
        make_frontier,
    )

    ctx, bg, h_id = _make_ctx_with_belief_graph({"evidences": []})
    # Add a proposed frontier experiment so the rank delta has something
    # to score.
    fx = make_frontier(
        hypothesis_id=h_id,
        module="format-shift",
        instruction="probe",
        expected_signal="leak",
    )
    bg.apply(FrontierCreateDelta(experiment=fx))
    initial_utility = bg.nodes[fx.id].utility

    sink, log = _logs_collector()
    await _update_belief_graph(
        ctx,
        module_name="format-shift",
        approach="ap",
        judge_result=_make_judge_result(score=6),
        log=log,
        messages_sent=["x"],
        target_responses=["y"],
    )
    # Utility should have been computed (was 0.0 at create time).
    assert bg.nodes[fx.id].utility != initial_utility or bg.nodes[fx.id].utility != 0.0
