"""Tests for `_build_graph_context` — TAPER tier rendering.

The leader's user prompt should carry [T0]/[T1]/[T2] prefixes on every
frontier item, and emit a "climb the ladder" directive whenever lower-tier
items coexist with higher-tier items. HUMAN ★ hints still render first,
and the tier is a label only for those — it does not reorder them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mesmer.core.agent.prompt import _build_graph_context
from mesmer.core.constants import BudgetMode, NodeSource
from mesmer.core.graph import AttackGraph
from mesmer.core.module import ModuleConfig
from mesmer.core.registry import Registry


def _make_ctx(graph: AttackGraph, registry: Registry):
    """Minimal Context stand-in — prompt only reads a handful of attrs."""
    ctx = MagicMock()
    ctx.graph = graph
    ctx.registry = registry
    ctx.turn_budget = 10
    ctx.turns_used = 0
    ctx.budget_mode = BudgetMode.EXPLORE
    return ctx


def _registry_with(*mods: tuple[str, int]) -> Registry:
    r = Registry()
    for name, tier in mods:
        r.register(ModuleConfig(name=name, tier=tier))
    return r


class TestTierPrefixOnFrontier:
    def test_each_frontier_line_carries_tier_prefix(self):
        """Every frontier `[T{n}]` prefix reflects the module's declared tier."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(root.id, "direct-ask", "ask plainly")
        g.add_frontier_node(root.id, "foot-in-door", "warm up")

        r = _registry_with(("direct-ask", 0), ("foot-in-door", 2))
        out = _build_graph_context(_make_ctx(g, r))

        assert "[T0]" in out
        assert "[T2]" in out
        assert "direct-ask" in out
        assert "foot-in-door" in out

    def test_unknown_module_defaults_to_tier_2_label(self):
        """A frontier for a module not in the registry still renders — default T2."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(root.id, "mystery-module", "who knows")
        r = Registry()  # empty — mystery-module is unknown
        out = _build_graph_context(_make_ctx(g, r))
        assert "[T2]" in out
        assert "mystery-module" in out


class TestLadderDirective:
    def test_ladder_directive_emits_when_tiers_coexist(self):
        """Mixed-tier frontier → 'attempt T0 before higher-tier' directive."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(root.id, "direct-ask", "ask plainly")
        g.add_frontier_node(root.id, "foot-in-door", "warm up")
        r = _registry_with(("direct-ask", 0), ("foot-in-door", 2))
        out = _build_graph_context(_make_ctx(g, r))
        assert "Tier-0 frontier items available" in out
        assert "BEFORE higher-tier" in out

    def test_no_ladder_directive_when_all_same_tier(self):
        """Single-tier frontier → no nudge (there's no ladder to climb)."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_frontier_node(root.id, "foot-in-door", "warm up one")
        g.add_frontier_node(root.id, "authority-bias", "warm up two")
        r = _registry_with(("foot-in-door", 2), ("authority-bias", 2))
        out = _build_graph_context(_make_ctx(g, r))
        assert "frontier items available" not in out


class TestHumanPriority:
    def test_human_hint_renders_first_even_over_lower_tier_agent(self):
        """HUMAN ★ frontier surfaces before tier-0 agent-proposed frontier."""
        g = AttackGraph()
        root = g.ensure_root()
        # Agent proposes a tier-0 item first (sort: source_rank=1).
        g.add_frontier_node(root.id, "direct-ask", "agent proposed")
        # Human adds a tier-2 hint (sort: source_rank=0 — wins).
        g.add_frontier_node(
            root.id, "authority-bias", "human insight",
            source=NodeSource.HUMAN.value,
        )
        r = _registry_with(("direct-ask", 0), ("authority-bias", 2))
        out = _build_graph_context(_make_ctx(g, r))
        # Human line appears before the agent's tier-0 line.
        human_idx = out.find("human insight")
        agent_idx = out.find("agent proposed")
        assert human_idx != -1
        assert agent_idx != -1
        assert human_idx < agent_idx
        # Tier label is still printed on the human line ([T2]).
        human_line = out.splitlines()[
            next(i for i, line in enumerate(out.splitlines()) if "human insight" in line)
        ]
        assert "[T2]" in human_line
        assert "★" in human_line


class TestExploredPathsGrouping:
    def test_format_summary_grouped_by_tier_when_tiers_passed(self):
        """`format_summary(tiers=...)` renders `[T{n}]` on the Explored Paths list."""
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "direct-ask", "first probe tokens here", score=2)
        g.add_node(root.id, "foot-in-door", "cognitive angle tokens", score=7)
        tiers = {"direct-ask": 0, "foot-in-door": 2}
        out = g.format_summary(tiers=tiers)
        assert "## Explored Paths" in out
        # Each module line carries its tier prefix.
        paths = [line for line in out.splitlines() if "attempts" in line]
        t0_line = next(line for line in paths if "direct-ask" in line)
        t2_line = next(line for line in paths if "foot-in-door" in line)
        assert "[T0]" in t0_line
        assert "[T2]" in t2_line
        # Ordering — tier 0 before tier 2.
        assert paths.index(t0_line) < paths.index(t2_line)
