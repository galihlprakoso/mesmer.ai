"""LLM retry, per-key cooldown, and throttle integration.

``_completion_with_retry`` is the single wrapper around ``ctx.completion``.
Three layered concerns live here:

  * **Throttle**: before each call, ``pool.acquire()`` blocks on the
    declarative rpm / concurrency caps and — when all keys are cooled —
    waits up to :attr:`ThrottleConfig.max_wait_seconds` for a key to
    recover. Exceeding that budget raises :class:`ThrottleTimeout`; we
    let it propagate so :meth:`mesmer.core.runner.execute_run` catches
    it into a ``result = "Error: ..."`` line that the bench orchestrator
    surfaces into the trial's JSONL ``error`` field. This closes the
    silent 0-turn trial loophole that hid the previous rate-limit wall.
  * **Rate-limit**: on a 429 from the provider, cool the specific key
    via ``KeyPool.cool_down`` and rotate — no sleep on a dead key.
  * **Transient errors** (provider/5xx/timeout/overloaded): backoff with
    ``asyncio.sleep(RETRY_DELAYS[attempt])`` on the same key.

``RETRY_DELAYS`` is re-exported from ``mesmer.core.agent.__init__`` so tests
can monkey-patch it via ``agent_mod.RETRY_DELAYS = [0, 0, 0]``. The late
``from mesmer.core.agent import RETRY_DELAYS`` inside ``_completion_with_retry``
is intentional — it resolves through the package namespace each call, so the
test patch takes effect without needing to thread the value through kwargs.
"""

from __future__ import annotations

import asyncio

from mesmer.core.constants import (
    MAX_LLM_RETRIES,
    LogEvent,
)


def _is_rate_limit_error(err_str: str) -> bool:
    """Heuristic: does this exception string look like a rate-limit error?"""
    s = err_str.lower()
    return "ratelimit" in s or "rate limit" in s or "429" in s


def _cool_down_key_for(ctx, err_str: str, log) -> None:
    """Cool down the key that was just used if we have a pool and the error
    looks rate-limited. Logs a `key_cooled` event."""
    pool = ctx.agent_config.pool
    key = ctx._last_key_used or ""
    if pool is None or not key:
        return
    from mesmer.core.keys import compute_cooldown, _mask
    import datetime
    until_ts, reason = compute_cooldown(err_str)
    pool.cool_down(key, until_ts, reason=reason)
    until_iso = datetime.datetime.fromtimestamp(
        until_ts, tz=datetime.timezone.utc
    ).isoformat()
    log(
        LogEvent.KEY_COOLED.value,
        f"key {_mask(key)} cooled until {until_iso} ({reason}); "
        f"active {pool.active_count()}/{pool.total}"
    )


async def _completion_with_retry(ctx, messages, tools, log):
    """Call ctx.completion with retry on transient provider errors.

    Wraps each attempt in the pool's throttle gate — ``pool.acquire()``
    blocks on rpm / concurrency / cooldown-wall constraints and raises
    :class:`ThrottleTimeout` when the :attr:`max_wait_seconds` budget is
    exceeded. We let that raise propagate so the error surfaces into the
    run's result string rather than returning None (which would mask it
    as a silent 0-turn trial — the bug that motivated this throttle).

    On rate-limit errors, cool down the specific key that was used and
    rotate. The next iteration's ``acquire()`` may then have to wait for
    the remaining keys; that wait is bounded by the same throttle budget.
    """
    # Late-bind RETRY_DELAYS via the package namespace so tests that
    # monkey-patch ``mesmer.core.agent.RETRY_DELAYS`` take effect.
    from mesmer.core.agent import RETRY_DELAYS

    pool = ctx.agent_config.pool

    for attempt in range(MAX_LLM_RETRIES):
        # Throttle gate. No try/except around acquire — a raised
        # ThrottleTimeout must propagate up so execute_run's handler turns
        # it into an "Error: ..." result (which bench then surfaces).
        if pool is not None and pool.throttle.is_active:
            await pool.acquire(log)
        try:
            try:
                response = await ctx.completion(messages=messages, tools=tools)
                # Some providers (notably Gemini) ship a 200 OK with empty
                # `choices` when the request hits a safety filter, content
                # block, or transient generation hiccup — completion_tokens=0,
                # no message, no tool_calls. LiteLLM passes that through as
                # a structurally-valid response so the exception path below
                # never fires. Treat it as a transient failure and retry on
                # the same key with backoff. If the retry budget runs out,
                # fall through to the LLM_ERROR / None return path below
                # exactly like any other exhausted retry.
                if not response.choices:
                    if attempt < MAX_LLM_RETRIES - 1:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        log(
                            LogEvent.LLM_RETRY.value,
                            f"Empty choices in response (attempt {attempt + 1}/"
                            f"{MAX_LLM_RETRIES}) — likely a safety block or "
                            f"provider blip; retrying in {delay}s...",
                        )
                        await asyncio.sleep(delay)
                        continue
                    log(LogEvent.LLM_ERROR.value, "Empty choices after max retries")
                    return None
                return response
            except Exception as e:
                err_str = str(e)

                # Rate-limit: cool the key and try the next one (no sleep).
                if _is_rate_limit_error(err_str):
                    _cool_down_key_for(ctx, err_str, log)
                    if pool is not None and pool.active_count() == 0:
                        # All keys cooled AND the caller either didn't
                        # configure a throttle wait or already exhausted
                        # it at acquire() above. Emit the wall signal so
                        # the CLI log is legible, then fall through to
                        # LLM_ERROR on the next iteration (or let the
                        # throttle block the next acquire()).
                        if not pool.throttle.is_active:
                            log(
                                LogEvent.RATE_LIMIT_WALL.value,
                                "all API keys are cooled down; stopping",
                            )
                            return None
                    if attempt < MAX_LLM_RETRIES - 1:
                        log(
                            LogEvent.LLM_RETRY.value,
                            f"Rate limit on current key (attempt {attempt + 1}/{MAX_LLM_RETRIES}): "
                            f"{err_str[:100]} — switching key and retrying"
                        )
                        continue
                    log(LogEvent.LLM_ERROR.value, f"Max retries on rate-limit: {err_str}")
                    return None

                # Other transient errors: backoff on the same key
                is_transient = any(k in err_str.lower() for k in (
                    "provider", "timeout", "500", "502", "503",
                    "overloaded", "capacity", "temporarily", "retry",
                ))
                if is_transient and attempt < MAX_LLM_RETRIES - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    log(LogEvent.LLM_RETRY.value, f"Transient error (attempt {attempt + 1}/{MAX_LLM_RETRIES}): {err_str[:100]} — retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                log(LogEvent.LLM_ERROR.value, f"{'Non-transient' if not is_transient else 'Max retries'}: {err_str}")
                return None
        finally:
            if pool is not None and pool.throttle.is_active:
                pool.release()
    return None


__all__ = ["_is_rate_limit_error", "_cool_down_key_for", "_completion_with_retry"]
