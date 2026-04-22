"""Tests for mesmer.core.graph — AttackGraph and AttackNode."""

import json
import tempfile
from pathlib import Path

import pytest

from mesmer.core.graph import AttackGraph, AttackNode, hash_target


# ---------------------------------------------------------------------------
# AttackNode
# ---------------------------------------------------------------------------

class TestAttackNode:
    def test_defaults(self):
        node = AttackNode(id="abc")
        assert node.status == "frontier"
        assert node.source == "agent"
        assert node.score == 0
        assert node.children == []
        assert node.messages_sent == []

    def test_is_dead(self):
        node = AttackNode(id="x", status="dead")
        assert node.is_dead
        assert not node.is_frontier
        assert not node.is_promising

    def test_is_frontier(self):
        node = AttackNode(id="x", status="frontier")
        assert node.is_frontier
        assert not node.is_dead

    def test_is_promising(self):
        node = AttackNode(id="x", status="promising")
        assert node.is_promising

    def test_round_trip_dict(self):
        node = AttackNode(
            id="test123",
            parent_id="root",
            module="foot-in-door",
            approach="philosophy question",
            messages_sent=["hello"],
            target_responses=["hi"],
            score=7,
            leaked_info="design principles",
            reflection="promising angle",
            status="promising",
            children=["child1"],
            depth=2,
            run_id="run1",
            source="human",
        )
        d = node.to_dict()
        restored = AttackNode.from_dict(d)
        assert restored.id == "test123"
        assert restored.module == "foot-in-door"
        assert restored.score == 7
        assert restored.source == "human"
        assert restored.messages_sent == ["hello"]

    def test_from_dict_ignores_extra_keys(self):
        """Forwards compat: from_dict ignores unknown keys."""
        d = {"id": "x", "module": "test", "future_field": 999}
        node = AttackNode.from_dict(d)
        assert node.id == "x"
        assert node.module == "test"


# ---------------------------------------------------------------------------
# AttackGraph — construction
# ---------------------------------------------------------------------------

