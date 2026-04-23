"""Summary-buffer compressor for CONTINUOUS-mode conversations (C9).

The attacker LLM's prompt grows with every turn in continuous mode — against
a long-running target (especially one with account-level memory that mesmer
resumes across runs) the transcript will eventually overshoot the attacker
model's context window. We solve this the way LangChain's
``ConversationSummaryBufferMemory`` does, but without taking LangChain as a
dependency for one class:

  - Count current messages with ``litellm.token_counter``.
  - When over the declared cap, LLM-summarise the oldest turns into a single
    synthetic :attr:`TurnKind.SUMMARY` Turn via
    ``ctx.completion(role=CompletionRole.JUDGE)``.
  - Replace the compressed tail in ``ctx.turns`` in-place; the ``keep_recent``
    most-recent turns stay verbatim.
  - Summary turns stack: a later compression can itself compress prior
    summaries since they're just regular ``Turn`` objects with a different
    ``kind``.

TRIALS mode never calls this — compression is a continuous-mode concern.
The ``effective_max_context_tokens`` cap falling to 0 (no explicit cap AND
``litellm.get_max_tokens`` didn't resolve) disables compression entirely.

Failure policy: the LLM call and helpers raise :class:`CompressionLLMError`
on failure. :func:`maybe_compress` is the single catch boundary — it logs
the reason and returns False so the run continues with an uncompressed
transcript. No silent ``except Exception: return ""`` anywhere downstream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import litellm

from mesmer.core.agent.context import Turn
from mesmer.core.agent.prompts import SUMMARY_SYSTEM
from mesmer.core.constants import CompletionRole, LogEvent, ScenarioMode, TurnKind
from mesmer.core.errors import CompressionLLMError

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


# litellm's default INFO-level logging floods the CLI with HTTP debug lines
# once per completion. Turning it off is a module-import side effect — we
# do it exactly once here so individual call sites don't each retrigger it.
litellm.suppress_debug_info = True


def _build_summary_user_prompt(turns_to_compress: list[Turn]) -> str:
    """Render the Turn slice for the summary LLM. Includes existing summary
    turns as-is so nested compression can build on them rather than
    re-summarising the same material twice."""
    lines = [
        "Summarise the conversation transcript below. Keep the five priorities "
        "in your system prompt in mind.\n",
        "--- transcript ---",
    ]
    for t in turns_to_compress:
        if t.kind == TurnKind.SUMMARY:
            lines.append(f"[Earlier summary] {t.received}")
            continue
        prefix = f"[{t.module}] " if t.module else ""
        if t.sent:
            lines.append(f"{prefix}Attacker: {t.sent}")
        if t.received:
            tag = " (pipeline-error)" if t.is_error else ""
            lines.append(f"Target{tag}: {t.received}")
    lines.append("--- end transcript ---")
    return "\n".join(lines)


def _char_fallback(text: str) -> int:
    """Crude ~4-chars-per-token heuristic. Used when litellm can't resolve
    the model or returns a non-int (test doubles, novel provider strings).
    Guarantees a monotonically-increasing signal so compression still fires
    for genuinely-long transcripts even without a real tokenizer."""
    return max(1, len(text) // 4) if text else 0


def _count_tokens(model: str, text: str) -> int:
    """Best-effort token counter for raw text.

    ``litellm.token_counter`` raises for models it doesn't know how to
    tokenise (novel provider strings, test stubs). That's a measurement
    limitation, not a run-breaking error — we fall back to the character
    heuristic so compression still fires on genuinely-large transcripts.
    """
    try:
        n = litellm.token_counter(model=model, text=text)
    except Exception:
        return _char_fallback(text)
    if isinstance(n, int) and n > 0:
        return n
    return _char_fallback(text)


def _count_message_tokens(model: str, messages: list[dict]) -> int:
    """Token count for a full OpenAI-shaped messages payload.

    Same fallback semantics as :func:`_count_tokens` — unknown-model errors
    degrade to a character-based estimate over concatenated content so the
    caller still sees a monotonically-increasing signal.
    """
    try:
        n = litellm.token_counter(model=model, messages=messages)
    except Exception:
        text = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        return _char_fallback(text)
    if isinstance(n, int) and n > 0:
        return n
    text = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
    return _char_fallback(text)


def _estimate_turns_tokens(model: str, turns: list[Turn]) -> int:
    """Token count for the rendered transcript, used when building the
    pre-call estimate and after compression to verify we fit."""
    if not turns:
        return 0
    rendered: list[str] = []
    for t in turns:
        if t.kind == TurnKind.SUMMARY:
            rendered.append(f"[Summary] {t.received}")
            continue
        if t.sent:
            rendered.append(f"Attacker: {t.sent}")
        if t.received:
            rendered.append(f"Target: {t.received}")
    return _count_tokens(model, "\n".join(rendered))


async def _summarise_block(
    ctx: Context,
    turns_to_compress: list[Turn],
    explicit_compression_model: str,
) -> str:
    """One LLM call → summary text.

    Raises :class:`CompressionLLMError` when the call fails or returns
    empty content — :func:`maybe_compress` is the single catch boundary
    that translates that into a logged no-op.

    When ``explicit_compression_model`` is set (the operator wrote
    ``agent.compression_model`` in the scenario), we call litellm directly
    so the choice isn't overridden by the role-resolution pipeline. When
    it's empty we go through :meth:`Context.completion` with
    ``role=CompletionRole.JUDGE``
    — that hits the same retry/cooldown logic as the judge and honours the
    configured ``judge_model`` cascade.
    """
    prompt = _build_summary_user_prompt(turns_to_compress)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    if explicit_compression_model:
        return await _raw_completion(ctx, explicit_compression_model, messages)

    # No explicit override — route through the normal judge-role completion
    # so retries / key rotation / rate-limit handling all apply.
    prior = ctx.attacker_model_override
    ctx.attacker_model_override = ""  # ensure CompletionRole.JUDGE path wins
    try:
        response = await ctx.completion(messages=messages, role=CompletionRole.JUDGE)
    except Exception as exc:
        raise CompressionLLMError(
            f"judge-role completion failed: {exc}", cause=exc
        ) from exc
    finally:
        ctx.attacker_model_override = prior

    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise CompressionLLMError("judge-role completion returned empty content")
    return content


async def _raw_completion(ctx: Context, model: str, messages: list[dict]) -> str:
    """Direct litellm call with an explicit model — used when
    ``compression_model`` overrides both the attacker and judge picks.

    Pulls API key + base URL from the agent config so it behaves identically
    to ``ctx.completion`` minus the role-resolution logic. Raises
    :class:`CompressionLLMError` on failure or empty response; callers
    upstream translate that into a logged no-op.
    """
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": ctx.agent_config.temperature,
    }
    key = ctx.agent_config.next_key()
    if key:
        kwargs["api_key"] = key
    if ctx.agent_config.api_base:
        kwargs["api_base"] = ctx.agent_config.api_base

    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception as exc:
        raise CompressionLLMError(
            f"compression_model {model!r} call failed: {exc}", cause=exc
        ) from exc

    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise CompressionLLMError(
            f"compression_model {model!r} returned empty content"
        )
    return content


async def maybe_compress(
    ctx: Context,
    model: str,
    *,
    messages: list[dict] | None = None,
    log: LogFn | None = None,
) -> bool:
    """Compress ``ctx.turns`` in place when over the model's context budget.

    Call before building an attacker or judge LLM call in CONTINUOUS mode.
    No-op when:

      - ``ctx.scenario_mode != CONTINUOUS`` (trials mode has no single arc).
      - The resolved cap is 0 (no explicit config AND no ``litellm.get_max_tokens``
        lookup for this model).
      - Current token count is already within cap.
      - There are fewer turns than ``compression_keep_recent`` + 2 (nothing
        meaningful to compress).

    ``messages`` is optional — when passed, its token count dominates the
    decision so the caller gets compression that's aware of the full
    attacker prompt, not just the Turn list. Otherwise we fall back to
    counting rendered turns.

    Returns True if compression ran. A :class:`CompressionLLMError` from
    the summary LLM is caught here (compression is best-effort) and logged.
    """
    if ctx.scenario_mode != ScenarioMode.CONTINUOUS:
        return False

    agent = ctx.agent_config
    cap = agent.effective_max_context_tokens(model)
    if cap <= 0:
        return False

    keep_recent = max(1, int(agent.compression_keep_recent))
    turns = ctx.turns
    if len(turns) < keep_recent + 2:
        return False

    # Measure current size. Prefer the full messages payload when the caller
    # supplied it — that's what actually hits the provider.
    if messages is not None:
        current_tokens = _count_message_tokens(model, messages)
    else:
        current_tokens = _estimate_turns_tokens(model, turns)

    if current_tokens <= cap:
        return False

    # Split: old = everything before the verbatim tail.
    old = turns[:-keep_recent]
    tail = turns[-keep_recent:]

    # Defensive — the >= keep_recent+2 check above already rules this out.
    if not old:
        return False

    if log is not None:
        log(
            LogEvent.COMPRESSION.value,
            f"Compressing {len(old)} turns (tokens {current_tokens} > cap {cap}); "
            f"keeping last {len(tail)} verbatim.",
        )

    try:
        summary_text = await _summarise_block(
            ctx,
            old,
            explicit_compression_model=agent.compression_model,
        )
    except CompressionLLMError as exc:
        if log is not None:
            log(LogEvent.COMPRESSION.value, f"Compression aborted: {exc.reason}")
        return False

    summary_turn = Turn(
        sent="",
        received=summary_text,
        module="_summary_",
        kind=TurnKind.SUMMARY,
    )
    # Mutate the shared list in place so child contexts (which hold the same
    # reference via ``_turns=self.turns``) observe the compression too.
    ctx.turns[:] = [summary_turn, *tail]
    # After compression the whole remaining transcript counts as "current
    # session" from the target's POV — the summary encodes earlier rounds.
    ctx._target_reset_at = 0

    if log is not None:
        new_tokens = _estimate_turns_tokens(model, ctx.turns)
        log(
            LogEvent.COMPRESSION.value,
            f"Compressed OK: transcript now {len(ctx.turns)} turns, "
            f"~{new_tokens} tokens (cap {cap}).",
        )
    return True


__all__ = [
    "maybe_compress",
    "_build_summary_user_prompt",
    "_char_fallback",
    "_count_tokens",
    "_count_message_tokens",
    "_estimate_turns_tokens",
    "_summarise_block",
    "_raw_completion",
]
