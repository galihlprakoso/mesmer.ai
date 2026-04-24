"""Per-trial tracing for benchmark runs.

Two pieces live here:

1. :class:`BenchEventRecorder` — a :data:`LogFn` that captures every ReAct
   event emitted during a trial (with a monotonic-clock timestamp) so the
   orchestrator can write a per-trial events JSONL next to the trial
   results. Optionally tees events to a parent log callback so the CLI's
   ``--verbose`` still prints them live.

2. :func:`extract_trial_telemetry` — a PURE walk of a finished
   :class:`RunResult` that answers the TAPER telemetry questions the
   published artifact has to carry:

     - which modules were delegated to, in what order?
     - did the agent climb the tier ladder?
     - which module produced the winning turn?
     - where did cheap probes die and what did they say?
     - how many LLM calls fed the trial (separate from token totals)?
     - how many compression events fired?

   Everything is derived from the read-only :class:`Context` + attack
   graph state after the run completes. No instrumentation inside the
   ReAct loop.

This file has no IO except the file-writing method on the recorder, and
no dependencies on :mod:`mesmer.bench.orchestrator` — keeps tests on the
extractor independent of the spec-loading machinery.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from mesmer.core.agent import LogFn
from mesmer.core.constants import LogEvent
from mesmer.core.graph import AttackNode
from mesmer.core.module import DEFAULT_TIER
from mesmer.core.registry import Registry
from mesmer.core.runner import RunResult


# ---------------------------------------------------------------------------
# Event capture — one recorder per trial
# ---------------------------------------------------------------------------


class BenchEventRecorder:
    """In-memory capture of every :data:`LogFn` event during a trial.

    Is itself a ``LogFn`` — pass it as ``log=`` to ``execute_run``. Each
    call stamps the event with monotonic elapsed seconds since the
    recorder was constructed (so cross-trial time isn't leaked and
    wall-clock skew doesn't matter).

    When ``tee_to`` is given, every captured event is also forwarded to
    the parent callback — this is how ``mesmer bench --verbose`` keeps
    printing live events while the same events land on disk.
    """

    def __init__(self, *, tee_to: LogFn | None = None) -> None:
        self.events: list[tuple[float, str, str]] = []
        self._t0 = time.monotonic()
        self._tee = tee_to

    def __call__(self, event: str, detail: str = "") -> None:
        elapsed = time.monotonic() - self._t0
        self.events.append((elapsed, event, detail))
        if self._tee is not None:
            self._tee(event, detail)

    # --- rollups used by extract_trial_telemetry ---

    def counts(self) -> dict[str, int]:
        """One dict ``{event_name: count}`` — cheap cardinality summary."""
        out: dict[str, int] = {}
        for _, ev, _ in self.events:
            out[ev] = out.get(ev, 0) + 1
        return out

    def throttle_wait_seconds(self) -> float:
        """Sum of reported throttle waits.

        :data:`LogEvent.THROTTLE_WAIT` details carry a human-readable
        fragment like ``"waited 0.42s at gate=max_rpm"``. We extract the
        seconds when present; silently 0 otherwise so a format change in
        the log string never crashes the bench.
        """
        total = 0.0
        for _, ev, detail in self.events:
            if ev != LogEvent.THROTTLE_WAIT.value:
                continue
            # Format authored by ``KeyPool.acquire`` — "waited {s:.2f}s …".
            # Be tolerant: anything non-parseable just contributes 0.
            seconds = _parse_leading_seconds(detail)
            total += seconds
        return total

    # --- IO ---

    def write(self, path: Path) -> None:
        """Flush events to ``path`` as JSONL.

        One row per event: ``{"t": 0.042, "event": "...", "detail": "..."}``.
        The enclosing directory is created on demand so the orchestrator
        doesn't need to pre-make an events/ subdir.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for t, ev, detail in self.events:
                f.write(json.dumps({
                    "t": round(t, 3),
                    "event": ev,
                    "detail": detail,
                }) + "\n")


def _parse_leading_seconds(detail: str) -> float:
    """Extract ``<float>s`` from the start of a detail fragment.

    ``KeyPool.acquire`` emits ``"waited 1.23s at gate=max_rpm"``. We grab
    the first number followed by ``s``. Returns 0 on any shape mismatch.
    """
    # Find the first substring of digits/dots ending in 's'. No regex —
    # this is called per-event, small and allocation-free.
    s = detail.lstrip()
    # Tolerate a leading keyword like "waited".
    for word in s.split():
        if word.endswith("s"):
            try:
                return float(word[:-1])
            except ValueError:
                return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Post-run telemetry — derived from Context + graph + event counts
# ---------------------------------------------------------------------------


@dataclass
class TrialTelemetry:
    """Per-trial trace fields derived from a completed ``RunResult``.

    Every field is safe to serialise to JSON as-is:
    ``dead_ends`` is a ``list[dict]``, ``per_module_scores`` keys are
    module names (strings), and tier-keyed dicts are intentionally kept
    at the dataclass level with ``int`` keys — the orchestrator coerces
    to string keys only at the JSONL boundary.
    """

    modules_called: list[str] = field(default_factory=list)
    tier_sequence: list[int] = field(default_factory=list)
    per_module_scores: dict[str, list[int]] = field(default_factory=dict)
    dead_ends: list[dict] = field(default_factory=list)
    winning_module: str | None = None
    winning_tier: int | None = None
    profiler_ran_first: bool = False
    # True when ``tier_sequence`` never climbs down (each step's tier >=
    # previous). Violations are the concrete "leader skipped the ladder"
    # signal at the trial level. A trivially-short sequence (0 or 1 item)
    # is monotonic by convention.
    ladder_monotonic: bool = True
    # Count of :data:`LogEvent.COMPRESSION` events — zero in TRIALS mode
    # is expected; non-zero in CONTINUOUS is the signal compression
    # actually fired during this trial.
    compression_events: int = 0
    n_llm_calls: int = 0
    llm_seconds: float = 0.0
    throttle_wait_seconds: float = 0.0


def extract_trial_telemetry(
    result: RunResult,
    *,
    registry: Registry | None,
    canary_turn: int | None,
    recorder: BenchEventRecorder | None = None,
) -> TrialTelemetry:
    """Derive a :class:`TrialTelemetry` from a completed run.

    ``canary_turn`` is the 1-indexed position of the successful target
    reply (the bench canary judge returns it). When set, the winning
    module is read off ``ctx.turns[canary_turn - 1].module`` — the engine
    stamps each Turn with the sub-module that produced it, so this is
    authoritative. Falls back to "highest-scoring node this run" when
    the canary didn't land.

    ``recorder`` is optional — when given, its event counts feed the
    compression / LLM-call / throttle-wait fields. When absent those
    default to zero.

    **Robustness** — when ``result.graph`` or ``registry`` is ``None``
    (test fakes stubbing execute_run, or a run that crashed before any
    graph state was created), the graph-derived trace fields stay at
    their zero-shaped defaults and only the telemetry counters sourced
    from ``result.telemetry`` + the recorder populate. This keeps the
    extractor trivially usable from stubs without forcing every test to
    mock a full AttackGraph + Registry.
    """
    graph = result.graph
    run_id = result.run_id

    # Early exit for stubs / crashed runs.
    if graph is None or registry is None:
        return _telemetry_only_from(result, recorder)

    # Only this run's nodes, in timestamp order — sibling runs persisted
    # on the same graph must not bleed into the trace.
    #
    # FRONTIER nodes (score=0 placeholders for "next move" suggestions) are
    # EXCLUDED from the attempt-level telemetry. They're unevaluated —
    # counting them as "attempts with score 0" would tank the median and
    # mis-attribute the ladder (a leader that enqueues T2 frontier items
    # during its T0 run would look like it did T2 work). Frontier nodes
    # still land in the graph snapshot; they just don't count as attempts.
    #
    # LEADER-VERDICT nodes are also excluded — the leader is a module
    # whose own execution is recorded at run end (source=LEADER). It's
    # not an attack attempt in the TAPER ladder sense: including it
    # would corrupt modules_called, tier_sequence (the leader's module
    # rarely has a TAPER tier declared), and winning-module attribution.
    all_run_nodes: list[AttackNode] = sorted(
        (
            n for n in graph.iter_nodes()
            if n.run_id == run_id
            and n.module
            and n.module != "root"
            and not n.is_leader_verdict
        ),
        key=lambda n: n.timestamp,
    )
    nodes = [n for n in all_run_nodes if not n.is_frontier]

    modules_called = [n.module for n in nodes]
    tier_sequence = [registry.tier_of(m) for m in modules_called]

    per_module_scores: dict[str, list[int]] = {}
    for n in nodes:
        per_module_scores.setdefault(n.module, []).append(n.score)

    dead_ends = [
        {
            "module": n.module,
            "tier": registry.tier_of(n.module),
            "score": n.score,
            # Truncate long reflections — the full text is still in
            # graph.json. 240 chars keeps the one-liner readable in
            # terminal tools like ``jq`` without blowing up an event row.
            "reason": (n.reflection or "")[:240],
        }
        for n in nodes if n.is_dead
    ]

    winning_module, winning_tier = _resolve_winning_module(
        result, nodes, registry, canary_turn,
    )

    profiler_ran_first = bool(modules_called) and modules_called[0] == "target-profiler"
    ladder_monotonic = _is_monotonic(tier_sequence)

    # Event-derived fields — default to zero when no recorder was wired.
    counts = recorder.counts() if recorder else {}
    compression_events = counts.get(LogEvent.COMPRESSION.value, 0)
    throttle_wait_s = recorder.throttle_wait_seconds() if recorder else 0.0

    tel = result.telemetry
    return TrialTelemetry(
        modules_called=modules_called,
        tier_sequence=tier_sequence,
        per_module_scores=per_module_scores,
        dead_ends=dead_ends,
        winning_module=winning_module,
        winning_tier=winning_tier,
        profiler_ran_first=profiler_ran_first,
        ladder_monotonic=ladder_monotonic,
        compression_events=compression_events,
        n_llm_calls=tel.n_calls,
        llm_seconds=round(tel.llm_seconds, 3),
        throttle_wait_seconds=round(throttle_wait_s, 3),
    )


def _telemetry_only_from(
    result: RunResult,
    recorder: BenchEventRecorder | None,
) -> TrialTelemetry:
    """Build a :class:`TrialTelemetry` with just the counters sourced from
    ``result.telemetry`` + the recorder. Called when graph / registry are
    unavailable (stubs, crashed runs) — keeps the extractor's return type
    consistent so callers never need to branch.
    """
    counts = recorder.counts() if recorder else {}
    throttle = recorder.throttle_wait_seconds() if recorder else 0.0
    tel = result.telemetry
    return TrialTelemetry(
        n_llm_calls=tel.n_calls,
        llm_seconds=round(tel.llm_seconds, 3),
        throttle_wait_seconds=round(throttle, 3),
        compression_events=counts.get(LogEvent.COMPRESSION.value, 0),
    )


def _resolve_winning_module(
    result: RunResult,
    run_nodes: list[AttackNode],
    registry: Registry,
    canary_turn: int | None,
) -> tuple[str | None, int | None]:
    """Pick the module credited with the win.

    Primary source — the Turn the canary landed on. The engine stamps
    each Turn with the sub-module that produced it; that's the
    authoritative attribution. Falls back to "top-scoring node of this
    run" when the canary judge didn't match but the graph still has a
    promising node (e.g. partial leaks).
    """
    turns = result.ctx.turns if result.ctx else []
    if canary_turn is not None and 1 <= canary_turn <= len(turns):
        mod = turns[canary_turn - 1].module
        if mod:
            return mod, registry.tier_of(mod)

    if run_nodes:
        best = max(run_nodes, key=lambda n: n.score)
        # Only credit a node that actually reached a promising score —
        # otherwise we'd attribute "wins" to tier-0 probes that were
        # merely non-dead.
        if best.score >= 7:
            return best.module, registry.tier_of(best.module)

    return None, None


def _is_monotonic(seq: Iterable[int]) -> bool:
    prev: int | None = None
    for x in seq:
        if prev is not None and x < prev:
            return False
        prev = x
    return True


def write_trial_graph_snapshot(
    result: RunResult,
    path: Path,
) -> None:
    """Write a per-trial snapshot of the attack graph.

    Includes the root node plus every node produced during ``result.run_id``.
    Keeps the JSON small and trial-scoped — consumers can diff snapshots
    across trials / runs without parsing the full (cross-run) target
    graph stored under ``~/.mesmer/targets/{hash}/graph.json``.

    Cross-run parents are severed. When a node's parent lives in a prior
    run (and therefore isn't in this snapshot), its ``parent_id`` is
    rewritten to ``None`` so tree-walking consumers don't chase a
    phantom reference. The original parent chain is preserved in the
    cross-run persisted graph — downstream tooling that needs the full
    ancestry reads ``~/.mesmer/targets/{hash}/graph.json`` instead.
    """
    graph = result.graph
    if graph is None:
        return

    run_id = result.run_id
    run_nodes = [
        n for n in graph.iter_nodes()
        if (n.run_id == run_id) or n.module == "root"
    ]
    ids_in_snapshot = {n.id for n in run_nodes}

    def _serialize(n: AttackNode) -> dict:
        d = n.to_dict()
        parent = d.get("parent_id")
        if parent and parent not in ids_in_snapshot:
            d["parent_id"] = None
        return d

    payload = {
        "run_id": run_id,
        "root_id": graph.root_id,
        "n_nodes": len(run_nodes),
        "nodes": {n.id: _serialize(n) for n in run_nodes},
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


__all__ = [
    "BenchEventRecorder",
    "TrialTelemetry",
    "extract_trial_telemetry",
    "write_trial_graph_snapshot",
    "DEFAULT_TIER",
]
