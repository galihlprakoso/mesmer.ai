"""Tests for mesmer.core.graph — AttackGraph and AttackNode."""


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

    # --- P2 graph.propose_frontier — MCTS Selection as pure code ---

    def test_propose_frontier_prefers_untried(self):
        """Untried modules have infinite UCB — they come first."""
        g = AttackGraph()
        root = g.ensure_root()
        # 'authority-bias' has been run and scored well.
        g.add_node(
            root.id, "authority-bias",
            approach="Stanford researcher claim persuading target",
            score=7,
        )
        # 'foot-in-door' is untried.
        candidates = g.propose_frontier(
            ["authority-bias", "foot-in-door", "anchoring"],
            top_k=3,
        )
        modules = [c["module"] for c in candidates]
        # foot-in-door and anchoring are both untried; whichever order, they
        # must both rank above the tried authority-bias.
        assert modules.index("foot-in-door") < modules.index("authority-bias")
        assert modules.index("anchoring") < modules.index("authority-bias")

    def test_propose_frontier_ranks_tried_modules_by_best_score(self):
        """Among tried modules, higher best-score comes first."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "authority-bias", "angle one words plenty", score=4)
        g.add_node(root.id, "foot-in-door", "angle two words plenty", score=7)
        candidates = g.propose_frontier(
            ["authority-bias", "foot-in-door"],
            top_k=2,
        )
        # foot-in-door (score 7) outranks authority-bias (score 4).
        assert candidates[0]["module"] == "foot-in-door"
        assert candidates[1]["module"] == "authority-bias"

    def test_propose_frontier_excludes_all_dead(self):
        """If every attempt of a module is dead, exclude it."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(
            root.id, "authority-bias", "claim one words plenty",
            score=1, reflection="detected instantly",
        )
        g.add_node(
            root.id, "authority-bias", "claim two words plenty",
            score=2, reflection="rebuffed again",
        )
        # foot-in-door has a live score-5 attempt — should survive.
        g.add_node(
            root.id, "foot-in-door", "ladder approach words plenty",
            score=5,
        )
        candidates = g.propose_frontier(
            ["authority-bias", "foot-in-door"],
            top_k=3,
        )
        modules = [c["module"] for c in candidates]
        assert "authority-bias" not in modules
        assert "foot-in-door" in modules

    def test_propose_frontier_respects_top_k(self):
        g = AttackGraph()
        g.ensure_root()
        candidates = g.propose_frontier(
            ["a", "b", "c", "d", "e"],
            top_k=2,
        )
        assert len(candidates) == 2

    def test_propose_frontier_empty_available_modules(self):
        g = AttackGraph()
        g.ensure_root()
        assert g.propose_frontier([], top_k=3) == []

    def test_propose_frontier_attaches_to_given_parent(self):
        g = AttackGraph()
        root = g.ensure_root()
        parent = g.add_node(root.id, "foo", "longer approach words here", score=5)
        candidates = g.propose_frontier(
            ["bar", "baz"],
            parent_id=parent.id,
            top_k=2,
        )
        assert all(c["parent_id"] == parent.id for c in candidates)

    def test_propose_frontier_attaches_to_root_by_default(self):
        g = AttackGraph()
        root = g.ensure_root()
        candidates = g.propose_frontier(["foo"], top_k=1)
        assert candidates[0]["parent_id"] == root.id

    def test_propose_frontier_rationale_distinguishes_untried_vs_deepen(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "tried-mod", "prior angle words plenty", score=6)
        candidates = g.propose_frontier(
            ["untried-mod", "tried-mod"],
            top_k=2,
        )
        by_mod = {c["module"]: c for c in candidates}
        assert "untried" in by_mod["untried-mod"]["rationale"]
        assert "deepen" in by_mod["tried-mod"]["rationale"]

    # --- TAPER — tier-aware propose_frontier (simple before complex) ---

    def test_tier_gate_prefers_lowest_live_tier_untried(self):
        """With a tier-0 untried and tier-2 untried, tier-0 wins — naive first."""
        g = AttackGraph()
        g.ensure_root()
        tiers = {"direct-ask": 0, "foot-in-door": 2}
        candidates = g.propose_frontier(
            ["direct-ask", "foot-in-door"],
            top_k=2,
            tiers=tiers,
        )
        # Gate filters to tier-0 only; foot-in-door doesn't appear until T0
        # is exhausted.
        assert [c["module"] for c in candidates] == ["direct-ask"]
        assert candidates[0]["tier"] == 0

    def test_tier_gate_skips_dead_tier_to_next(self):
        """Tier 0 dead → gate skips to tier 1."""
        g = AttackGraph()
        root = g.ensure_root()
        # Both tier-0 modules are dead.
        g.add_node(
            root.id, "direct-ask", "ask words plenty here",
            score=1, reflection="rebuffed",
        )
        g.add_node(
            root.id, "instruction-recital", "recite words plenty here",
            score=2, reflection="rebuffed",
        )
        tiers = {
            "direct-ask": 0,
            "instruction-recital": 0,
            "delimiter-injection": 1,
            "foot-in-door": 2,
        }
        candidates = g.propose_frontier(
            list(tiers.keys()),
            top_k=3,
            tiers=tiers,
        )
        modules = [c["module"] for c in candidates]
        # Gate promoted to tier-1; tier-0 all-dead excluded entirely.
        assert "direct-ask" not in modules
        assert "instruction-recital" not in modules
        assert "delimiter-injection" in modules
        # foot-in-door (tier 2) stays filtered out while tier 1 is live.
        assert "foot-in-door" not in modules

    def test_tier_gate_keeps_promising_tier_2_over_new_tier_0(self):
        """A promising (score>=7) tier-2 counts as live — gate surfaces
        both tiers when tier-0 is also untried.

        Note: the gate's contract is "lowest live tier". A tier-0 untried
        module wins via the gate; a tier-2 promising module lives through
        the escape hatch only if tier-0 is NOT live. This test verifies the
        escape hatch triggers when every lower tier is stale.
        """
        g = AttackGraph()
        root = g.ensure_root()
        # Tier-0 has been tried but scored poorly (stale but not dead).
        g.add_node(
            root.id, "direct-ask", "probe words plenty here",
            score=3,
        )
        # Tier-2 has a promising lead.
        g.add_node(
            root.id, "authority-bias", "promising words plenty here",
            score=8,
        )
        tiers = {"direct-ask": 0, "authority-bias": 2}
        candidates = g.propose_frontier(
            list(tiers.keys()),
            top_k=3,
            tiers=tiers,
        )
        modules = [c["module"] for c in candidates]
        # Escape hatch: every tier has only tried-unpromising or tried-
        # promising members; with no untried and no promising tier-0, the
        # promising tier-2 deserves a shot.
        assert "authority-bias" in modules

    def test_tier_gate_noop_when_no_tiers_passed(self):
        """Legacy callers without ``tiers`` see unchanged cross-tier ranking."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "a", "first angle words plenty", score=4)
        # Without tiers, this matches the pre-TAPER behaviour exactly.
        candidates = g.propose_frontier(["a", "b"], top_k=2)
        modules = [c["module"] for c in candidates]
        # 'b' is untried → first. 'a' is tried but not dead → second.
        assert modules == ["b", "a"]
        # Default tier is 2 for unmapped modules.
        assert all(c["tier"] == 2 for c in candidates)

    def test_tier_field_present_in_returned_dicts(self):
        """Every candidate dict carries its module's tier — callers render it."""
        g = AttackGraph()
        g.ensure_root()
        tiers = {"t0-mod": 0, "t1-mod": 1}
        candidates = g.propose_frontier(
            ["t0-mod", "t1-mod"],
            top_k=2,
            tiers=tiers,
        )
        by_mod = {c["module"]: c for c in candidates}
        # Gate filters to T0 only; the surviving dict carries tier=0.
        assert by_mod["t0-mod"]["tier"] == 0

    def test_escape_hatch_all_tiers_saturated(self):
        """All tiers tried + unpromising → escape hatch returns best across tiers."""
        g = AttackGraph()
        root = g.ensure_root()
        # Tier-0: tried, score below promising threshold, not dead.
        g.add_node(root.id, "t0", "t0 angle words plenty", score=3)
        # Tier-2: tried, score below promising threshold, not dead.
        g.add_node(root.id, "t2", "t2 angle words plenty", score=4)
        tiers = {"t0": 0, "t2": 2}
        candidates = g.propose_frontier(
            list(tiers.keys()),
            top_k=2,
            tiers=tiers,
        )
        modules = {c["module"] for c in candidates}
        # Escape hatch fires — both surface for cross-tier ranking; t2 wins
        # on score but both are present.
        assert modules == {"t0", "t2"}

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
        # Frontier was created suggesting target-profiler, but the leader
        # decides to call narrative-transport on that same refinement slot.
        f = g.add_frontier_node(root.id, "target-profiler", "probe rules")
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
        f = g.add_frontier_node(root.id, "target-profiler", "probe rules")
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
        assert result.module == "target-profiler"


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


