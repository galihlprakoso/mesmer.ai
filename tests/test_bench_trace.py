"""Tests for mesmer.bench.trace — per-trial event capture + post-run telemetry.

Covers:
  * BenchEventRecorder: callable contract, elapsed timestamping, tee
    forwarding, JSONL write shape, throttle-wait parsing.
  * extract_trial_telemetry: winning-module resolution via canary_turn AND
    via high-score fallback, tier-sequence derivation, monotonic ladder
    check, dead-end rollup, robustness to None graph/registry.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from mesmer.bench.trace import (
    BenchEventRecorder,
    TrialTelemetry,
    _is_monotonic,
    _parse_leading_seconds,
    extract_trial_telemetry,
)
from mesmer.core.agent.context import Turn
from mesmer.core.constants import LogEvent, NodeStatus
from mesmer.core.graph import AttackGraph
from mesmer.core.module import ModuleConfig
from mesmer.core.registry import Registry


# ---------------------------------------------------------------------------
# BenchEventRecorder
# ---------------------------------------------------------------------------


class TestRecorder:
    def test_call_stamps_events_in_order(self):
        """Events come back with a monotonic elapsed-second key."""
        rec = BenchEventRecorder()
        rec("module_start", "begin")
        rec("judge_score", "7/10")
        assert [e[1] for e in rec.events] == ["module_start", "judge_score"]
        # Timestamps strictly non-decreasing.
        assert rec.events[1][0] >= rec.events[0][0]

    def test_tee_forwards_events(self):
        """tee_to is called for every captured event."""
        tee_calls: list[tuple[str, str]] = []

        def tee(ev: str, detail: str = "") -> None:
            tee_calls.append((ev, detail))

        rec = BenchEventRecorder(tee_to=tee)
        rec("delegate", "target-profiler")
        rec("conclude", "done")
        assert tee_calls == [("delegate", "target-profiler"), ("conclude", "done")]

    def test_counts_rolls_up_by_event_name(self):
        rec = BenchEventRecorder()
        rec("llm_call", "")
        rec("llm_call", "")
        rec("judge_score", "5/10")
        assert rec.counts() == {"llm_call": 2, "judge_score": 1}

    def test_write_emits_jsonl(self, tmp_path):
        rec = BenchEventRecorder()
        rec("module_start", "hello")
        rec("conclude", "bye")
        target = tmp_path / "events" / "trial1.jsonl"
        rec.write(target)
        rows = [json.loads(line) for line in target.read_text().splitlines()]
        assert len(rows) == 2
        assert rows[0]["event"] == "module_start"
        assert rows[0]["detail"] == "hello"
        assert rows[0]["t"] >= 0
        # Parent directory auto-created.
        assert target.parent.is_dir()

    def test_throttle_wait_parses_seconds_from_detail(self):
        """Detail strings of the form '…<num>s…' contribute to the wait total."""
        rec = BenchEventRecorder()
        rec(LogEvent.THROTTLE_WAIT.value, "waited 0.42s at gate=max_rpm")
        rec(LogEvent.THROTTLE_WAIT.value, "waited 1.50s at gate=max_concurrent")
        # Unrelated events don't contribute.
        rec("llm_call", "pretend 99s here")
        assert abs(rec.throttle_wait_seconds() - (0.42 + 1.5)) < 1e-6

    def test_throttle_wait_tolerates_malformed_detail(self):
        """No crash when the detail doesn't match the expected shape."""
        rec = BenchEventRecorder()
        rec(LogEvent.THROTTLE_WAIT.value, "something unusual happened")
        assert rec.throttle_wait_seconds() == 0.0


class TestParseLeadingSeconds:
    """`_parse_leading_seconds` handles the throttle-wait detail shape."""

    def test_extracts_first_seconds_token(self):
        assert _parse_leading_seconds("waited 0.42s at gate=max_rpm") == 0.42

    def test_returns_zero_on_missing_suffix(self):
        assert _parse_leading_seconds("no seconds here") == 0.0

    def test_returns_zero_on_malformed_number(self):
        assert _parse_leading_seconds("waited abcs somewhere") == 0.0


# ---------------------------------------------------------------------------
# extract_trial_telemetry — happy-path walk of a real graph
# ---------------------------------------------------------------------------


def _registry() -> Registry:
    r = Registry()
    r.register(ModuleConfig(name="target-profiler", tier=0))
    r.register(ModuleConfig(name="instruction-recital", tier=0))
    r.register(ModuleConfig(name="delimiter-injection", tier=1))
    r.register(ModuleConfig(name="foot-in-door", tier=2))
    return r


