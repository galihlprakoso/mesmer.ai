"""Tests for mesmer.core.context — Context, budget mode, message tracking."""

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from mesmer.core.constants import LogEvent, ScenarioMode
from mesmer.core.context import Context, Turn, TurnBudgetExhausted, is_target_error
from mesmer.core.graph import AttackGraph
from mesmer.core.module import ModuleConfig
from mesmer.core.scenario import AgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(max_turns=None, graph=None):
    """Create a Context with mocked target and registry."""
    target = MagicMock()
    target.send = AsyncMock(return_value="target reply")
    target.reset = AsyncMock(return_value=None)

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


# ---------------------------------------------------------------------------
# Target-reset / fresh-session (P0)
# ---------------------------------------------------------------------------

class TestTargetReset:
    """Behavior around the `reset_target` module flag and the resulting
    `target_fresh_session` signal the child context carries.
    """

    def test_default_flag_is_false(self):
        ctx = _make_ctx()
        assert ctx.target_fresh_session is False
        assert ctx._target_reset_at == 0

    def test_child_inherits_reset_marker(self):
        ctx = _make_ctx()
        ctx._target_reset_at = 5
        child = ctx.child()
        assert child._target_reset_at == 5
        # Fresh-session flag defaults to False for plain child() — it's only
        # set True when run_module decides a reset happened.
        assert child.target_fresh_session is False

    def test_child_accepts_fresh_session_flag(self):
        ctx = _make_ctx()
        child = ctx.child(target_fresh_session=True)
        assert child.target_fresh_session is True

    @pytest.mark.asyncio
    async def test_run_module_resets_target_when_flag_set(self):
        ctx = _make_ctx(max_turns=10)
        module = ModuleConfig(
            name="fresh-mod",
            description="test",
            reset_target=True,
        )
        ctx.registry.get = MagicMock(return_value=module)

        await ctx.send("seed turn")
        assert len(ctx.turns) == 1
        assert ctx._target_reset_at == 0

        with patch("mesmer.core.loop.run_react_loop", new=AsyncMock(return_value="done")):
            await ctx.run_module("fresh-mod", "do it")

        ctx.target.reset.assert_awaited_once()
        # Reset marker should advance to the turn count at the moment of reset.
        assert ctx._target_reset_at == 1

    @pytest.mark.asyncio
    async def test_run_module_does_not_reset_when_flag_unset(self):
        ctx = _make_ctx(max_turns=10)
        module = ModuleConfig(
            name="stateful-mod",
            description="test",
            reset_target=False,
        )
        ctx.registry.get = MagicMock(return_value=module)

        await ctx.send("seed turn")

        with patch("mesmer.core.loop.run_react_loop", new=AsyncMock(return_value="done")):
            await ctx.run_module("stateful-mod", "do it")

        ctx.target.reset.assert_not_called()
        assert ctx._target_reset_at == 0

    @pytest.mark.asyncio
    async def test_run_module_propagates_fresh_session_to_child(self):
        ctx = _make_ctx(max_turns=10)
        module = ModuleConfig(name="fresh-mod", description="t", reset_target=True)
        ctx.registry.get = MagicMock(return_value=module)

        observed = {}

        async def _fake_loop(mod, child_ctx, instruction, **kwargs):
            observed["fresh"] = child_ctx.target_fresh_session
            observed["reset_at"] = child_ctx._target_reset_at
            return "done"

        with patch("mesmer.core.loop.run_react_loop", new=_fake_loop):
            await ctx.run_module("fresh-mod", "do it")

        assert observed["fresh"] is True

    @pytest.mark.asyncio
    async def test_run_module_survives_reset_failure(self):
        ctx = _make_ctx(max_turns=10)
        module = ModuleConfig(name="fresh-mod", description="t", reset_target=True)
        ctx.registry.get = MagicMock(return_value=module)
        ctx.target.reset = AsyncMock(side_effect=RuntimeError("boom"))

        observed = {}

        async def _fake_loop(mod, child_ctx, instruction, **kwargs):
            observed["fresh"] = child_ctx.target_fresh_session
            return "done"

        with patch("mesmer.core.loop.run_react_loop", new=_fake_loop):
            result = await ctx.run_module("fresh-mod", "do it")

        # Reset failure is non-fatal — module still runs, but without fresh flag.
        assert result == "done"
        assert observed["fresh"] is False