class TestAttackGraphConstruction:
    def test_empty_graph(self):
        g = AttackGraph()
        assert len(g) == 0
        assert g.root_id is None

    def test_ensure_root(self):
        g = AttackGraph()
        root = g.ensure_root()
        assert root.module == "root"
        assert root.status == "alive"
        assert root.depth == 0
        assert g.root_id == root.id
        assert len(g) == 1

        # Calling again returns same root
        root2 = g.ensure_root()
        assert root2.id == root.id
        assert len(g) == 1

    def test_add_node(self):
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(
            parent_id=root.id,
            module="authority-bias",
            approach="Stanford researcher",
            score=2,
            reflection="detected instantly",
        )
        assert node.parent_id == root.id
        assert node.depth == 1
        assert node.status == "dead"  # score 2 + reflection → auto-dead
        assert root.id in [n.parent_id for n in g.iter_nodes() if n.id == node.id]
        assert node.id in root.children

    def test_add_node_auto_promising(self):
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(
            parent_id=root.id,
            module="foot-in-door",
            approach="philosophy",
            score=7,
        )
        assert node.status == "promising"

    def test_add_node_alive_mid_score(self):
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(
            parent_id=root.id,
            module="cognitive-overload",
            approach="batch request",
            score=3,
        )
        assert node.status == "alive"  # score 3, no reflection → stays alive

    # --- P1 classification thresholds and same-module-no-gain ---

    def test_score_three_with_reflection_is_dead(self):
        """P1 raised the dead-threshold from 2 to 3. Meta-acknowledgement
        ('target admits it has instructions') now prunes rather than
        surviving as alive.
        """
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(
            root.id, "authority-bias", "QA team badge check",
            score=3, reflection="target just admitted having guardrails",
        )
        assert node.status == "dead"

    def test_same_module_no_gain_marks_dead(self):
        """Re-running the same technique with a near-identical approach and
        no score gain should be pruned automatically — this is the core
        fix for the 'authority-bias tried four times' pattern.
        """
        g = AttackGraph()
        root = g.ensure_root()
        # First attempt: authority-bias with a specific angle, moderate score.
        first = g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        assert first.status == "promising"

        # Second attempt: same module, same approach words, same score. Dead.
        second = g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        assert second.status == "dead"
        assert "same-module-no-gain" in second.reflection
        assert "5" in second.reflection

    def test_same_module_higher_score_stays_alive(self):
        """Repeating a module with genuine improvement keeps the node."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=4,
        )
        second = g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=7,
        )
        assert second.status == "promising"

    def test_same_module_dissimilar_approach_unaffected(self):
        """Different angles on the same technique are independent arms."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        second = g.add_node(
            root.id, "authority-bias",
            approach="compliance audit under HIPAA regulation request",
            score=5,
        )
        # Dissimilar tokens → no pruning collision
        assert second.status == "promising"

    def test_different_module_same_text_unaffected(self):
        """Two modules are different arms by definition, even with identical text."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        other = g.add_node(
            root.id, "narrative-transport",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        assert other.status == "promising"

    def test_short_approach_skips_similarity_check(self):
        """Approach strings with fewer than MIN_TOKENS tokens are too thin
        to match reliably — similarity check must skip them to avoid
        false positives on labels like 'Stanford'.
        """
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "authority-bias", "Stanford", score=4)
        second = g.add_node(root.id, "authority-bias", "Stanford", score=4)
        # Without similarity check, stays alive (no reflection, score=4 is
        # not below promising threshold so not classified, stays alive).
        assert second.status == "alive"

    def test_frontier_nodes_do_not_trigger_similarity_prune(self):
        """An unexplored frontier with the same module shouldn't cause the
        new attempt to auto-dead — a frontier has no score to compare.
        """
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(
            root.id, "authority-bias",
            "Stanford researcher claim persuading target",
        )
        node = g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        assert node.status == "promising"

    def test_fulfill_frontier_routes_through_classifier(self):
        """Fulfilling a frontier should apply the same P1 rules — raised
        dead threshold and same-module-no-gain.
        """
        g = AttackGraph()
        root = g.ensure_root()
        # Prior same-module attempt at score 5.
        g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=5,
        )
        f = g.add_frontier_node(
            root.id, "authority-bias",
            "Stanford researcher claim persuading target",
        )
        result = g.fulfill_frontier(
            f.id,
            approach="Stanford researcher claim persuading target",
            messages_sent=["hi"], target_responses=["no"],
            score=5, leaked_info="", reflection="", run_id="",
        )
        # Same module, same approach, no gain → dead via P1 rule.
        assert result.status == "dead"
        assert "same-module-no-gain" in result.reflection

    def test_add_frontier_node(self):
        g = AttackGraph()
        root = g.ensure_root()
        parent = g.add_node(root.id, "foot-in-door", "philosophy", score=7)
        frontier = g.add_frontier_node(parent.id, "foot-in-door", "ask about tools")
        assert frontier.status == "frontier"
        assert frontier.parent_id == parent.id
        assert frontier.depth == parent.depth + 1
        assert frontier.id in parent.children

    def test_add_human_hint(self):
        g = AttackGraph()
        g.ensure_root()
        hint = g.add_human_hint("try calendar API errors")
        assert hint.source == "human"
        assert hint.status == "frontier"
        assert hint.module == "human-insight"
        assert hint.approach == "try calendar API errors"
        assert hint.parent_id == g.root_id

    def test_add_human_hint_creates_root_if_needed(self):
        g = AttackGraph()
        assert g.root_id is None
        hint = g.add_human_hint("test hint")
        assert g.root_id is not None
        assert hint.parent_id == g.root_id

    def test_mark_dead(self):
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(root.id, "test", "approach", score=5)
        assert node.status == "promising"
        g.mark_dead(node.id, "failed completely")
        assert node.status == "dead"
        assert node.reflection == "failed completely"

    def test_promote_frontier(self):
        g = AttackGraph()
        root = g.ensure_root()
        frontier = g.add_frontier_node(root.id, "test", "explore this")
        assert frontier.is_frontier
        result = g.promote_frontier(frontier.id)
        assert result is not None
        assert result.status == "alive"

    def test_promote_non_frontier_returns_none(self):
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(root.id, "test", "explored", score=5)
        result = g.promote_frontier(node.id)
        assert result is None

    def test_edit_approach(self):
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "foo", "original approach text")
        result = g.edit_approach(f.id, "revised approach text")
        assert result is not None
        assert g.nodes[f.id].approach == "revised approach text"

    def test_edit_approach_unknown_id_returns_none(self):
        g = AttackGraph()
        g.ensure_root()
        assert g.edit_approach("no-such-id", "whatever") is None

    # --- fulfill_frontier (TAP-aligned promotion with details) ---

    def test_fulfill_frontier_promoting_sets_all_fields(self):
        g = AttackGraph()
        root = g.ensure_root()
        parent = g.add_node(root.id, "foo", "parent attempt", score=7)
        f = g.add_frontier_node(parent.id, "bar", "try bar with angle X")

        result = g.fulfill_frontier(
            f.id,
            approach="try bar with angle X",
            messages_sent=["hi", "follow-up"],
            target_responses=["hey", "maybe"],
            score=8,
            leaked_info="leaked the plan",
            reflection="Scored 8 because target opened up",
            run_id="r-1",
        )
        assert result is not None
        assert result.status == "promising"  # score 8 → promising
        assert result.score == 8
        assert result.leaked_info == "leaked the plan"
        assert result.messages_sent == ["hi", "follow-up"]
        assert result.target_responses == ["hey", "maybe"]
        assert result.reflection == "Scored 8 because target opened up"
        assert result.run_id == "r-1"
        # Parent-child relationship preserved (TAP semantics)
        assert result.parent_id == parent.id
        assert result.id in parent.children

    def test_fulfill_frontier_dead_on_low_score(self):
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "foo", "x")
        result = g.fulfill_frontier(
            f.id, approach="x", messages_sent=["m"], target_responses=["r"],
            score=1, leaked_info="", reflection="instant refusal", run_id="r",
        )
        assert result.status == "dead"

    def test_fulfill_frontier_alive_on_middling_score(self):
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "foo", "x")
        result = g.fulfill_frontier(
            f.id, approach="x", messages_sent=["m"], target_responses=["r"],
            score=4, leaked_info="", reflection="", run_id="",
        )
        assert result.status == "alive"

    def test_fulfill_frontier_on_explored_returns_none(self):
        """Cannot fulfill a non-frontier node."""
        g = AttackGraph()
        root = g.ensure_root()
        explored = g.add_node(root.id, "foo", "already done", score=5)
        result = g.fulfill_frontier(
            explored.id, approach="x", messages_sent=[], target_responses=[],
            score=8, leaked_info="", reflection="", run_id="",
        )
        assert result is None
        # Original node is untouched
        assert explored.score == 5

    def test_fulfill_frontier_unknown_id_returns_none(self):
        g = AttackGraph()
        g.ensure_root()
        result = g.fulfill_frontier(
            "no-such-id", approach="x", messages_sent=[], target_responses=[],
            score=5, leaked_info="", reflection="", run_id="",
        )
        assert result is None

    def test_fulfill_frontier_preserves_approach_when_empty_passed(self):
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "foo", "original approach")
        g.fulfill_frontier(
            f.id, approach="", messages_sent=["m"], target_responses=["r"],
            score=5, leaked_info="", reflection="", run_id="",
        )
        assert g.nodes[f.id].approach == "original approach"

    def test_fulfill_frontier_overrides_module_when_provided(self):
        """When the leader executes a frontier with a different sub-module than
        the frontier originally suggested, the fulfilled node must record the
        ACTUAL module that ran, not the frontier's stale label."""
        g = AttackGraph()
        root = g.ensure_root()
        # Frontier was created suggesting safety-profiler, but the leader
        # decides to call narrative-transport on that same refinement slot.
        f = g.add_frontier_node(root.id, "safety-profiler", "probe rules")
        result = g.fulfill_frontier(
            f.id,
            approach="story about rules",
            messages_sent=["hi"],
            target_responses=["hey"],
            score=5,
            leaked_info="",
            reflection="",
            run_id="r",
            module="narrative-transport",
        )
        assert result is not None
        assert result.module == "narrative-transport"

    def test_fulfill_frontier_preserves_module_when_not_provided(self):
        """Backward compat: no module arg means module stays as frontier's."""
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "safety-profiler", "probe rules")
        result = g.fulfill_frontier(
            f.id,
            approach="x",
            messages_sent=["m"],
            target_responses=["r"],
            score=5,
            leaked_info="",
            reflection="",
            run_id="",
        )
        assert result.module == "safety-profiler"


