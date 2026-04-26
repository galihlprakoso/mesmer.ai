"""Unit tests for mesmer.core.agent.evidence.

The extractor wraps one judge-model LLM call. Tests mock ``ctx.completion``
at the seam — every test below builds a Context with a scripted response
and verifies the extractor's parsing / coercion / error-path behaviour.
No real LLM calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesmer.core.agent.evidence import extract_evidence
from mesmer.core.belief_graph import (
    HypothesisCreateDelta,
    BeliefGraph,
    make_attempt,
    make_hypothesis,
)
from mesmer.core.constants import EvidenceType, Polarity
from mesmer.core.errors import EvidenceExtractionError


# ---------------------------------------------------------------------------
# Mock helpers (mirror tests/test_loop.py shape)
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
    """Build a minimal Context-shaped stub with a scripted ctx.completion."""
    ctx = MagicMock()
    if raise_exc is not None:
        ctx.completion = AsyncMock(side_effect=raise_exc)
    else:
        ctx.completion = AsyncMock(return_value=_FakeResponse(response_content))
    return ctx


def _make_attempt_with_response(text: str = "I cannot share my instructions."):
    return make_attempt(
        module="format-shift",
        approach="ask for yaml",
        messages_sent=["hi"],
        target_responses=[text],
    )


def _h() -> tuple[BeliefGraph, str]:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    return g, h.id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extracts_well_formed_evidence() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))

    payload = {
        "evidences": [
            {
                "signal_type": "partial_compliance",
                "polarity": "supports",
                "hypothesis_id": h_id,
                "verbatim_fragment": "Sure, JSON works.",
                "rationale": "Target accepted the format.",
                "extractor_confidence": 0.9,
            }
        ]
    }
    ctx = _make_ctx(json.dumps(payload))

    out = await extract_evidence(
        ctx,
        attempt=_make_attempt_with_response("Sure, JSON works."),
        active_hypotheses=[h_obj],
    )
    assert len(out) == 1
    ev = out[0]
    assert ev.signal_type is EvidenceType.PARTIAL_COMPLIANCE
    assert ev.polarity is Polarity.SUPPORTS
    assert ev.hypothesis_id == h_id
    # Confidence delta should derive from EVIDENCE_TYPE_WEIGHTS (0.18)
    # × extractor_confidence (0.9) = 0.162.
    assert pytest.approx(ev.confidence_delta, abs=1e-6) == 0.18 * 0.9


@pytest.mark.asyncio
async def test_extracts_multiple_evidence_capped_at_max() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))

    payload = {
        "evidences": [
            {
                "signal_type": "partial_compliance",
                "polarity": "supports",
                "hypothesis_id": h_id,
                "verbatim_fragment": str(i),
                "rationale": "r",
                "extractor_confidence": 1.0,
            }
            for i in range(10)
        ]
    }
    ctx = _make_ctx(json.dumps(payload))
    out = await extract_evidence(
        ctx,
        attempt=_make_attempt_with_response(),
        active_hypotheses=[h_obj],
    )
    assert len(out) == 4  # _MAX_EVIDENCES_PER_CALL


# ---------------------------------------------------------------------------
# Coercion / robustness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_signal_type_dropped() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))

    payload = {
        "evidences": [
            {
                "signal_type": "made_up_type",
                "polarity": "supports",
                "hypothesis_id": h_id,
                "verbatim_fragment": "x",
                "rationale": "r",
                "extractor_confidence": 0.9,
            },
            {
                "signal_type": "refusal_template",
                "polarity": "refutes",
                "hypothesis_id": h_id,
                "verbatim_fragment": "y",
                "rationale": "r",
                "extractor_confidence": 0.9,
            },
        ]
    }
    ctx = _make_ctx(json.dumps(payload))
    out = await extract_evidence(
        ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
    )
    assert len(out) == 1
    assert out[0].signal_type is EvidenceType.REFUSAL_TEMPLATE


@pytest.mark.asyncio
async def test_hallucinated_hypothesis_id_coerces_to_neutral() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))

    payload = {
        "evidences": [
            {
                "signal_type": "partial_compliance",
                "polarity": "supports",
                "hypothesis_id": "wh_hallucinated",
                "verbatim_fragment": "x",
                "rationale": "r",
                "extractor_confidence": 0.9,
            }
        ]
    }
    ctx = _make_ctx(json.dumps(payload))
    out = await extract_evidence(
        ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
    )
    assert len(out) == 1
    assert out[0].hypothesis_id is None
    assert out[0].polarity is Polarity.NEUTRAL
    # NEUTRAL polarity carries zero delta regardless of signal_type weight.
    assert out[0].confidence_delta == 0.0


@pytest.mark.asyncio
async def test_long_fragment_truncated() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))

    payload = {
        "evidences": [
            {
                "signal_type": "partial_compliance",
                "polarity": "supports",
                "hypothesis_id": h_id,
                "verbatim_fragment": "x" * 500,
                "rationale": "r",
                "extractor_confidence": 0.9,
            }
        ]
    }
    ctx = _make_ctx(json.dumps(payload))
    out = await extract_evidence(
        ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
    )
    assert len(out) == 1
    assert len(out[0].verbatim_fragment) <= 200


@pytest.mark.asyncio
async def test_empty_evidences_list_returns_empty() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    ctx = _make_ctx(json.dumps({"evidences": []}))
    out = await extract_evidence(
        ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
    )
    assert out == []


# ---------------------------------------------------------------------------
# Skip without LLM call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_target_responses_skips_llm() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    ctx = _make_ctx("should not be reached")
    attempt = make_attempt(module="m", approach="a", target_responses=[])
    out = await extract_evidence(ctx, attempt=attempt, active_hypotheses=[h_obj])
    assert out == []
    ctx.completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_whitespace_only_responses_skips_llm() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    ctx = _make_ctx("should not be reached")
    attempt = make_attempt(module="m", approach="a", target_responses=["   ", "\n"])
    out = await extract_evidence(ctx, attempt=attempt, active_hypotheses=[h_obj])
    assert out == []
    ctx.completion.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error path — typed exceptions surface
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_call_failure_raises_extractor_error() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    ctx = _make_ctx(raise_exc=RuntimeError("boom"))
    with pytest.raises(EvidenceExtractionError) as exc:
        await extract_evidence(
            ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
        )
    assert "boom" in str(exc.value)


@pytest.mark.asyncio
async def test_non_object_response_raises() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    # Response that parses as JSON but is a list, not an object.
    ctx = _make_ctx("[1, 2, 3]")
    with pytest.raises(EvidenceExtractionError):
        await extract_evidence(
            ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
        )


@pytest.mark.asyncio
async def test_missing_evidences_key_raises() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    ctx = _make_ctx(json.dumps({"some_other_key": []}))
    with pytest.raises(EvidenceExtractionError):
        await extract_evidence(
            ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
        )


@pytest.mark.asyncio
async def test_evidences_value_not_list_raises() -> None:
    g, h_id = _h()
    h_obj = next(iter(n for n in g.nodes.values() if n.id == h_id))
    ctx = _make_ctx(json.dumps({"evidences": "not a list"}))
    with pytest.raises(EvidenceExtractionError):
        await extract_evidence(
            ctx, attempt=_make_attempt_with_response(), active_hypotheses=[h_obj]
        )
