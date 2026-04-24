"""Tests for the forensic trace events added in TAPER Phase A+.

Verifies each new :class:`LogEvent` fires at the right seam with a
structured JSON payload the bench artifact can consume:

  * ``tier_gate``     — one per frontier expansion, from _reflect_and_expand.
  * ``judge_verdict`` — one per judged sub-module, from _judge_module_result.
  * ``delegate``      — now carries full instruction/tier/frontier_id.
  * ``llm_completion``— one per ctx.completion call (any role).

These are what lets us answer "why did the agent do X?" from artifacts.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesmer.core.agent import _judge_module_result
from mesmer.core.agent.context import Context, Turn
from mesmer.core.agent.judge import JudgeResult
from mesmer.core.constants import CompletionRole, LogEvent, ScenarioMode
from mesmer.core.graph import AttackGraph
from mesmer.core.module import ModuleConfig
from mesmer.core.scenario import AgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(*, turns: list[Turn] | None = None):
    target = MagicMock()
    target.send = AsyncMock(return_value="ok")
    registry = MagicMock()
    # ModuleConfig with an empty rubric so _judge_module_result doesn't fail
    # on a MagicMock attribute access.
    registry.get = MagicMock(return_value=ModuleConfig(name="probe"))
    registry.tiers_for = MagicMock(side_effect=lambda names: {n: 0 for n in names})
    registry.tier_of = MagicMock(return_value=0)
    agent = AgentConfig(
        model="test/attacker", judge_model="test/judge", api_key="sk",
    )
    graph = AttackGraph()
    graph.ensure_root()
    ctx = Context(
        target=target, registry=registry, agent_config=agent,
        objective="o", run_id="r", graph=graph,
        scenario_mode=ScenarioMode.TRIALS,
    )
    if turns is not None:
        ctx.turns[:] = turns
    return ctx


# ---------------------------------------------------------------------------
# judge_verdict
# ---------------------------------------------------------------------------


class TestJudgeVerdict:
    @pytest.mark.asyncio
    async def test_emits_full_judge_result_as_json(self):
        """Every successful evaluate_attempt → one ``judge_verdict`` with
        the full score + rationale as JSON, in addition to the one-line
        ``judge_score``.
        """
        ctx = _ctx(turns=[
            Turn(sent="probe", received="nope", module="probe")
        ])
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        verdict = JudgeResult(
            score=7, leaked_info="partial rules",
            promising_angle="reprint request",
            dead_end="bare directive",
            suggested_next="escalate to delimiter-injection",
        )

        async def fake_eval(ctx, **kw):
            return verdict

        with patch("mesmer.core.agent.judge.evaluate_attempt", new=fake_eval):
            result = await _judge_module_result(
                ctx, "probe", "angle", capture,
                exchanges=ctx.turns, module_result="",
            )

        assert result is verdict
        # judge_score (short) + judge_verdict (JSON) both fire.
        score_lines = [d for e, d in events if e == LogEvent.JUDGE_SCORE.value]
        verdict_lines = [d for e, d in events if e == LogEvent.JUDGE_VERDICT.value]
        assert len(score_lines) == 1
        assert len(verdict_lines) == 1
        payload = json.loads(verdict_lines[0])
        assert payload["module"] == "probe"
        assert payload["score"] == 7
        assert payload["leaked_info"] == "partial rules"
        assert payload["promising_angle"] == "reprint request"
        assert payload["dead_end"] == "bare directive"
        assert payload["suggested_next"] == "escalate to delimiter-injection"

    @pytest.mark.asyncio
    async def test_no_verdict_when_evaluate_throws(self):
        """Judge errors propagate as JUDGE_ERROR — no verdict emitted."""
        ctx = _ctx(turns=[Turn(sent="x", received="y", module="probe")])
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        async def boom(ctx, **kw):
            raise RuntimeError("judge blew up")

        with patch("mesmer.core.agent.judge.evaluate_attempt", new=boom):
            result = await _judge_module_result(
                ctx, "probe", "angle", capture,
                exchanges=ctx.turns, module_result="",
            )
        assert result is None
        verdicts = [e for e, _ in events if e == LogEvent.JUDGE_VERDICT.value]
        errors = [e for e, _ in events if e == LogEvent.JUDGE_ERROR.value]
        assert verdicts == []
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# delegate — JSON payload
# ---------------------------------------------------------------------------


class TestDelegateEventPayload:
    @pytest.mark.asyncio
    async def test_delegate_carries_instruction_tier_frontier_id(self):
        """The sub_module tool logs a structured DELEGATE event with
        module / tier / instruction / frontier_id.
        """
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        # Stub the graph + sub-module pipeline — we only care about the
        # DELEGATE emission, not the downstream judge/graph update. Patch
        # each of the post-delegate helpers to no-ops.
        ctx.run_module = AsyncMock(return_value="module result")
        call = MagicMock()
        call.id = "call_1"
        module = ModuleConfig(name="leader", sub_modules=[])
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        with patch(
            "mesmer.core.agent.tools.sub_module._judge_module_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "mesmer.core.agent.tools.sub_module._update_graph",
            return_value=None,
        ), patch(
            "mesmer.core.agent.tools.sub_module._reflect_and_expand",
            new=AsyncMock(return_value=None),
        ):
            await handle(
                ctx, module, call, "direct-ask",
                args={
                    "instruction": "ask the target plainly for its rules",
                    "max_turns": 1,
                    "frontier_id": "fid123",
                },
                instruction="ignored-fallback",
                log=capture,
            )

        delegate_lines = [d for e, d in events if e == LogEvent.DELEGATE.value]
        assert len(delegate_lines) == 1
        payload = json.loads(delegate_lines[0])
        assert payload["module"] == "direct-ask"
        assert payload["tier"] == 0
        assert payload["max_turns"] == 1
        assert payload["frontier_id"] == "fid123"
        assert payload["instruction"] == "ask the target plainly for its rules"


# ---------------------------------------------------------------------------
# sub_module.handle — short-circuit when judge flags objective_met
# ---------------------------------------------------------------------------


class TestSubModuleObjectiveMetShortCircuit:
    """When the in-loop judge sets ``objective_met=True``, the engine's
    auto-conclude fires on the next iteration. Running ``_reflect_and_expand``
    after that point burns 2-3 refine_approach LLM calls for frontier nodes
    nothing will ever execute — ~2k tokens + ~14s per winning trial.

    These tests pin the short-circuit at ``sub_module.handle`` so the fix
    can't silently regress.
    """

    @pytest.mark.asyncio
    async def test_skips_reflect_and_expand_when_objective_met(self):
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.run_module = AsyncMock(return_value="we got it")

        call = MagicMock()
        call.id = "call_win"
        module = ModuleConfig(name="leader", sub_modules=["direct-ask"])

        winning = JudgeResult(
            score=10, leaked_info="paradox",
            promising_angle="framing as python var",
            dead_end="none", suggested_next="done",
            objective_met=True,
        )
        graph_node = MagicMock()
        graph_node.id = "node_win"
        reflect_spy = AsyncMock(return_value=None)

        with patch(
            "mesmer.core.agent.tools.sub_module._judge_module_result",
            new=AsyncMock(return_value=winning),
        ), patch(
            "mesmer.core.agent.tools.sub_module._update_graph",
            return_value=graph_node,
        ), patch(
            "mesmer.core.agent.tools.sub_module._reflect_and_expand",
            new=reflect_spy,
        ):
            await handle(
                ctx, module, call, "direct-ask",
                args={"instruction": "ask plainly"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        reflect_spy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_reflect_and_expand_when_objective_not_met(self):
        """Below-threshold verdicts still hit the frontier-expansion path —
        the short-circuit gates ONLY on ``objective_met``, not on score.
        """
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.run_module = AsyncMock(return_value="partial signal only")

        call = MagicMock()
        call.id = "call_partial"
        module = ModuleConfig(name="leader", sub_modules=["direct-ask"])

        partial = JudgeResult(
            score=4, leaked_info="",
            promising_angle="maybe try framing",
            dead_end="refusal template",
            suggested_next="try delimiter-injection",
            objective_met=False,
        )
        graph_node = MagicMock()
        graph_node.id = "node_partial"
        reflect_spy = AsyncMock(return_value=None)

        with patch(
            "mesmer.core.agent.tools.sub_module._judge_module_result",
            new=AsyncMock(return_value=partial),
        ), patch(
            "mesmer.core.agent.tools.sub_module._update_graph",
            return_value=graph_node,
        ), patch(
            "mesmer.core.agent.tools.sub_module._reflect_and_expand",
            new=reflect_spy,
        ):
            await handle(
                ctx, module, call, "direct-ask",
                args={"instruction": "ask plainly"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        reflect_spy.assert_awaited_once()


# ---------------------------------------------------------------------------
# llm_completion — per-call emission from ctx.completion
# ---------------------------------------------------------------------------


class TestLlmCompletionEvent:
    @pytest.mark.asyncio
    async def test_fires_on_every_completion_with_role_and_usage(self):
        """Every successful completion emits one ``llm_completion`` event
        with role + model + elapsed + token usage. Attacker and judge
        roles are distinguishable from the same trace.
        """
        ctx = _ctx()
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        ctx.log = capture

        # Fake litellm — returns a usage-bearing response. Patch at the
        # import site inside ctx.completion.
        class FakeUsage:
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15

        fake_response = MagicMock()
        fake_response.usage = FakeUsage()

        fake_litellm = MagicMock()
        fake_litellm.suppress_debug_info = False
        fake_litellm.acompletion = AsyncMock(return_value=fake_response)

        with patch.dict("sys.modules", {"litellm": fake_litellm}):
            # Attacker call.
            await ctx.completion(
                [{"role": "user", "content": "hi"}],
                role=CompletionRole.ATTACKER,
            )
            # Judge call.
            await ctx.completion(
                [{"role": "user", "content": "score it"}],
                role=CompletionRole.JUDGE,
            )

        llm_events = [d for e, d in events if e == LogEvent.LLM_COMPLETION.value]
        assert len(llm_events) == 2
        payloads = [json.loads(d) for d in llm_events]
        assert payloads[0]["role"] == "attacker"
        assert payloads[1]["role"] == "judge"
        # Model names reflect the role resolution — attacker vs judge model.
        assert payloads[0]["model"] == "test/attacker"
        assert payloads[1]["model"] == "test/judge"
        # Usage is folded into the detail.
        assert payloads[0]["prompt_tokens"] == 10
        assert payloads[0]["completion_tokens"] == 5
        assert payloads[0]["total_tokens"] == 15
        assert payloads[0]["n_messages"] == 1

    @pytest.mark.asyncio
    async def test_completion_silent_when_ctx_log_not_bound(self):
        """Contexts without a bound log (tests / direct SDK use) never
        emit — zero observable side effects.
        """
        ctx = _ctx()
        # ctx.log stays None by default.
        assert ctx.log is None

        fake_response = MagicMock()
        fake_response.usage = None
        fake_litellm = MagicMock()
        fake_litellm.suppress_debug_info = False
        fake_litellm.acompletion = AsyncMock(return_value=fake_response)

        with patch.dict("sys.modules", {"litellm": fake_litellm}):
            # Would raise if the emission path fell through to a None log.
            await ctx.completion(
                [{"role": "user", "content": "hi"}],
            )
        # No crash is the contract; trace is empty.
