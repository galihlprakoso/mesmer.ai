"""Tests for single-key KeyPool throttling and compute_cooldown heuristics."""

import asyncio
import time

import pytest

from mesmer.core.errors import ThrottleTimeout
from mesmer.core.keys import (
    KeyPool,
    ThrottleConfig,
    clear_pool_cache,
    compute_cooldown,
    get_or_create_pool,
    next_utc_midnight,
)


# ---------------------------------------------------------------------------
# KeyPool
# ---------------------------------------------------------------------------


class TestKeyPool:
    def test_empty_pool(self):
        p = KeyPool([])
        assert p.total == 0
        assert not p.has_keys
        assert p.next() is None
        assert p.active_count() == 0

    def test_uses_first_key_only(self):
        p = KeyPool(["A", "B", "C"])
        seen = [p.next() for _ in range(3)]
        assert seen == ["A", "A", "A"]
        assert p.total == 1

    def test_cool_down_is_noop(self):
        p = KeyPool(["A", "B"])
        p.cool_down("A", time.time() + 3600, reason="test")
        for _ in range(5):
            assert p.next() == "A"
        assert p.active_count() == 1

    def test_cool_down_unknown_key_noop(self):
        p = KeyPool(["A"])
        p.cool_down("Z", time.time() + 3600)  # not in pool
        assert p.next() == "A"

    def test_status_reports_cooled_and_reason(self):
        p = KeyPool(["A", "B"])
        p.cool_down("A", time.time() + 300, reason="rate_limit")
        statuses = p.status()
        assert len(statuses) == 1
        a = next(s for s in statuses if "A" in s.masked or s.masked == "***")
        assert a.cooled_until == 0
        assert a.reason == ""

    def test_active_count(self):
        p = KeyPool(["A", "B", "C"])
        assert p.active_count() == 1
        p.cool_down("A", time.time() + 300)
        assert p.active_count() == 1
        p.cool_down("B", time.time() + 300)
        assert p.active_count() == 1


# ---------------------------------------------------------------------------
# compute_cooldown — the heuristic for deciding cooldown duration
# ---------------------------------------------------------------------------


class TestComputeCooldown:
    def test_parses_x_ratelimit_reset_ms(self):
        future_ms = int((time.time() + 3600) * 1000)
        err = f'{{"error":{{"metadata":{{"headers":{{"X-RateLimit-Reset":"{future_ms}"}}}}}}}}'
        until, reason = compute_cooldown(err)
        assert reason == "parsed-reset"
        # Within a small tolerance of future_ms / 1000
        assert abs(until - future_ms / 1000) < 1

    def test_parses_x_ratelimit_reset_seconds(self):
        future_s = int(time.time() + 3600)
        err = f'"X-RateLimit-Reset":"{future_s}"'
        until, reason = compute_cooldown(err)
        # Small value treated as seconds
        assert reason == "parsed-reset"
        assert abs(until - future_s) < 1

    def test_ignores_past_or_absurd_reset_value(self):
        # Past timestamp → must fall through to default handling
        past_ms = int((time.time() - 10_000) * 1000)
        err = f'"X-RateLimit-Reset":"{past_ms}"; per-day limit hit'
        until, reason = compute_cooldown(err)
        assert reason == "per-day"  # fell through to per-day
        assert until > time.time()

    def test_per_day_error_cools_to_next_utc_midnight(self):
        err = "Rate limit exceeded: free-models-per-day"
        now = time.time()
        until, reason = compute_cooldown(err, now=now)
        assert reason == "per-day"
        assert until == next_utc_midnight(now)

    def test_daily_synonym(self):
        err = "daily quota exceeded"
        until, reason = compute_cooldown(err)
        assert reason == "per-day"

    def test_generic_60s_default(self):
        now = time.time()
        err = "rate limit"  # no reset header, no per-day
        until, reason = compute_cooldown(err, now=now)
        assert reason == "generic-60s"
        assert 55 <= (until - now) <= 65


class TestNextUtcMidnight:
    def test_is_in_future(self):
        now = time.time()
        m = next_utc_midnight(now)
        assert m > now
        # And no more than 24h + 1h ahead (handles edge cases near midnight)
        assert m - now <= 25 * 3600


# ---------------------------------------------------------------------------
# ThrottleConfig — input clamping
# ---------------------------------------------------------------------------


class TestThrottleConfig:
    def test_default_is_inactive_no_op(self):
        """A default-constructed throttle imposes no gates — legacy behaviour."""
        cfg = ThrottleConfig()
        assert cfg.max_rpm is None
        assert cfg.max_concurrent is None
        assert cfg.max_wait_seconds == 0.0
        assert cfg.is_active is False

    def test_clamps_non_positive_caps_to_none(self):
        """Zero or negative caps degrade to None — a typoed YAML shouldn't crash."""
        cfg = ThrottleConfig(max_rpm=0, max_concurrent=-5, max_wait_seconds=-1)
        assert cfg.max_rpm is None
        assert cfg.max_concurrent is None
        assert cfg.max_wait_seconds == 0.0
        assert cfg.is_active is False

    def test_any_positive_cap_is_active(self):
        """is_active flips on for any single non-trivial field."""
        assert ThrottleConfig(max_rpm=60).is_active
        assert ThrottleConfig(max_concurrent=2).is_active
        assert ThrottleConfig(max_wait_seconds=10).is_active