def _graph_with_nodes(run_id: str) -> AttackGraph:
    """Build a graph with four nodes in a fixed temporal order."""
    g = AttackGraph()
    root = g.ensure_root()
    # Add nodes; stamp each with monotonically-increasing timestamps so the
    # extractor's time-based ordering is unambiguous.
    t = time.time()
    for i, (mod, score, is_dead) in enumerate([
        ("target-profiler", 7, False),
        ("instruction-recital", 1, True),     # tier-0 dead-end
        ("delimiter-injection", 9, False),
        ("foot-in-door", 3, False),
    ]):
        status = NodeStatus.DEAD.value if is_dead else NodeStatus.ALIVE.value
        n = g.add_node(
            parent_id=root.id,
            module=mod,
            approach=f"angle {i} words here plenty",
            score=score,
            status=status,
            run_id=run_id,
            reflection="detected" if is_dead else "",
        )
        n.timestamp = t + i * 0.01
    return g


def _result_with_turns(graph: AttackGraph, run_id: str, turns: list[Turn], tel):
    """Assemble the minimum SimpleNamespace-shaped RunResult the extractor needs."""
    ctx = SimpleNamespace(turns=turns, telemetry=tel)
    return SimpleNamespace(
        graph=graph,
        run_id=run_id,
        telemetry=tel,
        ctx=ctx,
    )