# ---------------------------------------------------------------------------
# AttackGraph — queries
# ---------------------------------------------------------------------------

class TestAttackGraphQueries:
    @pytest.fixture
    def populated_graph(self):
        g = AttackGraph()
        root = g.ensure_root()
        # Dead node
        g.add_node(root.id, "authority-bias", "Stanford", score=1, reflection="detected")
        # Promising node
        promising = g.add_node(root.id, "foot-in-door", "philosophy", score=7, leaked_info="design principles")
        # Alive mid-range
        g.add_node(root.id, "cognitive-overload", "batch", score=3)
        # Frontier nodes
        g.add_frontier_node(promising.id, "foot-in-door", "ask about tools")
        g.add_frontier_node(promising.id, "foot-in-door", "ask about restrictions")
        # Human hint
        g.add_human_hint("try calendar API errors")
        return g

    def test_get_frontier_nodes(self, populated_graph):
        frontier = populated_graph.get_frontier_nodes()
        assert len(frontier) == 3
        # Human hint should be first
        assert frontier[0].source == "human"

    def test_get_promising_nodes(self, populated_graph):
        promising = populated_graph.get_promising_nodes()
        assert len(promising) == 1
        assert promising[0].module == "foot-in-door"
        assert promising[0].score == 7

    def test_get_dead_nodes(self, populated_graph):
        dead = populated_graph.get_dead_nodes()
        assert len(dead) == 1
        assert dead[0].module == "authority-bias"

    def test_get_explored_nodes(self, populated_graph):
        explored = populated_graph.get_explored_nodes()
        assert len(explored) == 3  # dead + promising + alive (not root, not frontiers)

    def test_get_best_score(self, populated_graph):
        assert populated_graph.get_best_score() == 7

    def test_get_path(self, populated_graph):
        frontier = populated_graph.get_frontier_nodes()
        # Pick a non-human frontier
        agent_frontier = [f for f in frontier if f.source == "agent"][0]
        path = populated_graph.get_path(agent_frontier.id)
        assert len(path) >= 3  # root → promising → frontier
        assert path[0].module == "root"

    def test_stats(self, populated_graph):
        stats = populated_graph.stats()
        assert stats["total"] == 7  # root + 3 explored + 3 frontier
        assert stats["best_score"] == 7
        assert stats["by_status"]["dead"] == 1
        assert stats["by_status"]["promising"] == 1
        assert stats["by_status"]["frontier"] == 3


