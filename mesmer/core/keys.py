"""API key throttling helpers.

This module owns everything about managing a bag of API keys:

  * :class:`KeyPool` — a single configured API key plus optional throttle.
    Mesmer intentionally does not rotate across multiple provider keys.
  * :class:`ThrottleConfig` — declarative rate limiter attached to a pool.
    Caps requests-per-minute and in-flight concurrency, and — when
    ``max_wait_seconds > 0`` — blocks ``acquire()`` on saturation instead
    of failing fast. This is the fix for the silent 0-turn trials the
    bench harness was producing: when the pool wall is hit, either wait
    and recover or raise :class:`ThrottleTimeout` so the error surfaces
    in the trial's JSONL row rather than disappearing as "turns=0".
  * :func:`get_or_create_pool` — process-level cache keyed by the sorted
    tuple of API keys. Lets sibling bench trials that share the same keys
    share a single throttle.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from mesmer.core.constants import LogEvent
from mesmer.core.errors import ThrottleTimeout


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"


LogFn = Callable[[str, str], None]


def _noop_log(event: str, detail: str = "") -> None:
    """Fallback logger when the caller doesn't wire one in."""
    _ = event, detail


# ---------------------------------------------------------------------------
# Throttle — declarative rate limiter
# ---------------------------------------------------------------------------


@dataclass
class ThrottleConfig:
    """Declarative rate-limit policy for a :class:`KeyPool`.

    All fields opt-in. A default-constructed ThrottleConfig is a no-op:
    unlimited rpm, unlimited concurrency, fail-fast on cooldown wall
    (matching pre-throttle behaviour).

    :attr:`max_wait_seconds` collapses the "should we block or fail" axis
    into a single timeout field, matching the shape used by
    ``asyncio.wait_for``, ``threading.Lock.acquire``, and every
    mainstream rate-limiter library. ``0`` = fail-fast; ``>0`` = block up
    to N seconds per acquire attempt; exhausting the timeout raises
    :class:`ThrottleTimeout`.
    """

    max_rpm: int | None = None
    max_concurrent: int | None = None
    max_wait_seconds: float = 0.0

    def __post_init__(self) -> None:
        # Clamp nonsense values to the safe default rather than crashing
        # a run on a typoed YAML field.
        if self.max_rpm is not None and int(self.max_rpm) <= 0:
            self.max_rpm = None
        elif self.max_rpm is not None:
            self.max_rpm = int(self.max_rpm)

        if self.max_concurrent is not None and int(self.max_concurrent) <= 0:
            self.max_concurrent = None
        elif self.max_concurrent is not None:
            self.max_concurrent = int(self.max_concurrent)

        try:
            self.max_wait_seconds = max(0.0, float(self.max_wait_seconds))
        except (TypeError, ValueError):
            self.max_wait_seconds = 0.0

    @property
    def is_active(self) -> bool:
        """True iff any throttle constraint or wait policy is set."""
        return (
            self.max_rpm is not None
            or self.max_concurrent is not None
            or self.max_wait_seconds > 0
        )


@dataclass
class KeyStatus:
    """Snapshot of one key's state for UI/logging."""

    masked: str
    cooled_until: float  # unix seconds; 0 if not cooled
    reason: str

    @property
    def is_cooled(self) -> bool:
        return self.cooled_until > time.time()