class TestScenarioModePropagation:
    """C0 / C1 — ScenarioMode threads from Context through child() and
    controls whether ``run_module()`` honours ``reset_target`` on the module."""

    def test_default_scenario_mode_is_trials(self):
        ctx = _make_ctx()
        assert ctx.scenario_mode == ScenarioMode.TRIALS

    def test_child_inherits_scenario_mode_trials(self):
        ctx = _make_ctx()
        ctx.scenario_mode = ScenarioMode.TRIALS
        assert ctx.child().scenario_mode == ScenarioMode.TRIALS

    def test_child_inherits_scenario_mode_continuous(self):
        ctx = _make_ctx()
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        # Mode must survive arbitrarily deep nesting — sub-modules of
        # sub-modules must all see the same scenario mode as the root.
        assert ctx.child().child().child().scenario_mode == ScenarioMode.CONTINUOUS

    @pytest.mark.asyncio
    async def test_run_module_skips_reset_in_continuous_mode(self):
        """C1 — even if a module YAML declares reset_target: true, the
        reset is skipped under CONTINUOUS to preserve the live chat."""
        ctx = _make_ctx(max_turns=10)
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        module = ModuleConfig(
            name="wants-reset",
            description="t",
            reset_target=True,  # will be warn-ignored
        )
        ctx.registry.get = MagicMock(return_value=module)

        events: list[tuple[str, str]] = []
        def log(e, d=""):
            events.append((e, d))

        with patch("mesmer.core.loop.run_react_loop", new=AsyncMock(return_value="done")):
            await ctx.run_module("wants-reset", "do it", log=log)

        ctx.target.reset.assert_not_called()
        assert ctx._target_reset_at == 0  # no reset advance
        # A MODE_OVERRIDE event must be logged so scenario authors notice.
        override_events = [e for e in events if e[0] == LogEvent.MODE_OVERRIDE.value]
        assert len(override_events) == 1
        assert "wants-reset" in override_events[0][1]
        assert "CONTINUOUS" in override_events[0][1]

    @pytest.mark.asyncio
    async def test_run_module_still_resets_in_trials_mode(self):
        """Regression: the CONTINUOUS skip-reset branch must not leak into
        TRIALS runs — existing reset_target behaviour is unchanged."""
        ctx = _make_ctx(max_turns=10)
        assert ctx.scenario_mode == ScenarioMode.TRIALS
        module = ModuleConfig(name="wants-reset", description="t", reset_target=True)
        ctx.registry.get = MagicMock(return_value=module)

        with patch("mesmer.core.loop.run_react_loop", new=AsyncMock(return_value="done")):
            await ctx.run_module("wants-reset", "do it")

        ctx.target.reset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_module_continuous_no_reset_flag_is_noop(self):
        """No MODE_OVERRIDE logs when module.reset_target is False —
        the warning only fires for the *override* case."""
        ctx = _make_ctx(max_turns=10)
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        module = ModuleConfig(name="plain", description="t", reset_target=False)
        ctx.registry.get = MagicMock(return_value=module)

        events: list[tuple[str, str]] = []
        with patch("mesmer.core.loop.run_react_loop", new=AsyncMock(return_value="done")):
            await ctx.run_module("plain", "do it", log=lambda e, d="": events.append((e, d)))

        ctx.target.reset.assert_not_called()
        assert not any(e[0] == LogEvent.MODE_OVERRIDE.value for e in events)


class TestTargetErrorDetection:
    """P4 — is_target_error heuristic + Turn tagging in Context.send()."""

    @pytest.mark.parametrize("reply,expected", [
        ("", True),
        ("   ", True),
        (None, True),
        ("(timeout — no response)", True),
        ("I couldn't process that request.", True),
        ("Internal Server Error", True),
        ("Service unavailable", True),
        ("bad gateway", True),
        ("Gateway Timeout", True),
        ("Rate limit exceeded", True),
        ("Too Many Requests", True),
        ("I cannot help with that request.", False),  # real refusal
        ("Sure, here you go!", False),
        ("I don't share my system prompt, sorry.", False),  # real refusal
    ])
    def test_is_target_error_classification(self, reply, expected):
        assert is_target_error(reply) is expected

    @pytest.mark.asyncio
    async def test_send_tags_clean_reply_as_not_error(self):
        ctx = _make_ctx(max_turns=5)
        ctx.target.send = AsyncMock(return_value="a real reply")
        await ctx.send("probe")
        assert ctx.turns[-1].is_error is False

    @pytest.mark.asyncio
    async def test_send_tags_timeout_reply_as_error(self):
        ctx = _make_ctx(max_turns=5)
        ctx.target.send = AsyncMock(return_value="(timeout — no response)")
        await ctx.send("probe")
        assert ctx.turns[-1].is_error is True

    @pytest.mark.asyncio
    async def test_send_tags_empty_reply_as_error(self):
        ctx = _make_ctx(max_turns=5)
        ctx.target.send = AsyncMock(return_value="")
        await ctx.send("probe")
        assert ctx.turns[-1].is_error is True

    def test_turn_is_error_default_false(self):
        t = Turn(sent="hi", received="hello")
        assert t.is_error is False

    def test_turn_round_trip_preserves_is_error(self):
        t = Turn(sent="hi", received="(timeout)", is_error=True)
        data = t.to_dict()
        assert data["is_error"] is True


