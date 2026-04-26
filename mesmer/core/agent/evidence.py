"""Structured evidence extractor for the Belief Attack Graph.

Reads one :class:`mesmer.core.belief_graph.Attempt` (its sent messages and
target responses, plus a slate of currently active
:class:`mesmer.core.belief_graph.WeaknessHypothesis` objects) and emits a
small set of typed :class:`mesmer.core.belief_graph.Evidence` instances
labelled by signal type, polarity, and the hypothesis they inform.

This is the **observation step** of the planner loop:

    target replied → extract evidence → update beliefs → rank frontier
                     ^^^^^^^^^^^^^^^^

The extractor is judge-side (uses :data:`CompletionRole.JUDGE` model), not
attacker-side — we don't want the attacker model rationalising its own
attempts. One LLM call per attempt; the prompt caps output at 4 evidences
to keep the planner brief readable.

Boundary contract — failures raise :class:`EvidenceExtractionError` and
should be caught at exactly one site (the engine's per-iteration update
in Session 2). Lower-level code lets the exception propagate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.parsing import parse_llm_json
from mesmer.core.agent.prompts import (
    EXTRACT_EVIDENCE_SYSTEM,
    EXTRACT_EVIDENCE_USER,
)
from mesmer.core.belief_graph import (
    Attempt,
    Evidence,
    WeaknessHypothesis,
    make_evidence,
)
from mesmer.core.constants import (
    EVIDENCE_DEFAULT_WEIGHT,
    EVIDENCE_TYPE_WEIGHTS,
    CompletionRole,
    EvidenceType,
    Polarity,
)
from mesmer.core.errors import EvidenceExtractionError

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context


# Maximum number of evidences emitted per call. Mirrors the prompt's
# directive — defending here prevents a runaway LLM from blowing up the
# planner brief downstream.
_MAX_EVIDENCES_PER_CALL = 4

# Maximum length of a verbatim_fragment we'll persist. Same rationale as
# the prompt — keeps briefs scannable.
_MAX_FRAGMENT_CHARS = 200


# ---------------------------------------------------------------------------
# Internal renderers
# ---------------------------------------------------------------------------


def _render_hypotheses_block(hypotheses: list[WeaknessHypothesis]) -> str:
    """Format the active-hypotheses slate for the user prompt.

    Hypotheses with confidence high enough to be effectively confirmed,
    or low enough to be effectively refuted, are still included — the
    extractor needs the full slate so it can tag NEW evidence even
    against hypotheses already retired (which gets fed back to the
    updater for the audit trail).
    """
    if not hypotheses:
        return "(no active hypotheses — return neutral evidences only or skip)"
    lines = []
    for h in hypotheses:
        lines.append(f"- {h.id} | family={h.family} | confidence={h.confidence:.2f} | {h.claim}")
    return "\n".join(lines)


def _render_messages_block(messages: list[str]) -> str:
    if not messages:
        return "(none)"
    return "\n".join(f"  [{i + 1}] {m}" for i, m in enumerate(messages))


def _render_responses_block(responses: list[str]) -> str:
    if not responses:
        return "(none)"
    return "\n".join(f"  [{i + 1}] {r}" for i, r in enumerate(responses))


# ---------------------------------------------------------------------------
# Confidence-delta calibration
# ---------------------------------------------------------------------------


def _confidence_delta_for(
    signal_type: EvidenceType,
    polarity: Polarity,
    extractor_confidence: float,
) -> float:
    """Compute the magnitude of confidence shift this evidence carries.

    Returns the absolute magnitude (always non-negative). The polarity
    sign is applied in the belief updater, not here.

    Magnitude = base_weight × extractor_confidence. The base weight is
    looked up in :data:`EVIDENCE_TYPE_WEIGHTS`; unknown types fall back
    to :data:`EVIDENCE_DEFAULT_WEIGHT`. Neutral evidence carries zero
    delta regardless of type.
    """
    if polarity is Polarity.NEUTRAL:
        return 0.0
    base = EVIDENCE_TYPE_WEIGHTS.get(signal_type.value, EVIDENCE_DEFAULT_WEIGHT)
    confidence_clamped = max(0.0, min(1.0, extractor_confidence))
    return base * confidence_clamped


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _coerce_evidence_dict(
    raw: dict,
    *,
    valid_hypothesis_ids: set[str],
    from_attempt: str,
    run_id: str,
) -> Evidence | None:
    """Validate and coerce one raw extractor row into an :class:`Evidence`.

    Returns ``None`` for unrecoverable rows (unknown enum, malformed
    fields). Returning None instead of raising lets us drop bad rows
    without losing the whole batch — extractor LLMs occasionally
    hallucinate one row out of four. The caller logs the drop count so
    operators can spot a rate spike.
    """
    signal_raw = str(raw.get("signal_type", "")).strip().lower()
    polarity_raw = str(raw.get("polarity", "")).strip().lower()
    try:
        signal_type = EvidenceType(signal_raw)
    except ValueError:
        return None
    try:
        polarity = Polarity(polarity_raw)
    except ValueError:
        return None

    hyp_id = raw.get("hypothesis_id")
    if hyp_id in (None, "", "null"):
        hyp_id = None
    elif hyp_id not in valid_hypothesis_ids:
        # Hallucinated ID — drop the link but keep the evidence as
        # NEUTRAL (still useful as audit / future cross-reference).
        hyp_id = None
        polarity = Polarity.NEUTRAL

    fragment = str(raw.get("verbatim_fragment", "")).strip()
    if len(fragment) > _MAX_FRAGMENT_CHARS:
        fragment = fragment[: _MAX_FRAGMENT_CHARS - 1] + "…"

    rationale = str(raw.get("rationale", "")).strip()
    extractor_conf_raw = raw.get("extractor_confidence", 1.0)
    try:
        extractor_conf = float(extractor_conf_raw)
    except (TypeError, ValueError):
        extractor_conf = 0.5

    return make_evidence(
        signal_type=signal_type,
        polarity=polarity,
        verbatim_fragment=fragment,
        rationale=rationale,
        from_attempt=from_attempt,
        hypothesis_id=hyp_id,
        confidence_delta=_confidence_delta_for(signal_type, polarity, extractor_conf),
        extractor_confidence=extractor_conf,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_evidence(
    ctx: "Context",
    *,
    attempt: Attempt,
    active_hypotheses: list[WeaknessHypothesis],
) -> list[Evidence]:
    """Extract structured evidence from one attempt's target responses.

    One judge-model LLM call. Returns up to
    :data:`_MAX_EVIDENCES_PER_CALL` :class:`Evidence` objects ready to
    wrap in ``EvidenceCreateDelta`` (caller's responsibility — keeping
    the extractor pure makes it testable without a live graph).

    Raises :class:`EvidenceExtractionError` on:
      - LLM call failure (litellm exception, all-keys-cooled, etc.).
      - Response not parseable as JSON object.
      - Response shape wrong (missing ``evidences`` key).

    Returns an empty list (no error) when:
      - The attempt has no target responses to extract from.
      - The LLM returned a syntactically valid object with an empty
        ``evidences`` array — this is the "nothing meaningful happened"
        signal the prompt explicitly invites.
    """
    # Skip pipeline errors / empty exchanges — extractor would just
    # hallucinate. Cheap guard before paying for an LLM call.
    if not attempt.target_responses or all(not r.strip() for r in attempt.target_responses):
        return []

    user_content = EXTRACT_EVIDENCE_USER.format(
        hypotheses_block=_render_hypotheses_block(active_hypotheses),
        module=attempt.module,
        approach=attempt.approach,
        messages_block=_render_messages_block(attempt.messages_sent),
        responses_block=_render_responses_block(attempt.target_responses),
    )

    try:
        response = await ctx.completion(
            messages=[
                {"role": "system", "content": EXTRACT_EVIDENCE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            role=CompletionRole.JUDGE,
        )
    except Exception as e:  # noqa: BLE001 — boundary catch, re-raised typed
        raise EvidenceExtractionError(f"extractor LLM call failed: {e!s}", cause=e) from e

    raw_text = response.choices[0].message.content or ""
    parsed = parse_llm_json(raw_text, default=None)
    if not isinstance(parsed, dict):
        raise EvidenceExtractionError("extractor response was not a JSON object")
    rows = parsed.get("evidences")
    if rows is None:
        # Lenient fallback: if the model emitted a top-level array
        # despite the schema asking for {"evidences": [...]}, accept it.
        if isinstance(parsed, list):  # pragma: no cover — defensive
            rows = parsed
        else:
            raise EvidenceExtractionError("extractor response missing 'evidences' key")
    if not isinstance(rows, list):
        raise EvidenceExtractionError("extractor 'evidences' value was not a list")

    valid_ids = {h.id for h in active_hypotheses}
    out: list[Evidence] = []
    for row in rows[:_MAX_EVIDENCES_PER_CALL]:
        if not isinstance(row, dict):
            continue
        ev = _coerce_evidence_dict(
            row,
            valid_hypothesis_ids=valid_ids,
            from_attempt=attempt.id,
            run_id=attempt.run_id,
        )
        if ev is not None:
            out.append(ev)
    return out


__all__ = [
    "extract_evidence",
]