class KeyPool:
    """Single API key holder with optional throttle.

    The object intentionally does not rotate across keys. It exists so the
    runner can share one throttle across concurrent calls that use the same
    configured key.

      * ``acquire()`` enforces
        :attr:`ThrottleConfig.max_concurrent` (via :class:`asyncio.Semaphore`)
        and :attr:`ThrottleConfig.max_rpm` (via a 60-second sliding window)
        before a completion call goes out. A timeout raises
        :class:`ThrottleTimeout`; :meth:`mesmer.core.runner.execute_run`
        catches it into an ``"Error: ..."`` result that the bench
        orchestrator surfaces into the trial's JSONL ``error`` field.
    """

    def __init__(
        self,
        keys: list[str] | None = None,
        *,
        throttle: ThrottleConfig | None = None,
    ):
        cleaned = [k.strip() for k in (keys or []) if k.strip()]
        self._keys: list[str] = cleaned[:1]
        self._lock = threading.Lock()

        # Throttle state. A None throttle disables throttling entirely
        # (preserves legacy behaviour for callers that never configured one).
        self._throttle: ThrottleConfig = throttle or ThrottleConfig()
        # Semaphore lazily bound to an event loop on first acquire() — must
        # live on the loop that runs the completion, so we defer creation.
        self._sem: asyncio.Semaphore | None = None
        # Sliding-window log of recent request start-times (monotonic).
        self._request_times: deque[float] = deque()
        # Serialises rpm decisions. Created alongside _sem.
        self._rpm_lock: asyncio.Lock | None = None

    # --- construction ---

    @classmethod
    def from_env(
        cls,
        env_multi: str = "OPENROUTER_API_KEYS",
        env_single: str = "OPENROUTER_API_KEY",
        *,
        throttle: ThrottleConfig | None = None,
    ) -> "KeyPool":
        single = os.environ.get(env_single, "")
        if not single:
            # Legacy compatibility: if an old multi-key env var exists, use
            # only the first value. Do not create a rotating pool.
            single = os.environ.get(env_multi, "").split(",", 1)[0]
        return cls([single] if single.strip() else [], throttle=throttle)

    # --- core rotation ---

    @property
    def total(self) -> int:
        return len(self._keys)

    @property
    def has_keys(self) -> bool:
        return bool(self._keys)

    @property
    def throttle(self) -> ThrottleConfig:
        return self._throttle

    def active_count(self) -> int:
        """Number of configured keys. Kept for UI compatibility."""
        return len(self._keys)

    def next(self) -> str | None:
        """Return the single configured key, if any."""
        return self._keys[0] if self._keys else None

    def cool_down(self, key: str, until_ts: float, reason: str = "") -> None:
        """Deprecated no-op. API key cooldown/rotation is disabled."""
        _ = key, until_ts, reason

    def clear_expired(self) -> None:
        """Deprecated no-op."""

    def earliest_cooldown(self) -> float:
        """Deprecated compatibility hook; cooldowns are disabled."""
        return 0.0

    def status(self) -> list[KeyStatus]:
        """Snapshot of each key's state (for UI display)."""
        out: list[KeyStatus] = []
        for k in self._keys:
            out.append(KeyStatus(
                masked=_mask(k),
                cooled_until=0.0,
                reason="",
            ))
        return out

    def all_masked(self) -> list[str]:
        return [_mask(k) for k in self._keys]

    # --- throttle (async) ---

    def _ensure_async_primitives(self) -> None:
        """Create the semaphore + rpm-lock lazily on the current event loop.

        Done on first acquire() rather than in __init__ so the pool can
        be constructed outside an event loop (e.g. on module import) and
        still bind cleanly when a completion eventually runs.
        """
        if self._rpm_lock is None:
            self._rpm_lock = asyncio.Lock()
        if self._sem is None and self._throttle.max_concurrent is not None:
            self._sem = asyncio.Semaphore(self._throttle.max_concurrent)

    async def acquire(self, log: LogFn | None = None) -> None:
        """Block until all throttle gates permit the caller to proceed.

        Gates, in order:
          1. :attr:`ThrottleConfig.max_concurrent` — asyncio.Semaphore.
          2. :attr:`ThrottleConfig.max_rpm` — sliding 60s window.
          3. Cooldown wall — if every key is cooled, wait for the earliest
             expiry.

        ``max_wait_seconds == 0`` is fail-fast on every gate: any gate that
        would require waiting raises :class:`ThrottleTimeout` immediately.

        ``max_wait_seconds > 0`` is the per-gate wait budget. Exceeding it
        at any gate raises :class:`ThrottleTimeout(gate=...)`. The budget
        is per-gate by design — a fully-saturated pool (rpm AND cooldown)
        shouldn't silently double-spend one operator-declared timeout.

        Call :meth:`release` in a ``finally`` after the completion call to
        return the concurrency slot. When :attr:`max_concurrent` is None,
        :meth:`release` is a no-op.
        """
        logger = log or _noop_log
        self._ensure_async_primitives()
        cfg = self._throttle

        # 1. Concurrency cap
        if self._sem is not None:
            t0 = time.monotonic()
            timeout = cfg.max_wait_seconds
            if timeout > 0:
                if self._sem.locked():
                    logger(
                        LogEvent.THROTTLE_WAIT.value,
                        "concurrency cap reached; waiting for an available slot",
                    )
                try:
                    await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
                except asyncio.TimeoutError:
                    raise ThrottleTimeout(
                        "max_concurrent",
                        waited_s=time.monotonic() - t0,
                    )
            else:
                if self._sem.locked():
                    raise ThrottleTimeout("max_concurrent", waited_s=0.0)
                await self._sem.acquire()

        # From here on, we hold a concurrency slot — release it on any
        # raised ThrottleTimeout so we don't leak slots on saturation.
        try:
            # 2. RPM cap
            if cfg.max_rpm is not None:
                assert self._rpm_lock is not None  # ensured above
                await self._rpm_gate(cfg, logger)

        except ThrottleTimeout:
            self.release()
            raise

    async def _rpm_gate(self, cfg: ThrottleConfig, log: LogFn) -> None:
        """Enforce the requests-per-minute cap via a 60s sliding window.

        Purges timestamps older than 60s each entry, and if the window is
        full computes how long until the oldest entry ages out. That wait
        is compared against ``cfg.max_wait_seconds`` — fail-fast when
        the budget is 0, timeout when the budget is exceeded, otherwise
        sleep the required delta and record our entry.
        """
        assert self._rpm_lock is not None
        assert cfg.max_rpm is not None
        t0 = time.monotonic()
        async with self._rpm_lock:
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60.0:
                self._request_times.popleft()
            if len(self._request_times) < cfg.max_rpm:
                self._request_times.append(now)
                return

            # Window full — compute wait until the oldest slot expires.
            wait = 60.0 - (now - self._request_times[0])
            if cfg.max_wait_seconds <= 0 or wait > cfg.max_wait_seconds:
                raise ThrottleTimeout(
                    "max_rpm",
                    waited_s=time.monotonic() - t0,
                )
            log(
                LogEvent.THROTTLE_WAIT.value,
                f"rpm cap reached ({cfg.max_rpm}/min); waiting {wait:.2f}s",
            )
            await asyncio.sleep(wait)
            # Post-sleep, prune again and append.
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60.0:
                self._request_times.popleft()
            self._request_times.append(now)

    def release(self) -> None:
        """Return a concurrency slot taken by :meth:`acquire`.

        No-op when :attr:`ThrottleConfig.max_concurrent` is unset.
        Standard semaphore contract: call exactly once per successful
        :meth:`acquire`. A raising ``acquire`` already releases internally
        before propagating the error, so callers should pair via
        ``await acquire(); try: ... finally: release()``.
        """
        if self._sem is not None:
            self._sem.release()