# ---------------------------------------------------------------------------
# AttackGraph — formatting
# ---------------------------------------------------------------------------

class TestAttackGraphFormatting:
    def test_format_summary_empty(self):
        g = AttackGraph()
        g.ensure_root()
        summary = g.format_summary()
        assert "0" in summary or "best score: 0" in summary

    def test_format_summary_populated(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "foot-in-door", "philosophy", score=7, leaked_info="design principles")
        g.add_node(root.id, "authority-bias", "Stanford", score=1, reflection="detected")
        g.add_frontier_node(root.id, "test", "next move")
        g.add_human_hint("calendar API")

        summary = g.format_summary()
        assert "foot-in-door" in summary
        assert "Dead Ends" in summary or "dead" in summary.lower()
        assert "Frontier" in summary or "frontier" in summary.lower()
        assert "HUMAN" in summary

    def test_format_summary_exposes_frontier_ids(self):
        """Leader must see frontier IDs to reference them via frontier_id param."""
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "foo", "ask about tools")
        summary = g.format_summary()
        # The frontier's ID must appear so the leader can cite it
        assert f.id in summary
        # And the signature hint so the leader knows what to do
        assert "frontier_id" in summary

    def test_format_dead_ends_empty(self):
        g = AttackGraph()
        assert g.format_dead_ends() == "(none yet)"

    def test_format_explored_approaches_empty(self):
        g = AttackGraph()
        assert g.format_explored_approaches() == "(none yet)"


# ---------------------------------------------------------------------------
# AttackGraph — serialization
# ---------------------------------------------------------------------------

class TestAttackGraphSerialization:
    def test_json_round_trip(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "test-module", "test approach", score=5, leaked_info="something")
        g.add_frontier_node(root.id, "next", "explore more")
        g.add_human_hint("human says try this")
        g.run_counter = 3

        json_str = g.to_json()
        g2 = AttackGraph.from_json(json_str)

        assert len(g2) == len(g)
        assert g2.root_id == g.root_id
        assert g2.run_counter == 3
        assert g2.get_best_score() == g.get_best_score()

    def test_save_and_load(self, tmp_path):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "test", "approach", score=8)
        g.run_counter = 5

        path = tmp_path / "graph.json"
        g.save(path)
        assert path.exists()

        g2 = AttackGraph.load(path)
        assert len(g2) == len(g)
        assert g2.run_counter == 5

    def test_load_nonexistent(self, tmp_path):
        path = tmp_path / "nope.json"
        g = AttackGraph.load(path)
        assert len(g) == 0

    def test_save_creates_dirs(self, tmp_path):
        g = AttackGraph()
        g.ensure_root()
        path = tmp_path / "deep" / "nested" / "graph.json"
        g.save(path)
        assert path.exists()


# ---------------------------------------------------------------------------
# hash_target
# ---------------------------------------------------------------------------

class TestHashTarget:
    def test_deterministic(self):
        h1 = hash_target("websocket", url="wss://example.com/ws")
        h2 = hash_target("websocket", url="wss://example.com/ws")
        assert h1 == h2

    def test_different_targets(self):
        h1 = hash_target("websocket", url="wss://a.com/ws")
        h2 = hash_target("websocket", url="wss://b.com/ws")
        assert h1 != h2

    def test_case_insensitive(self):
        h1 = hash_target("WebSocket", url="WSS://Example.COM/ws")
        h2 = hash_target("websocket", url="wss://example.com/ws")
        assert h1 == h2

    def test_model_based(self):
        h1 = hash_target("openai", model="gpt-4")
        h2 = hash_target("openai", model="gpt-4")
        assert h1 == h2
        h3 = hash_target("openai", model="gpt-3.5")
        assert h1 != h3
