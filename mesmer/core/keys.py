"""API key rotation — round-robin across multiple keys, with
per-key cooldown so a rate-limited key is skipped until its window expires."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"


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
    """
    Round-robin API key pool with per-key cooldown.

    When a key hits a rate-limit, call `cool_down(key, until_ts, reason)`.
    Subsequent `next()` calls skip that key until `until_ts` passes.

    Thread-safe.
    """

    def __init__(self, keys: list[str] | None = None):
        self._keys: list[str] = [k.strip() for k in (keys or []) if k.strip()]
        self._index = 0
        # key → unix-seconds until (0 / absent = active)
        self._cooldown: dict[str, float] = {}
        # key → reason for cooldown (for UI/log)
        self._reasons: dict[str, str] = {}
        self._lock = threading.Lock()

    # --- construction ---

    @classmethod
    def from_env(cls, env_multi: str = "OPENROUTER_API_KEYS", env_single: str = "OPENROUTER_API_KEY") -> "KeyPool":
        multi = os.environ.get(env_multi, "")
        if multi:
            return cls([k.strip() for k in multi.split(",") if k.strip()])
        single = os.environ.get(env_single, "")
        return cls([single] if single.strip() else [])

    # --- core rotation ---

    @property
    def total(self) -> int:
        return len(self._keys)

    @property
    def has_keys(self) -> bool:
        return bool(self._keys)

    def active_count(self) -> int:
        """Number of keys that are currently NOT in cooldown."""
        now = time.time()
        return sum(1 for k in self._keys if self._cooldown.get(k, 0.0) <= now)

    def next(self) -> str | None:
        """Return the next key whose cooldown has expired.

        Cycles through all keys once; returns None if every key is cooled down.
        """
        if not self._keys:
            return None
        now = time.time()
        with self._lock:
            n = len(self._keys)
            for _ in range(n):
                idx = self._index % n
                self._index += 1
                key = self._keys[idx]
                if self._cooldown.get(key, 0.0) <= now:
                    return key
            # All keys cooled
            return None

    def cool_down(self, key: str, until_ts: float, reason: str = "") -> None:
        """Mark `key` as cooled until `until_ts` (unix seconds)."""
        if not key or key not in self._keys:
            return
        with self._lock:
            # Never shorten an existing cooldown
            prior = self._cooldown.get(key, 0.0)
            self._cooldown[key] = max(prior, until_ts)
            if reason:
                self._reasons[key] = reason

    def clear_expired(self) -> None:
        """Drop expired cooldowns from the table. Purely for hygiene."""
        now = time.time()
        with self._lock:
            for k in list(self._cooldown.keys()):
                if self._cooldown[k] <= now:
                    del self._cooldown[k]
                    self._reasons.pop(k, None)

    def status(self) -> list[KeyStatus]:
        """Snapshot of each key's state (for UI display)."""
        now = time.time()
        out: list[KeyStatus] = []
        for k in self._keys:
            cooled = self._cooldown.get(k, 0.0)
            if cooled <= now:
                cooled = 0.0
            out.append(KeyStatus(
                masked=_mask(k),
                cooled_until=cooled,
                reason=self._reasons.get(k, "") if cooled else "",
            ))
        return out

    def all_masked(self) -> list[str]:
        return [_mask(k) for k in self._keys]


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
