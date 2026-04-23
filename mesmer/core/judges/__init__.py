"""Deterministic judge implementations — reproducible scoring for benchmarks.

Unlike :mod:`mesmer.core.agent.judge` (which is LLM-based and runs mid-loop),
these judges are pure functions over a run's post-hoc artifacts. They have
no randomness and no LLM calls — perfect for benchmarks that need
reproducible numbers.
"""

from mesmer.core.judges.canary import (
    CanaryJudgeResult,
    find_canary_in_turns,
    scan_canary,
)

__all__ = ["CanaryJudgeResult", "find_canary_in_turns", "scan_canary"]