# ---------------------------------------------------------------------------
# latest_explored_node (C5 — used by CONTINUOUS-mode attach resolution)
# ---------------------------------------------------------------------------

class TestModuleOutputPersistence:
    """AttackNode.module_output — the canonical persisted form of a
    module's conclude() text.
    """

    def test_add_node_persists_module_output(self):
        g = AttackGraph()
        root = g.ensure_root()
        node = g.add_node(
            root.id, "target-profiler", "profile defences",
            score=8,
            module_output="## Identity\n- claimed_model: llama-3.1-8b\n",
            run_id="r",
        )
        assert node.module_output.startswith("## Identity")

    def test_module_output_round_trips_through_json(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(
            root.id, "target-profiler", "probe", score=8,
            module_output="DOSSIER TEXT", run_id="r",
        )
        g2 = AttackGraph.from_json(g.to_json())
        # Ordered timeline round-trips the module_output field.
        history = g2.conversation_history()
        assert len(history) == 1
        assert history[0].module_output == "DOSSIER TEXT"

    def test_fulfill_frontier_persists_module_output(self):
        """A leader that executes a frontier_id should still have its
        module_output recorded on the fulfilled node."""
        g = AttackGraph()
        root = g.ensure_root()
        f = g.add_frontier_node(root.id, "target-profiler", "probe")
        result = g.fulfill_frontier(
            f.id,
            approach="probe defences",
            messages_sent=["hi"],
            target_responses=["hello"],
            score=7,
            module_output="DOSSIER FROM FRONTIER",
            run_id="r",
        )
        assert result is not None
        assert result.module_output == "DOSSIER FROM FRONTIER"


class TestConversationHistory:
    """AttackGraph.conversation_history — ordered timeline view.

    Paired with Scratchpad (current-state KV) this is how the framework
    surfaces "what happened, when" to every module's user prompt. Same
    underlying data as the tree; different read axis.
    """

    def test_empty_graph_returns_empty_history(self):
        g = AttackGraph()
        g.ensure_root()
        assert g.conversation_history() == []
        assert g.render_conversation_history() == ""

    def test_history_orders_by_timestamp_oldest_first(self):
        import time as _time
        g = AttackGraph()
        root = g.ensure_root()
        # Add in reverse-time order to prove the sort, not insertion order.
        later = g.add_node(root.id, "late", "a1", score=5,
                           module_output="LATE", run_id="r")
        later.timestamp = _time.time()
        earlier = g.add_node(root.id, "early", "a2", score=5,
                             module_output="EARLY", run_id="r")
        earlier.timestamp = _time.time() - 100
        hist = g.conversation_history()
        assert [n.module for n in hist] == ["early", "late"]

    def test_history_excludes_frontier_and_root(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(root.id, "proposed", "frontier angle")
        g.add_node(root.id, "real", "real angle", score=5,
                   module_output="R", run_id="r")
        hist = g.conversation_history()
        mods = [n.module for n in hist]
        assert "proposed" not in mods
        assert "root" not in mods
        assert "real" in mods

    def test_history_includes_cross_run_turns(self):
        """A turn from a previous run must remain visible — that's how
        mesmer gets smarter over time."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "target-profiler", "r1 probe",
                   score=8, module_output="R1 DOSSIER", run_id="run-A")
        g.add_node(root.id, "direct-ask", "r2 ask",
                   score=3, module_output="", run_id="run-B")
        hist = g.conversation_history()
        run_ids = {n.run_id for n in hist}
        assert run_ids == {"run-A", "run-B"}

    def test_render_shows_module_score_approach_output(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "target-profiler", "profile defences",
                   score=8, module_output="DOSSIER", run_id="r")
        out = g.render_conversation_history()
        assert "target-profiler" in out
        assert "score 8" in out
        assert "profile defences" in out
        assert "DOSSIER" in out

    def test_render_caps_to_last_n(self):
        g = AttackGraph()
        root = g.ensure_root()
        for i in range(20):
            g.add_node(root.id, f"mod{i}", f"a{i}", score=5,
                       module_output=f"o{i}", run_id="r")
        out = g.render_conversation_history(last_n=3)
        # Only the last three mods appear.
        assert "mod17" in out
        assert "mod18" in out
        assert "mod19" in out
        assert "mod0" not in out
        assert "mod10" not in out

    def test_render_truncates_long_turn_output_with_notice(self):
        """No silent truncation — the reader sees exactly how much was
        clipped and where the full text lives."""
        g = AttackGraph()
        root = g.ensure_root()
        huge = "X" * 5000
        g.add_node(root.id, "verbose", "a", score=5,
                   module_output=huge, run_id="r")
        out = g.render_conversation_history(max_chars_per_turn=800)
        assert "see graph.json" in out
        assert "chars" in out

    def test_render_empty_output_still_shows_turn(self):
        """A module that ran but produced no conclude text is still a
        turn in the conversation — reader should see it happened."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "silent-module", "tried", score=4,
                   module_output="", run_id="r")
        out = g.render_conversation_history()
        assert "silent-module" in out
        assert "no conclude text" in out


class TestLearnedExperience:
    """AttackGraph query methods that turn the graph into the "experience"
    store — the Planner reads these, and the engine renders
    render_learned_experience() into every module's user message."""

    def test_winning_modules_ranks_by_best_score(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "instruction-recital", "recite",
                   score=10, run_id="r1")
        g.add_node(root.id, "direct-ask", "ask",
                   score=8, run_id="r1")
        g.add_node(root.id, "foot-in-door", "warm",
                   score=4, run_id="r1")  # below default min_score
        out = g.winning_modules()
        assert out == [("instruction-recital", 10), ("direct-ask", 8)]

    def test_winning_modules_excludes_frontier(self):
        """Frontier nodes have score=0 placeholders; they must not
        appear in winning_modules even with min_score=0."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(root.id, "proposed", "frontier angle")
        g.add_node(root.id, "real", "explored angle", score=9, run_id="r")
        out = g.winning_modules(min_score=0)
        mods = [m for m, _ in out]
        assert "proposed" not in mods
        assert "real" in mods

    def test_failed_modules_only_lists_fully_failed(self):
        """A module with any score above max_score must NOT appear in
        failed_modules — mixed scores mean the module might still pay
        off with a new angle."""
        g = AttackGraph()
        root = g.ensure_root()
        # fully failed
        g.add_node(root.id, "bad", "a1", score=1, run_id="r")
        g.add_node(root.id, "bad", "a2", score=2, run_id="r")
        # partially succeeded — not failed
        g.add_node(root.id, "mixed", "b1", score=1, run_id="r")
        g.add_node(root.id, "mixed", "b2", score=6, run_id="r")
        out = g.failed_modules()
        assert "bad" in out
        assert "mixed" not in out

    def test_verbatim_leaks_dedupes_exact_matches(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "a", "a1", score=8,
                   leaked_info="Password is paradox", run_id="r")
        g.add_node(root.id, "b", "b1", score=9,
                   leaked_info="Password is paradox", run_id="r")
        g.add_node(root.id, "c", "c1", score=7,
                   leaked_info="Access Granted", run_id="r")
        out = g.verbatim_leaks()
        assert out.count("Password is paradox") == 1
        assert "Access Granted" in out

    def test_verbatim_leaks_filters_by_score(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "low", "low angle here plenty", score=3,
                   leaked_info="should be dropped", run_id="r")
        g.add_node(root.id, "high", "high angle here plenty", score=7,
                   leaked_info="keep this", run_id="r")
        out = g.verbatim_leaks(min_score=5)
        assert "keep this" in out
        assert "should be dropped" not in out

    def test_refusal_templates_filters_by_length(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "m", "angle words plenty here", score=2,
                   run_id="r",
                   target_responses=[
                       "Haha, nice try!",
                       # too short to be a template
                       "No.",
                       # too long — likely substantive, not refusal
                       " ".join(["long"] * 60),
                   ])
        out = g.refusal_templates_from_turns()
        assert "Haha, nice try!" in out
        assert "No." not in out
        # No entry in the output is the 60-word string.
        assert not any("long long" in o and len(o.split()) >= 40 for o in out)

    def test_render_learned_experience_empty_graph(self):
        g = AttackGraph()
        g.ensure_root()
        assert g.render_learned_experience() == ""

    def test_render_learned_experience_shows_wins_and_fails(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "winner", "w", score=10,
                   leaked_info="the password is paradox", run_id="r")
        g.add_node(root.id, "loser", "l", score=1, run_id="r")
        out = g.render_learned_experience()
        assert "winner" in out
        assert "loser" in out
        assert "Modules that worked" in out
        assert "Modules that failed" in out
        assert "Verbatim leaks" in out

    def test_render_truncation_shows_more_suffix(self):
        g = AttackGraph()
        root = g.ensure_root()
        # 10 winning modules, max_entries=3
        for i in range(10):
            g.add_node(root.id, f"mod{i}", f"a{i}", score=10, run_id="r")
        out = g.render_learned_experience(max_entries=3)
        assert "(+7 more)" in out


class TestLatestExploredNode:
    def test_empty_graph_returns_none(self):
        """No explored nodes yet → None. Callers fall back to root."""
        g = AttackGraph()
        g.ensure_root()
        assert g.latest_explored_node() is None

    def test_only_root_returns_none(self):
        """Root is not considered explored — it's the origin marker."""
        g = AttackGraph()
        g.ensure_root()
        assert g.latest_explored_node() is None

    def test_returns_highest_timestamp_explored(self):
        """When multiple explored nodes exist, pick the most recent by
        timestamp — that's the chain's current leaf in CONTINUOUS mode."""
        g = AttackGraph()
        root = g.ensure_root()
        import time as _t
        g.add_node(root.id, "m", "first angle words plenty", score=4)
        _t.sleep(0.001)  # force strict ordering across dataclass timestamps
        g.add_node(root.id, "m", "second angle words plenty", score=4)
        _t.sleep(0.001)
        third = g.add_node(root.id, "m", "third angle words plenty", score=4)
        leaf = g.latest_explored_node()
        assert leaf is not None
        assert leaf.id == third.id

    def test_frontier_nodes_do_not_count(self):
        """Frontier nodes are unexplored proposals — the leaf of the live
        chain is whatever was actually EXECUTED most recently."""
        g = AttackGraph()
        root = g.ensure_root()
        explored = g.add_node(root.id, "m", "real move words plenty", score=5)
        frontier = g.add_frontier_node(root.id, "m", "suggested next move")
        leaf = g.latest_explored_node()
        assert leaf is not None
        assert leaf.id == explored.id
        assert leaf.id != frontier.id

    def test_dead_nodes_still_count_as_leaf(self):
        """A dead node is STILL the latest executed move — in a continuous
        conversation, its presence is what the target remembers, even if
        the technique failed. Next move extends from that state, not root."""
        g = AttackGraph()
        root = g.ensure_root()
        dead = g.add_node(
            root.id, "m", "failed angle words plenty",
            score=1, reflection="flat refusal",
        )
        leaf = g.latest_explored_node()
        assert leaf is not None
        assert leaf.id == dead.id
