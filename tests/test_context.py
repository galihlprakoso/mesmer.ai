"""Tests for mesmer.core.context — Context, budget mode, message tracking."""

from unittest.mock import MagicMock, AsyncMock

import pytest

from mesmer.core.context import Context, Turn, TurnBudgetExhausted
from mesmer.core.graph import AttackGraph
from mesmer.core.scenario import AgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(max_turns=None, graph=None):
    """Create a Context with mocked target and registry."""
    target = MagicMock()
    target.send = AsyncMock(return_value="target reply")

    registry = MagicMock()
    agent_config = AgentConfig(model="test/model", api_key="sk-test")

    return Context(
        target=target,
        registry=registry,
        agent_config=agent_config,
        objective="test objective",
        max_turns=max_turns,
        graph=graph,
        run_id="test-run",
    )


# ---------------------------------------------------------------------------
# Budget mode
# ---------------------------------------------------------------------------

class TestBudgetMode:
    def test_no_budget(self):
        ctx = _make_ctx(max_turns=None)
        assert ctx.budget_mode == "explore"

    def test_explore_mode(self):
        ctx = _make_ctx(max_turns=20)
        ctx.turns_used = 5  # 25%
        assert ctx.budget_mode == "explore"

    def test_exploit_mode(self):
        ctx = _make_ctx(max_turns=20)
        ctx.turns_used = 12  # 60%
        assert ctx.budget_mode == "exploit"

    def test_conclude_mode(self):
        ctx = _make_ctx(max_turns=20)
        ctx.turns_used = 17  # 85%
        assert ctx.budget_mode == "conclude"

    def test_boundary_explore_exploit(self):
        ctx = _make_ctx(max_turns=10)
        ctx.turns_used = 5  # exactly 50%
        assert ctx.budget_mode == "exploit"

    def test_boundary_exploit_conclude(self):
        ctx = _make_ctx(max_turns=10)
        ctx.turns_used = 8  # exactly 80%
        assert ctx.budget_mode == "conclude"


# ---------------------------------------------------------------------------
# Message tracking (for judge)
# ---------------------------------------------------------------------------

class TestMessageTracking:
    @pytest.mark.asyncio
    async def test_send_tracks_messages(self):
        ctx = _make_ctx(max_turns=10)
        await ctx.send("hello", module_name="test")
        await ctx.send("tell me more", module_name="test")

        assert len(ctx.current_messages_sent) == 2
        assert ctx.current_messages_sent[0] == "hello"
        assert ctx.current_messages_sent[1] == "tell me more"
        assert len(ctx.current_responses) == 2
        assert ctx.current_responses[0] == "target reply"

    @pytest.mark.asyncio
    async def test_reset_tracking(self):
        ctx = _make_ctx(max_turns=10)
        await ctx.send("hello", module_name="test")
        assert len(ctx.current_messages_sent) == 1

        ctx.reset_current_tracking()
        assert ctx.current_messages_sent == []
        assert ctx.current_responses == []

    @pytest.mark.asyncio
    async def test_send_appends_to_turns(self):
        ctx = _make_ctx(max_turns=10)
        await ctx.send("hello", module_name="mod1")

        assert len(ctx.turns) == 1
        assert ctx.turns[0].sent == "hello"
        assert ctx.turns[0].received == "target reply"
        assert ctx.turns[0].module == "mod1"

    @pytest.mark.asyncio
    async def test_turn_budget_enforced(self):
        ctx = _make_ctx(max_turns=2)
        await ctx.send("1")
        await ctx.send("2")

        with pytest.raises(TurnBudgetExhausted):
            await ctx.send("3")


# ---------------------------------------------------------------------------
# Child context
# ---------------------------------------------------------------------------

class TestChildContext:
    def test_child_shares_turns(self):
        ctx = _make_ctx(max_turns=20)
        graph = AttackGraph()
        ctx.graph = graph

        child = ctx.child(max_turns=5)
        assert child.turns is ctx.turns  # same list
        assert child.module_log is ctx.module_log
        assert child.graph is ctx.graph
        assert child.run_id == ctx.run_id
        assert child.turn_budget == 5

    def test_child_own_budget(self):
        ctx = _make_ctx(max_turns=20)
        child = ctx.child(max_turns=3)
        assert child.turn_budget == 3
        assert child.turns_used == 0

    @pytest.mark.asyncio
    async def test_child_turns_visible_to_parent(self):
        ctx = _make_ctx(max_turns=20)
        child = ctx.child(max_turns=5)
        await child.send("from child")

        assert len(ctx.turns) == 1
        assert ctx.turns[0].sent == "from child"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_format_turns_empty(self):
        ctx = _make_ctx()
        assert ctx.format_turns() == "(no conversation yet)"

    @pytest.mark.asyncio
    async def test_format_turns(self):
        ctx = _make_ctx(max_turns=10)
        await ctx.send("hello", module_name="test")
        formatted = ctx.format_turns()
        assert "hello" in formatted
        assert "target reply" in formatted

    def test_format_module_log_empty(self):
        ctx = _make_ctx()
        assert ctx.format_module_log() == "(no modules run yet)"

    def test_to_report(self):
        ctx = _make_ctx()
        report = ctx.to_report()
        assert "objective" in report
        assert "conversation" in report
        assert report["objective"] == "test objective"