class TestExtractorGraphWalk:
    def test_modules_called_and_tier_sequence(self):
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=5, llm_seconds=2.5)
        turns = [Turn("", "", module="target-profiler")]
        result = _result_with_turns(g, "r1", turns, tel)

        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)

        assert tr.modules_called == [
            "target-profiler",
            "instruction-recital",
            "delimiter-injection",
            "foot-in-door",
        ]
        assert tr.tier_sequence == [0, 0, 1, 2]

    def test_profiler_ran_first(self):
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=5, llm_seconds=2.5)
        result = _result_with_turns(g, "r1", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.profiler_ran_first is True

    def test_ladder_monotonic_true_for_ascending_tier_sequence(self):
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "r1", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.ladder_monotonic is True

    def test_ladder_monotonic_false_when_leader_skipped_rungs(self):
        """Tier [2, 0] is a violation — leader went cognitive before probe."""
        g = AttackGraph()
        root = g.ensure_root()
        t = time.time()
        n1 = g.add_node(root.id, "foot-in-door", "angle plenty words here",
                        score=3, run_id="r2")
        n1.timestamp = t
        n2 = g.add_node(root.id, "target-profiler", "angle other words here",
                        score=5, run_id="r2")
        n2.timestamp = t + 0.01
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "r2", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.tier_sequence == [2, 0]
        assert tr.ladder_monotonic is False

    def test_dead_ends_rolled_up_with_tier_and_reason(self):
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "r1", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert len(tr.dead_ends) == 1
        assert tr.dead_ends[0]["module"] == "instruction-recital"
        assert tr.dead_ends[0]["tier"] == 0
        assert tr.dead_ends[0]["score"] == 1

    def test_per_module_scores_grouped(self):
        """One entry per unique module, preserving order of observation."""
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "r1", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.per_module_scores["target-profiler"] == [7]
        assert tr.per_module_scores["delimiter-injection"] == [9]
        assert tr.per_module_scores["foot-in-door"] == [3]
        assert tr.per_module_scores["instruction-recital"] == [1]


class TestWinAttribution:
    def test_winning_module_from_canary_turn(self):
        """When canary_turn points at a Turn whose ``module`` is set, that wins."""
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        turns = [
            Turn(sent="probe", received="refusal", module="instruction-recital"),
            Turn(sent="frame", received="LEAK", module="delimiter-injection"),
        ]
        result = _result_with_turns(g, "r1", turns, tel)
        tr = extract_trial_telemetry(
            result, registry=_registry(), canary_turn=2,
        )
        assert tr.winning_module == "delimiter-injection"
        assert tr.winning_tier == 1

    def test_falls_back_to_best_node_when_canary_turn_absent(self):
        """No canary → winning_module = highest-score promising node (>=7)."""
        g = _graph_with_nodes(run_id="r1")
        # delimiter-injection has score 9 above; should win the fallback.
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "r1", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.winning_module == "delimiter-injection"
        assert tr.winning_tier == 1

    def test_no_winner_when_no_node_reaches_promising_score(self):
        """If every node scored <7 and no canary landed, winning_module stays None."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "target-profiler", "probe tokens here plenty",
                   score=3, run_id="r3")
        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "r3", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.winning_module is None
        assert tr.winning_tier is None


class TestFrontierExclusion:
    """Frontier nodes (score=0 placeholders for proposed-next-moves) must
    NOT pollute the per-trial attempt telemetry.

    Regression guard: a trial that ran one tier-0 probe (score 10) and
    produced five frontier suggestions for the next call must report
    ``modules_called`` = 1 module, ``per_module_scores`` = {mod: [10]},
    median score 10 — not six entries with four trailing zeros.
    """

    def test_frontiers_excluded_from_per_module_scores(self):
        g = AttackGraph()
        root = g.ensure_root()
        t = time.time()
        # One real attempt.
        explored = g.add_node(
            root.id, "instruction-recital", "recite words here plenty",
            score=10, run_id="R",
        )
        explored.timestamp = t
        # Three frontier suggestions for the next move — score 0 placeholders.
        for i, mod in enumerate(["target-profiler", "direct-ask", "format-shift"]):
            fn = g.add_frontier_node(root.id, mod, f"angle {i} words here",
                                     run_id="R")
            fn.timestamp = t + 0.01 * (i + 1)

        tel = SimpleNamespace(n_calls=5, llm_seconds=2.0)
        result = _result_with_turns(g, "R", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)

        # Exactly one module attempted; no frontier pollution.
        assert tr.modules_called == ["instruction-recital"]
        assert tr.tier_sequence == [0]
        assert tr.per_module_scores == {"instruction-recital": [10]}
        assert tr.dead_ends == []

    def test_frontiers_dont_influence_winning_module_fallback(self):
        """The score>=7 fallback for winning_module must ignore frontier
        score=0 entries. Otherwise an unevaluated frontier could
        artificially become the highest-scored node (if all real attempts
        scored low) and mis-attribute the win.
        """
        g = AttackGraph()
        root = g.ensure_root()
        t = time.time()
        real = g.add_node(
            root.id, "instruction-recital", "probe angle words here plenty",
            score=8, run_id="R",
        )
        real.timestamp = t
        # A frontier for a different tier-2 module — would score 0 but
        # must not displace the real tier-0 attempt as the winner.
        fn = g.add_frontier_node(root.id, "foot-in-door",
                                 "warm up angle words plenty", run_id="R")
        fn.timestamp = t + 0.01

        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = _result_with_turns(g, "R", [], tel)
        tr = extract_trial_telemetry(result, registry=_registry(), canary_turn=None)
        assert tr.winning_module == "instruction-recital"
        assert tr.winning_tier == 0


class TestExtractorRobustness:
    def test_returns_telemetry_only_when_graph_is_none(self):
        """Fake RunResults (tests that skip the graph) don't crash."""
        rec = BenchEventRecorder()
        rec(LogEvent.COMPRESSION.value, "squash")
        tel = SimpleNamespace(n_calls=3, llm_seconds=1.25)
        result = SimpleNamespace(
            graph=None, run_id="r", ctx=SimpleNamespace(turns=[], telemetry=tel),
            telemetry=tel,
        )
        tr = extract_trial_telemetry(
            result, registry=_registry(), canary_turn=None, recorder=rec,
        )
        assert tr.modules_called == []
        assert tr.winning_module is None
        assert tr.n_llm_calls == 3
        assert abs(tr.llm_seconds - 1.25) < 1e-6
        assert tr.compression_events == 1

    def test_returns_telemetry_only_when_registry_is_none(self):
        g = _graph_with_nodes(run_id="r1")
        tel = SimpleNamespace(n_calls=2, llm_seconds=0.1)
        result = _result_with_turns(g, "r1", [], tel)
        tr = extract_trial_telemetry(result, registry=None, canary_turn=None)
        # Graph present but registry missing → still degrades cleanly.
        assert tr.modules_called == []
        assert tr.n_llm_calls == 2


class TestIsMonotonic:
    def test_empty_and_single_element(self):
        assert _is_monotonic([])
        assert _is_monotonic([2])

    def test_ascending_passes(self):
        assert _is_monotonic([0, 0, 1, 2, 2])

    def test_descending_fails(self):
        assert not _is_monotonic([2, 0])


# ---------------------------------------------------------------------------
# TrialTelemetry is a plain dataclass — smoke-test default shape stability
# ---------------------------------------------------------------------------


class TestTrialTelemetryDefaults:
    def test_all_fields_zero_on_construction(self):
        t = TrialTelemetry()
        assert t.modules_called == []
        assert t.tier_sequence == []
        assert t.per_module_scores == {}
        assert t.dead_ends == []
        assert t.winning_module is None
        assert t.winning_tier is None
        assert t.profiler_ran_first is False
        assert t.ladder_monotonic is True
        assert t.compression_events == 0
        assert t.n_llm_calls == 0
        assert t.llm_seconds == 0.0
        assert t.throttle_wait_seconds == 0.0


# ---------------------------------------------------------------------------
# Per-trial graph snapshot
# ---------------------------------------------------------------------------


class TestGraphSnapshot:
    """`write_trial_graph_snapshot` emits a trial-scoped JSON view of the
    attack graph — root + nodes whose run_id matches the trial's run_id.
    Cross-run pollution on the shared target graph is invisible.
    """

    def test_writes_only_this_runs_nodes_plus_root(self, tmp_path):
        from mesmer.bench.trace import write_trial_graph_snapshot

        g = AttackGraph()
        root = g.ensure_root()
        # Two runs on the same graph.
        g.add_node(root.id, "direct-ask", "angle words here",
                   score=3, run_id="A")
        g.add_node(root.id, "authority-bias", "other angle words",
                   score=5, run_id="B")

        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = SimpleNamespace(
            graph=g, run_id="A", ctx=SimpleNamespace(turns=[], telemetry=tel),
            telemetry=tel,
        )

        path = tmp_path / "events" / "trialA.graph.json"
        write_trial_graph_snapshot(result, path)

        payload = json.loads(path.read_text())
        assert payload["run_id"] == "A"
        modules = {n["module"] for n in payload["nodes"].values()}
        # Root + run A's node only. Run B's node is excluded.
        assert modules == {"root", "direct-ask"}

    def test_missing_graph_does_not_crash(self, tmp_path):
        """Fake RunResults with graph=None simply write nothing."""
        from mesmer.bench.trace import write_trial_graph_snapshot

        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = SimpleNamespace(
            graph=None, run_id="A", ctx=SimpleNamespace(turns=[], telemetry=tel),
            telemetry=tel,
        )
        path = tmp_path / "missing.graph.json"
        write_trial_graph_snapshot(result, path)  # no-op
        assert not path.exists()

    def test_cross_run_parents_are_severed(self, tmp_path):
        """A node whose parent lives in a prior run (and isn't in the
        snapshot) must have its ``parent_id`` nulled out — otherwise
        tree-walkers chase a phantom reference that's only resolvable by
        opening the cross-run persisted graph.
        """
        from mesmer.bench.trace import write_trial_graph_snapshot

        g = AttackGraph()
        root = g.ensure_root()
        # Prior run's node.
        prior = g.add_node(root.id, "direct-ask", "old angle",
                           score=10, run_id="OLD")
        # Current run's node parented under the prior-run node — exactly
        # the shape observed in the 20260424T133148Z bench artifacts.
        current = g.add_node(prior.id, "direct-ask", "refined angle",
                             score=10, run_id="NEW")

        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = SimpleNamespace(
            graph=g, run_id="NEW",
            ctx=SimpleNamespace(turns=[], telemetry=tel),
            telemetry=tel,
        )
        path = tmp_path / "events" / "trialNEW.graph.json"
        write_trial_graph_snapshot(result, path)

        payload = json.loads(path.read_text())
        # Root + NEW's single node. OLD's node is excluded.
        assert set(payload["nodes"].keys()) == {root.id, current.id}
        # NEW's node parent is severed (was OLD's id, now null) so tree
        # walkers don't chase a missing reference.
        assert payload["nodes"][current.id]["parent_id"] is None
        # Root's parent_id stays None (never changed).
        assert payload["nodes"][root.id]["parent_id"] is None

    def test_intra_snapshot_parents_preserved(self, tmp_path):
        """Parents that ARE in the snapshot must stay intact — the severance
        only kicks in for cross-run orphans.
        """
        from mesmer.bench.trace import write_trial_graph_snapshot

        g = AttackGraph()
        root = g.ensure_root()
        parent = g.add_node(root.id, "target-profiler", "recon",
                            score=7, run_id="NOW")
        child = g.add_node(parent.id, "direct-ask", "probe",
                           score=9, run_id="NOW")

        tel = SimpleNamespace(n_calls=0, llm_seconds=0.0)
        result = SimpleNamespace(
            graph=g, run_id="NOW",
            ctx=SimpleNamespace(turns=[], telemetry=tel),
            telemetry=tel,
        )
        path = tmp_path / "events" / "trialNOW.graph.json"
        write_trial_graph_snapshot(result, path)

        payload = json.loads(path.read_text())
        assert payload["nodes"][child.id]["parent_id"] == parent.id
        assert payload["nodes"][parent.id]["parent_id"] == root.id
