"""Shared exception types for mesmer.

All mesmer-raised errors derive from :class:`MesmerError`. This gives the
CLI / web layer a single ``except MesmerError`` handler that catches every
known failure mode while still letting unexpected bugs surface as raw
Python exceptions (the sign that something *unknown* blew up).

Keep this module dependency-free — every core/agent/target module should be
able to import from here without pulling in heavy submodules.
"""

from __future__ import annotations


class MesmerError(Exception):
    """Base class for all mesmer-specific errors."""


# --- Budget / loop control ---

class TurnBudgetExhausted(MesmerError):
    """Raised when a module exceeds its turn budget.

    Not an "error" in the failure sense — the ReAct engine catches this
    to terminate the module cleanly and record turns_used.
    """

    def __init__(self, turns_used: int):
        self.turns_used = turns_used
        super().__init__(f"Turn budget exhausted after {turns_used} turns")


# --- Human-in-the-loop ---

class HumanQuestionTimeout(MesmerError):
    """Raised when a human doesn't answer a co-op ``ask_human`` question
    within the broker's timeout window."""


# --- CONTINUOUS-mode compression (C9) ---

class CompressionError(MesmerError):
    """Base class for compressor failures.

    Callers at the compression boundary (:func:`maybe_compress`) catch
    this, log it, and continue the run with an uncompressed transcript —
    compression is best-effort.
    """


class CompressionLLMError(CompressionError):
    """The compression LLM call failed or returned unusable output.

    ``reason`` carries a short human-readable explanation; ``cause`` is
    the underlying exception when one was raised by litellm (None when
    the call returned empty content).
    """

    def __init__(self, reason: str, *, cause: BaseException | None = None):
        self.reason = reason
        self.cause = cause
        super().__init__(reason)


# --- Module configuration ---

class InvalidModuleConfig(MesmerError):
    """Raised when a ``module.yaml`` declares a field outside its allowed range.

    Carries ``module_name``, ``field``, and ``value`` so the loader can point
    the operator at the exact misconfigured file. Currently emitted for
    ``tier`` values outside 0..3 — the tier contract documented in
    :class:`ModuleConfig`.
    """

    def __init__(self, module_name: str, field: str, value: object, *, reason: str = ""):
        self.module_name = module_name
        self.field = field
        self.value = value
        self.reason = reason
        msg = f"invalid {field}={value!r} on module {module_name!r}"
        if reason:
            msg = f"{msg}: {reason}"
        super().__init__(msg)


# --- Throttle (keys.py) ---

class ThrottleTimeout(MesmerError):
    """Raised when ``KeyPool.acquire`` cannot obtain a slot within the
    throttle's ``max_wait_seconds`` budget.

    Carries ``gate`` — which constraint blocked us (``"max_concurrent"``,
    ``"max_rpm"``) — so ``execute_run``'s error
    surfacing gives operators an actionable message instead of a silent
    0-turn trial.
    """

    def __init__(self, gate: str, *, waited_s: float = 0.0):
        self.gate = gate
        self.waited_s = waited_s
        super().__init__(f"throttle timeout at gate={gate} after {waited_s:.1f}s")


# --- Belief Attack Graph (Session 1) ---

class BeliefGraphError(MesmerError):
    """Base for failures inside :mod:`mesmer.core.belief_graph` and the
    extractor / updater pipeline that feeds it."""


class InvalidDelta(BeliefGraphError):
    """A :class:`mesmer.core.belief_graph.GraphDelta` failed validation
    when applied — references a node that doesn't exist, declares an
    unknown enum value, or violates the edge-endpoint contract.

    Carries the delta ``kind`` and a one-line ``reason`` so the boundary
    catch (typically the engine's per-iteration update) can log
    actionable context. The whole delta payload is intentionally NOT
    captured here — it can be huge (full attempt transcripts) and
    ``logger.exception`` would dump it. Callers that need the full
    payload should hold the delta object alongside the raise site.
    """

    def __init__(self, kind: str, reason: str):
        self.kind = kind
        self.reason = reason
        super().__init__(f"invalid belief-graph delta kind={kind!r}: {reason}")


class EvidenceExtractionError(BeliefGraphError):
    """The evidence extractor's LLM call failed or returned unparseable
    JSON.

    ``reason`` is short and human-readable; ``cause`` carries the
    underlying exception when one was raised by litellm or the JSON
    parser. Caught at the engine boundary — extraction is best-effort,
    a failure does NOT abort the run, it just leaves the iteration's
    evidence empty.
    """

    def __init__(self, reason: str, *, cause: BaseException | None = None):
        self.reason = reason
        self.cause = cause
        super().__init__(reason)


class HypothesisGenerationError(BeliefGraphError):
    """The hypothesis generator's LLM call failed or returned unparseable
    JSON. Same boundary behaviour as :class:`EvidenceExtractionError` —
    a failure leaves the existing hypothesis set unchanged for this
    iteration."""

    def __init__(self, reason: str, *, cause: BaseException | None = None):
        self.reason = reason
        self.cause = cause
        super().__init__(reason)
