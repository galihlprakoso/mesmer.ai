"""Tests for KeyPool rotation + cooldown, and compute_cooldown heuristics."""

import time

import pytest

from mesmer.core.keys import (
    KeyPool,
    KeyStatus,
    compute_cooldown,
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

    def test_round_robin_through_active_keys(self):
        p = KeyPool(["A", "B", "C"])
        # 3 keys, so we should cycle through all three
        seen = [p.next() for _ in range(3)]
        assert set(seen) == {"A", "B", "C"}

    def test_cool_down_skips_that_key(self):
        p = KeyPool(["A", "B"])
        p.cool_down("A", time.time() + 3600, reason="test")
        # Only B should come out — A is cooled
        for _ in range(5):
            assert p.next() == "B"

    def test_all_cooled_returns_none(self):
        p = KeyPool(["A", "B"])
        later = time.time() + 3600
        p.cool_down("A", later)
        p.cool_down("B", later)
        assert p.next() is None
        assert p.active_count() == 0

    def test_expired_cooldown_reactivates_key(self):
        p = KeyPool(["A"])
        # Cool down to a past timestamp → already expired → still active
        p.cool_down("A", time.time() - 10, reason="expired")
        assert p.next() == "A"
        assert p.active_count() == 1

    def test_cool_down_extends_never_shortens(self):
        """If a key is cooled until T1, cool_down(key, T0<T1) must not shorten it."""
        p = KeyPool(["A"])
        far = time.time() + 3600
        near = time.time() + 60
        p.cool_down("A", far, reason="far")
        p.cool_down("A", near, reason="near")
        # Still cooled
        assert p.next() is None
        status = [s for s in p.status() if s.masked]
        assert status[0].cooled_until >= far - 1

    def test_cool_down_unknown_key_noop(self):
        p = KeyPool(["A"])
        p.cool_down("Z", time.time() + 3600)  # not in pool
        assert p.next() == "A"

    def test_status_reports_cooled_and_reason(self):
        p = KeyPool(["A", "B"])
        p.cool_down("A", time.time() + 300, reason="rate_limit")
        statuses = p.status()
        assert len(statuses) == 2
        a = next(s for s in statuses if "A" in s.masked or s.masked == "***")
        assert a.cooled_until > time.time()
        assert a.reason == "rate_limit"

    def test_active_count(self):
        p = KeyPool(["A", "B", "C"])
        assert p.active_count() == 3
        p.cool_down("A", time.time() + 300)
        assert p.active_count() == 2
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
