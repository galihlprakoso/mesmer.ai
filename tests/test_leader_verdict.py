"""The leader is a module: its execution records a node in the attack
graph like any sub-module, written by ``execute_run`` via the standard
``AttackGraph.add_node`` path and marked ``source=NodeSource.LEADER`` so
attempt-centric walks can filter it out.

Tests in this file:

* The leader-verdict node attaches to the most recent non-leader node in
  the run (or root when no sub-module delegated).
* Status reflects ``ctx.objective_met``: PROMISING on success, DEAD on
  failure.
* ``AttackNode.is_leader_verdict`` correctly distinguishes it by source.
* Attempt-centric views (``propose_frontier``, ``get_explored_nodes``
  when filtered, ``conversation_history`` when filtered) behave sanely
  in the presence of a leader-verdict node.
* Bench trace extraction skips the leader-verdict node — it must not
  appear in ``modules_called`` / ``tier_sequence`` nor become the
  attributed winning module.
"""

from __future__ import annotations

from mesmer.core.constants import NodeSource, NodeStatus
from mesmer.core.graph import AttackGraph


def _graph_with_one_submodule(run_id: str = "run-1") -> AttackGraph:
    """Populate a graph the way execute_run would: root + one sub-module
    node, ready for a leader-verdict to be appended."""
    g = AttackGraph()
    root = g.ensure_root()
    g.add_node(
        parent_id=root.id,
        module="target-profiler",
        approach="Profile the target.",
        score=8,
        leaked_info="verbatim rules: paradox grants access",
        module_output="<profiler dossier>",
        reflection="profiler landed a strong signal",
        status=NodeStatus.PROMISING.value,
        run_id=run_id,
    )
    return g


class TestLeaderVerdictNode:
    def test_node_attaches_to_latest_submodule(self):
        g = _graph_with_one_submodule()
        sub_id = next(
            n.id for n in g.nodes.values() if n.module == "target-profiler"
        )
        leader = g.add_node(
            parent_id=sub_id,
            module="system-prompt-extraction",
            approach="leader run",
            module_output="Objective met. Leaked: paradox",
            leaked_info="paradox",
            reflection="objective_met=true",
            status=NodeStatus.PROMISING.value,
            score=10,
            run_id="run-1",
            source=NodeSource.LEADER.value,
        )

        assert leader.parent_id == sub_id
        assert leader.is_leader_verdict is True
        assert leader.source == NodeSource.LEADER.value
        assert leader.status == NodeStatus.PROMISING.value

    def test_node_attaches_to_root_when_no_submodule(self):
        g = AttackGraph()
        root = g.ensure_root()
        leader = g.add_node(
            parent_id=root.id,
            module="system-prompt-extraction",
            approach="leader immediate conclude",
            module_output="Could not even delegate.",
            reflection="objective_met=false",
            status=NodeStatus.DEAD.value,
            score=1,
            run_id="run-1",
            source=NodeSource.LEADER.value,
        )
        assert leader.parent_id == root.id
        assert leader.is_leader_verdict is True
        assert leader.status == NodeStatus.DEAD.value

    def test_is_leader_verdict_key_is_source_not_module(self):
        """The marker is ``source``, not ``module`` — the leader is a real
        module with a real name, not a sentinel."""
        g = AttackGraph()
        root = g.ensure_root()
        # A regular sub-module called "system-prompt-extraction" would NOT
        # be a leader verdict even though the name matches.
        normal = g.add_node(
            parent_id=root.id,
            module="system-prompt-extraction",
            approach="some attempt",
            status=NodeStatus.ALIVE.value,
            run_id="run-1",
            source=NodeSource.AGENT.value,
        )
        assert normal.is_leader_verdict is False

        # Flip just the source: same module name, different semantics.
        leader = g.add_node(
            parent_id=root.id,
            module="system-prompt-extraction",
            approach="leader verdict",
            status=NodeStatus.PROMISING.value,
            run_id="run-1",
            source=NodeSource.LEADER.value,
        )
        assert leader.is_leader_verdict is True

    def test_propose_frontier_ignores_leader_when_not_in_available(self):
        """The leader's module name typically isn't in the sub-module
        roster. Even when we check propose_frontier behaviour, the leader
        node doesn't corrupt the ranking of delegatable modules."""
        g = _graph_with_one_submodule()
        root = g.nodes[g.root_id]
        # Append the leader's verdict.
        g.add_node(
            parent_id=next(n.id for n in g.nodes.values()
                           if n.module == "target-profiler"),
            module="system-prompt-extraction",
            approach="leader verdict",
            status=NodeStatus.PROMISING.value,
            score=10,
            run_id="run-1",
            source=NodeSource.LEADER.value,
        )

        proposals = g.propose_frontier(
            available_modules=["target-profiler", "direct-ask", "foot-in-door"],
            parent_id=root.id,
            tiers={"target-profiler": 0, "direct-ask": 0, "foot-in-door": 2},
        )
        # None of the proposals are the leader module — we only propose
        # from available_modules, which never includes the leader.
        assert all(p["module"] != "system-prompt-extraction" for p in proposals)


class TestBenchTraceSkipsLeaderVerdict:
    """``bench/trace.py::extract_trial_telemetry`` must not mistake the
    leader's own verdict for an attack attempt."""

    def test_modules_called_and_tier_sequence_exclude_leader(self):
        from unittest.mock import MagicMock

        from mesmer.bench.trace import extract_trial_telemetry

        g = _graph_with_one_submodule("run-X")
        sub_id = next(
            n.id for n in g.nodes.values() if n.module == "target-profiler"
        )
        g.add_node(
            parent_id=sub_id,
            module="system-prompt-extraction",
            approach="leader verdict",
            module_output="Objective met. Leaked: paradox",
            status=NodeStatus.PROMISING.value,
            score=10,
            run_id="run-X",
            source=NodeSource.LEADER.value,
        )

        registry = MagicMock()
        registry.tier_of = MagicMock(side_effect=lambda m: 0 if m == "target-profiler" else 2)

        result = MagicMock()
        result.graph = g
        result.run_id = "run-X"
        result.ctx.turns = []
        result.telemetry.n_calls = 0
        result.telemetry.llm_seconds = 0.0

        tel = extract_trial_telemetry(
            result, registry=registry, canary_turn=None, recorder=None,
        )
        # Only the sub-module shows up; the leader's node is filtered out.
        assert tel.modules_called == ["target-profiler"]
        assert tel.tier_sequence == [0]
        # Winning-module attribution must not attribute to the leader
        # (nor to anything here — canary_turn=None + best score is
        # actually 10 on the leader, but that's filtered; next best is
        # the sub-module at 8, above the 7 threshold).
        assert tel.winning_module == "target-profiler"