# ---------------------------------------------------------------------------
# Process-level pool cache
# ---------------------------------------------------------------------------
#
# The bench harness builds a new AgentConfig — and thus a new KeyPool —
# per trial. Without sharing, a ``max_concurrent=2`` throttle would yield
# 2 slots *per trial*, meaning concurrency=8 at the bench layer produces
# 16 parallel completions against the provider. The cache collapses all
# AgentConfigs that carry the same key set onto one pool, so the throttle
# caps are global to the key set rather than per-scenario.
#
# Keyed by the single configured key (not an arbitrary scenario ID) because
# that's what the rate-limit actually applies to.

_POOL_CACHE: dict[tuple[str, ...], KeyPool] = {}
_POOL_CACHE_LOCK = threading.Lock()


def get_or_create_pool(
    keys: list[str],
    throttle: ThrottleConfig | None = None,
) -> KeyPool:
    """Return the shared :class:`KeyPool` for this key set, creating one if needed.

    First caller wins on throttle configuration — subsequent callers that
    supply a different ``throttle`` see it ignored. This is intentional:
    the pool represents a provider quota, and once a process has declared
    "these keys are capped at N rpm" the cap shouldn't silently change
    under it. Operators who need a fresh configuration call
    :func:`clear_pool_cache` first.

    Empty key lists bypass the cache — returns a fresh pool since every
    empty pool is equivalent and caching them only wastes dict entries.
    """
    first_key = next((k.strip() for k in keys if k and k.strip()), "")
    key_tuple = (first_key,) if first_key else ()
    if not key_tuple:
        return KeyPool([], throttle=throttle)
    with _POOL_CACHE_LOCK:
        pool = _POOL_CACHE.get(key_tuple)
        if pool is None:
            pool = KeyPool(list(key_tuple), throttle=throttle)
            _POOL_CACHE[key_tuple] = pool
        return pool


def clear_pool_cache() -> None:
    """Drop the process-level pool cache. Test hook."""
    with _POOL_CACHE_LOCK:
        _POOL_CACHE.clear()


# ---------------------------------------------------------------------------
# Cooldown computation — extracted so it can be unit-tested independently.
# ---------------------------------------------------------------------------

def next_utc_midnight(now: float | None = None) -> float:
    """Return the unix-seconds timestamp for the next UTC midnight."""
    import datetime
    now = now or time.time()
    dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    tomorrow = (dt + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tomorrow.timestamp()


def compute_cooldown(error_message: str, now: float | None = None) -> tuple[float, str]:
    """Decide how long to cool down a key given a rate-limit error message.

    Returns (until_ts, reason). Rules:
      1. If the error string contains an `X-RateLimit-Reset` value (unix ms),
         use that (converted to seconds).
      2. Else if the error mentions 'per-day' or 'daily', cool until next UTC midnight.
      3. Else, 60 seconds from now.
    """
    now = now or time.time()
    msg = error_message or ""

    # Pattern 1: X-RateLimit-Reset header
    import re
    m = re.search(r'X-RateLimit-Reset["\']?\s*[:=]\s*["\']?(\d{10,16})', msg)
    if m:
        raw = int(m.group(1))
        # Heuristic: values > 10^12 are ms since epoch; smaller are seconds
        secs = raw / 1000.0 if raw > 10**12 else float(raw)
        # Guard against past or absurd times
        if secs > now and secs < now + 14 * 24 * 3600:
            return secs, "parsed-reset"

    # Pattern 2: per-day / daily quota → next UTC midnight
    if "per-day" in msg.lower() or "per day" in msg.lower() or "daily" in msg.lower():
        return next_utc_midnight(now), "per-day"

    # Pattern 3: generic
    return now + 60.0, "generic-60s"
