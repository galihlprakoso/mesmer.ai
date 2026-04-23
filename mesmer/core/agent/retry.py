"""LLM retry + per-key cooldown integration.

``_completion_with_retry`` is the single wrapper around ``ctx.completion``.
On rate-limit errors it cools the specific key that was just used (via
``KeyPool.cool_down``) and rotates — no sleep on a dead key. Transient errors
(provider/5xx/timeout/overloaded) use ``asyncio.sleep(RETRY_DELAYS[attempt])``
backoff. Everything else returns ``None`` so the loop can emit a clean failure
result.

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
    pool = getattr(ctx.agent_config, "pool", None)
    key = getattr(ctx, "_last_key_used", "") or ""
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

    On rate-limit errors, cool down the specific key that was used and
    immediately rotate — no need to sleep on a dead key.
    """
    # Late-bind RETRY_DELAYS via the package namespace so tests that
    # monkey-patch ``mesmer.core.agent.RETRY_DELAYS`` take effect.
    from mesmer.core.agent import RETRY_DELAYS

    for attempt in range(MAX_LLM_RETRIES):
        try:
            return await ctx.completion(messages=messages, tools=tools)
        except Exception as e:
            err_str = str(e)

            # Rate-limit: cool the key and try the next one (no sleep).
            if _is_rate_limit_error(err_str):
                _cool_down_key_for(ctx, err_str, log)
                pool = getattr(ctx.agent_config, "pool", None)
                if pool is not None and pool.active_count() == 0:
                    log(LogEvent.RATE_LIMIT_WALL.value, "all API keys are cooled down; stopping")
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
    return None


__all__ = ["_is_rate_limit_error", "_cool_down_key_for", "_completion_with_retry"]