# ---------------------------------------------------------------------------
# KeyPool.acquire — throttle gates
# ---------------------------------------------------------------------------


class TestKeyPoolAcquire:
    @pytest.mark.asyncio
    async def test_no_throttle_acquire_is_passthrough(self):
        """Pool without throttle: acquire/release are cheap and never block."""
        p = KeyPool(["A"])
        await p.acquire()
        p.release()

    @pytest.mark.asyncio
    async def test_concurrent_cap_blocks_over_budget(self):
        """max_concurrent=1 with max_wait=0 raises on the second acquire."""
        p = KeyPool(["A"], throttle=ThrottleConfig(max_concurrent=1))
        await p.acquire()
        with pytest.raises(ThrottleTimeout, match="max_concurrent"):
            await p.acquire()
        p.release()

    @pytest.mark.asyncio
    async def test_concurrent_cap_waits_when_budget_allows(self):
        """max_wait_seconds > 0: second acquire blocks until the first releases."""
        p = KeyPool(["A"], throttle=ThrottleConfig(
            max_concurrent=1, max_wait_seconds=1.0,
        ))
        await p.acquire()

        async def second_acquire():
            await p.acquire()
            p.release()

        task = asyncio.create_task(second_acquire())
        await asyncio.sleep(0.05)  # let the task hit the semaphore
        assert not task.done()
        p.release()
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_rpm_cap_fail_fast(self):
        """max_rpm with max_wait=0: second call in the same window raises."""
        p = KeyPool(["A"], throttle=ThrottleConfig(max_rpm=1))
        await p.acquire()
        p.release()
        with pytest.raises(ThrottleTimeout, match="max_rpm"):
            await p.acquire()

    @pytest.mark.asyncio
    async def test_gate_raise_releases_concurrency_slot(self):
        """If a later gate raises, the concurrency slot taken earlier is freed.

        Regression guard — if acquire() leaked slots on partial failure,
        the pool would deadlock after one 429 burst.
        """
        p = KeyPool(["A"], throttle=ThrottleConfig(
            max_concurrent=1, max_rpm=1,
        ))
        # Burn the rpm window with one successful acquire.
        await p.acquire()
        p.release()
        # Second call should fail at rpm gate, but NOT hold the concurrency slot.
        with pytest.raises(ThrottleTimeout, match="max_rpm"):
            await p.acquire()
        # If the slot leaked, this would hang/raise max_concurrent instead.
        # Wait out the rpm window by fast-forwarding the deque.
        p._request_times.clear()
        await p.acquire()
        p.release()


# ---------------------------------------------------------------------------
# Process-level pool cache
# ---------------------------------------------------------------------------


class TestPoolCache:
    def setup_method(self):
        clear_pool_cache()

    def teardown_method(self):
        clear_pool_cache()

    def test_same_key_shares_one_pool(self):
        """Sibling AgentConfigs with the same key share throttle state."""
        a = get_or_create_pool(["sk-123"])
        b = get_or_create_pool(["sk-123"])
        assert a is b

    def test_only_first_key_is_used_for_cache_identity(self):
        a = get_or_create_pool(["sk-123", "sk-456"])
        b = get_or_create_pool(["sk-123"])
        assert a is b

    def test_different_keys_produce_different_pools(self):
        a = get_or_create_pool(["sk-aaa"])
        b = get_or_create_pool(["sk-bbb"])
        assert a is not b

    def test_empty_keys_bypass_cache(self):
        """Empty pools aren't cached — no point conflating process-wide no-ops."""
        a = get_or_create_pool([])
        b = get_or_create_pool([])
        assert a is not b

    def test_first_caller_wins_on_throttle(self):
        """Cache is pool identity, not config — later throttles are ignored.

        The process-wide rate cap represents provider quota; once declared
        it shouldn't silently change when a sibling scenario inherits it.
        Callers that need a different throttle call :func:`clear_pool_cache`.
        """
        a = get_or_create_pool(
            ["sk-x"], throttle=ThrottleConfig(max_rpm=10),
        )
        b = get_or_create_pool(
            ["sk-x"], throttle=ThrottleConfig(max_rpm=999),
        )
        assert a is b
        assert b.throttle.max_rpm == 10  # first-wins

    def test_clear_pool_cache_restores_fresh_state(self):
        a = get_or_create_pool(["sk-y"])
        clear_pool_cache()
        b = get_or_create_pool(["sk-y"])
        assert a is not b