class TestAttackerModelRotation:
    """P5 — attacker-model ensemble rotation via ctx.run_module(), and the
    judge model staying stable regardless of the attacker rotation."""

    def _make_ensemble_ctx(self, models, judge_model=""):
        target = MagicMock()
        target.send = AsyncMock(return_value="ok")
        target.reset = AsyncMock(return_value=None)
        registry = MagicMock()
        agent_config = AgentConfig(models=models, judge_model=judge_model, api_key="sk")
        return Context(
            target=target, registry=registry, agent_config=agent_config,
            objective="o", run_id="r",
        )

    def test_agent_model_returns_override_first(self):
        ctx = self._make_ensemble_ctx(["a", "b"])
        ctx.attacker_model_override = "b"
        assert ctx.agent_model == "b"

    def test_agent_model_falls_back_to_config(self):
        ctx = self._make_ensemble_ctx(["a", "b"])
        assert ctx.agent_model == "a"  # config's current base

    def test_resolve_model_attacker_uses_override(self):
        ctx = self._make_ensemble_ctx(["a", "b"])
        ctx.attacker_model_override = "b"
        assert ctx._resolve_model("attacker") == "b"

    def test_resolve_model_judge_ignores_override(self):
        """Judge role must use judge_model (or config.model), never the
        attacker override — stops the judge from drifting with rotation."""
        ctx = self._make_ensemble_ctx(["a", "b", "c"], judge_model="judge-x")
        ctx.attacker_model_override = "b"
        assert ctx._resolve_model("judge") == "judge-x"

    def test_child_inherits_override_when_not_overridden_again(self):
        ctx = self._make_ensemble_ctx(["a", "b"])
        ctx.attacker_model_override = "b"
        child = ctx.child()
        assert child.attacker_model_override == "b"

    def test_child_accepts_explicit_override(self):
        ctx = self._make_ensemble_ctx(["a", "b"])
        child = ctx.child(attacker_model_override="b")
        assert child.attacker_model_override == "b"

    @pytest.mark.asyncio
    async def test_run_module_rotates_and_binds_child(self):
        """Running a sub-module advances the rotation and binds the chosen
        model onto the child context."""
        ctx = self._make_ensemble_ctx(["a", "b", "c"])
        module = ModuleConfig(name="mod", description="t")
        ctx.registry.get = MagicMock(return_value=module)

        observed = []

        async def _fake_loop(mod, child_ctx, instruction, **kwargs):
            observed.append(child_ctx.attacker_model_override)
            return "done"

        with patch("mesmer.core.loop.run_react_loop", new=_fake_loop):
            await ctx.run_module("mod", "do")
            await ctx.run_module("mod", "do")
            await ctx.run_module("mod", "do")

        assert observed == ["a", "b", "c"]


class TestContextDepth:
    """P6 — depth tracks nesting so the CLI can distinguish iteration
    counters of parent vs child modules."""

    def test_root_depth_is_zero(self):
        ctx = _make_ctx()
        assert ctx.depth == 0

    def test_child_increments_depth(self):
        ctx = _make_ctx()
        c1 = ctx.child()
        c2 = c1.child()
        assert c1.depth == 1
        assert c2.depth == 2

    def test_explicit_depth_preserved(self):
        ctx = _make_ctx()
        # child() at depth 3 produces a depth-4 grandchild.
        deep = Context(
            target=ctx.target, registry=ctx.registry,
            agent_config=ctx.agent_config, depth=3,
        )
        assert deep.depth == 3
        assert deep.child().depth == 4


class TestFormatSessionTurns:
    def test_empty_when_no_turns(self):
        ctx = _make_ctx()
        assert ctx.format_session_turns() == "(no conversation in this target session yet)"

    @pytest.mark.asyncio
    async def test_excludes_pre_reset_turns(self):
        ctx = _make_ctx(max_turns=10)
        await ctx.send("old", module_name="prior")
        await ctx.send("older", module_name="prior")
        ctx._target_reset_at = len(ctx.turns)  # simulate reset
        await ctx.send("new", module_name="current")

        formatted = ctx.format_session_turns()
        assert "new" in formatted
        assert "old" not in formatted
        assert "older" not in formatted

    @pytest.mark.asyncio
    async def test_format_turns_still_shows_all(self):
        ctx = _make_ctx(max_turns=10)
        await ctx.send("old")
        ctx._target_reset_at = len(ctx.turns)
        await ctx.send("new")

        # The unfiltered helper still returns everything — it's the attacker's
        # intel view, not the target's session view.
        all_formatted = ctx.format_turns()
        assert "old" in all_formatted
        assert "new" in all_formatted
