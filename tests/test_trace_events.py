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
from mesmer.core.belief_graph import (
    BeliefGraph,
    FrontierCreateDelta,
    HypothesisCreateDelta,
    make_frontier,
    make_hypothesis,
)
from mesmer.core.constants import CompletionRole, LogEvent, ScenarioMode
from mesmer.core.graph import AttackGraph
from mesmer.core.module import ModuleConfig, SubModuleEntry
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
        model="test/attacker",
        judge_model="test/judge",
        api_key="sk",
    )
    graph = AttackGraph()
    graph.ensure_root()
    ctx = Context(
        target=target,
        registry=registry,
        agent_config=agent,
        objective="o",
        run_id="r",
        graph=graph,
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
        ctx = _ctx(turns=[Turn(sent="probe", received="nope", module="probe")])
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        verdict = JudgeResult(
            score=7,
            leaked_info="partial rules",
            promising_angle="reprint request",
            dead_end="bare directive",
            suggested_next="escalate to delimiter-injection",
        )

        async def fake_eval(ctx, **kw):
            return verdict

        with patch("mesmer.core.agent.judge.evaluate_attempt", new=fake_eval):
            result = await _judge_module_result(
                ctx,
                "probe",
                "angle",
                capture,
                exchanges=ctx.turns,
                module_result="",
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
                ctx,
                "probe",
                "angle",
                capture,
                exchanges=ctx.turns,
                module_result="",
            )
        assert result is None
        verdicts = [e for e, _ in events if e == LogEvent.JUDGE_VERDICT.value]
        errors = [e for e, _ in events if e == LogEvent.JUDGE_ERROR.value]
        assert verdicts == []
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_artifact_only_module_with_rubric_is_judged(self):
        """Planner/catalog modules are scored on their authored artifact."""
        ctx = _ctx(turns=[])
        ctx.registry.get = MagicMock(
            return_value=ModuleConfig(
                name="attack-planner",
                judge_rubric="Score this module on plan quality.",
            )
        )
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        verdict = JudgeResult(
            score=8,
            leaked_info="strong plan",
            promising_angle="grounded escalation",
            dead_end="none",
            suggested_next="execute step 1",
        )

        async def fake_eval(ctx, **kw):
            assert kw["exchanges"] == []
            assert "## Strategy" in kw["module_result"]
            return verdict

        with patch("mesmer.core.agent.judge.evaluate_attempt", new=fake_eval):
            result = await _judge_module_result(
                ctx,
                "attack-planner",
                "plan",
                capture,
                exchanges=[],
                module_result="## Strategy\nUse the known winning vector.",
            )

        assert result is verdict
        assert any("artifact-only" in detail for event, detail in events if event == LogEvent.JUDGE.value)


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

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=None,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=AsyncMock(return_value=None),
            ),
        ):
            await handle(
                ctx,
                module,
                call,
                "direct-ask",
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

    @pytest.mark.asyncio
    async def test_child_does_not_inherit_parent_experiment_for_different_module(self):
        """A manager may be executing a parent experiment, but its child
        modules should not inherit that experiment unless the experiment
        targets the child module itself.
        """
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        bg = BeliefGraph(target_hash="t")
        h = make_hypothesis(
            claim="system prompt can be extracted",
            description="parent-manager hypothesis",
            family="system-prompt-extraction",
            confidence=0.5,
        )
        bg.apply(HypothesisCreateDelta(hypothesis=h))
        parent_fx = make_frontier(
            hypothesis_id=h.id,
            module="system-prompt-extraction",
            instruction="run the extraction manager",
            expected_signal="prompt fragment",
        )
        bg.apply(FrontierCreateDelta(experiment=parent_fx))
        ctx.belief_graph = bg
        ctx.active_experiment_id = parent_fx.id

        captured: dict[str, str | None] = {}

        async def fake_run_module(fn_name, instruction, max_turns, log, active_experiment_id=None):
            captured["active_experiment_id"] = active_experiment_id
            return "child result"

        ctx.run_module = fake_run_module
        call = MagicMock()
        call.id = "call_child"
        module = ModuleConfig(
            name="system-prompt-extraction",
            sub_modules=[SubModuleEntry(name="direct-ask")],
        )
        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=None),
            ),
            patch("mesmer.core.agent.tools.sub_module._update_graph", return_value=None),
            patch(
                "mesmer.core.agent.tools.sub_module._update_belief_graph",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=AsyncMock(return_value=None),
            ),
        ):
            await handle(
                ctx,
                module,
                call,
                "direct-ask",
                args={"instruction": "ask directly"},
                instruction="fallback",
                log=capture,
            )

        assert captured["active_experiment_id"] is None
        delegate_lines = [d for e, d in events if e == LogEvent.DELEGATE.value]
        payload = json.loads(delegate_lines[0])
        assert payload["experiment_id"] is None

    @pytest.mark.asyncio
    async def test_child_keeps_matching_experiment_id(self):
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        bg = BeliefGraph(target_hash="t")
        h = make_hypothesis(
            claim="direct ask may work",
            description="child-module hypothesis",
            family="direct-ask",
            confidence=0.5,
        )
        bg.apply(HypothesisCreateDelta(hypothesis=h))
        child_fx = make_frontier(
            hypothesis_id=h.id,
            module="direct-ask",
            instruction="ask directly",
            expected_signal="prompt fragment",
        )
        bg.apply(FrontierCreateDelta(experiment=child_fx))
        ctx.belief_graph = bg
        ctx.active_experiment_id = child_fx.id

        captured: dict[str, str | None] = {}

        async def fake_run_module(fn_name, instruction, max_turns, log, active_experiment_id=None):
            captured["active_experiment_id"] = active_experiment_id
            return "child result"

        ctx.run_module = fake_run_module
        call = MagicMock()
        call.id = "call_child"
        module = ModuleConfig(name="leader", sub_modules=[SubModuleEntry(name="direct-ask")])

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=None),
            ),
            patch("mesmer.core.agent.tools.sub_module._update_graph", return_value=None),
            patch(
                "mesmer.core.agent.tools.sub_module._update_belief_graph",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=AsyncMock(return_value=None),
            ),
        ):
            await handle(
                ctx,
                module,
                call,
                "direct-ask",
                args={"instruction": "ask directly"},
                instruction="fallback",
                log=lambda *_: None,
            )

        assert captured["active_experiment_id"] == child_fx.id

    @pytest.mark.asyncio
    async def test_fixed_order_executive_blocks_later_phase_until_prior_writes_scratchpad(self):
        """Authored multi-manager scenarios are prompt-guided, but the runtime
        also enforces the declared phase order before spending a delegation.
        """
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.run_module = AsyncMock(return_value="should not run")
        call = MagicMock()
        call.id = "call_1"
        module = ModuleConfig(
            name="scenario:executive",
            sub_modules=[
                SubModuleEntry(name="system-prompt-extraction"),
                SubModuleEntry(name="exploit-analysis"),
                SubModuleEntry(name="exploit-executor"),
            ],
            parameters={
                "ordered_modules": [
                    "system-prompt-extraction",
                    "exploit-analysis",
                    "exploit-executor",
                ]
            },
            is_executive=True,
        )

        result = await handle(
            ctx,
            module,
            call,
            "exploit-executor",
            args={"instruction": "execute"},
            instruction="ignored",
            log=lambda *_: None,
        )

        ctx.run_module.assert_not_awaited()
        assert "Fixed scenario phase order blocked" in result["content"]
        assert "`system-prompt-extraction`" in result["content"]

    @pytest.mark.asyncio
    async def test_fixed_order_executive_warns_but_runs_when_analysis_catalog_is_thin(self):
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.scratchpad.set("system-prompt-extraction", "recon done")
        ctx.scratchpad.set("exploit-analysis", "Exploit analysis complete.")
        ctx.run_module = AsyncMock(return_value="executor saw no usable findings")
        call = MagicMock()
        call.id = "call_1"
        module = ModuleConfig(
            name="scenario:executive",
            sub_modules=[
                SubModuleEntry(name="system-prompt-extraction"),
                SubModuleEntry(name="exploit-analysis"),
                SubModuleEntry(name="exploit-executor"),
            ],
            parameters={
                "ordered_modules": [
                    "system-prompt-extraction",
                    "exploit-analysis",
                    "exploit-executor",
                ],
                "ordered_artifact_requirements": {
                    "exploit-analysis": ["## Findings"],
                },
            },
            is_executive=True,
        )

        result = await handle(
            ctx,
            module,
            call,
            "exploit-executor",
            args={"instruction": "execute"},
            instruction="ignored",
            log=lambda *_: None,
        )

        ctx.run_module.assert_awaited_once()
        delegated_instruction = ctx.run_module.await_args.args[1]
        assert "Framework Handoff Warning" in delegated_instruction
        assert "`## Findings`" in delegated_instruction
        assert "executor saw no usable findings" in result["content"]


# ---------------------------------------------------------------------------
# sub_module.handle — short-circuit when judge flags objective_met
# ---------------------------------------------------------------------------


class TestSubModuleReflectAndExpand:
    """_reflect_and_expand is always called after sub-module delegation —
    regardless of whether the judge flagged objective_met. Termination
    authority lives at the LEADER level: the judge's objective_met is an
    advisory signal in the tool_result, not a run-stopper. If the leader
    decides the objective is met it calls conclude("OBJECTIVE MET — ..."),
    but unused frontier nodes from a correct signal are harmless, and a
    wrong judge signal (e.g. "Access Granted" ≠ the secret code) should not
    strand the attack by skipping frontier expansion.
    """

    @pytest.mark.asyncio
    async def test_runs_reflect_and_expand_even_when_judge_flags_objective_met(self):
        """Frontier expansion is NOT skipped when judge.objective_met=True.
        The leader reads the OBJECTIVE SIGNAL in the tool_result and decides.
        """
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.run_module = AsyncMock(return_value="we got it")

        call = MagicMock()
        call.id = "call_win"
        module = ModuleConfig(name="leader", sub_modules=["direct-ask"])

        winning = JudgeResult(
            score=10,
            leaked_info="paradox",
            promising_angle="framing as python var",
            dead_end="none",
            suggested_next="done",
            objective_met=True,
        )
        graph_node = MagicMock()
        graph_node.id = "node_win"
        reflect_spy = AsyncMock(return_value=None)

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=winning),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=graph_node,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=reflect_spy,
            ),
        ):
            await handle(
                ctx,
                module,
                call,
                "direct-ask",
                args={"instruction": "ask plainly"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        # Frontier expansion must still run — leader decides termination.
        reflect_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runs_reflect_and_expand_when_objective_not_met(self):
        """Below-threshold verdicts still hit the frontier-expansion path."""
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.run_module = AsyncMock(return_value="partial signal only")

        call = MagicMock()
        call.id = "call_partial"
        module = ModuleConfig(name="leader", sub_modules=["direct-ask"])

        partial = JudgeResult(
            score=4,
            leaked_info="",
            promising_angle="maybe try framing",
            dead_end="refusal template",
            suggested_next="try delimiter-injection",
            objective_met=False,
        )
        graph_node = MagicMock()
        graph_node.id = "node_partial"
        reflect_spy = AsyncMock(return_value=None)

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=partial),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=graph_node,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=reflect_spy,
            ),
        ):
            await handle(
                ctx,
                module,
                call,
                "direct-ask",
                args={"instruction": "ask plainly"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        reflect_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ordered_executive_skips_generic_frontier_expansion(self):
        """Fixed manager pipelines should not propose current/prior phases."""
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        ctx.run_module = AsyncMock(return_value="phase output")

        call = MagicMock()
        call.id = "call_ordered"
        module = ModuleConfig(
            name="scenario:executive",
            sub_modules=[
                SubModuleEntry(name="system-prompt-extraction"),
                SubModuleEntry(name="exploit-analysis"),
                SubModuleEntry(name="exploit-executor"),
            ],
            parameters={
                "ordered_modules": [
                    "system-prompt-extraction",
                    "exploit-analysis",
                    "exploit-executor",
                ],
            },
            is_executive=True,
        )

        verdict = JudgeResult(
            score=8,
            leaked_info="phase output",
            promising_angle="phase worked",
            dead_end="none",
            suggested_next="next phase",
            objective_met=False,
        )
        graph_node = MagicMock()
        graph_node.id = "node_ordered"
        reflect_spy = AsyncMock(return_value=None)

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=verdict),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=graph_node,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=reflect_spy,
            ),
        ):
            await handle(
                ctx,
                module,
                call,
                "system-prompt-extraction",
                args={"instruction": "run phase 1"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        reflect_spy.assert_not_awaited()


# ---------------------------------------------------------------------------
# sub_module.handle — verbatim target evidence in tool_result
# ---------------------------------------------------------------------------


class TestTargetEvidenceInToolResult:
    """The sub_module tool injects a verbatim target-evidence block into the
    tool_result text the leader sees on its next iteration. Previously the
    leader saw only (a) the sub-module's prose self-summary and (b) the
    judge's prose digest — two layers of LLM judgment between the leader
    and the actual target reply. A verbatim leak (e.g. an alphanumeric
    secret) could therefore be rationalised away by both upstream actors
    and the leader had no path to disagree.

    The block is the deterministic last line of defense — leader applies
    the OBJECTIVE AWARENESS recognition heuristics directly to the raw
    target text quoted into its context.
    """

    @pytest.mark.asyncio
    async def test_tool_result_quotes_recent_target_replies_verbatim(self):
        from mesmer.core.agent.tools.sub_module import handle
        from mesmer.core.constants import TurnKind

        ctx = _ctx()

        async def fake_run_module(fn_name, instruction, max_turns, log):
            ctx.turns.append(
                Turn(
                    sent="probe one",
                    received="bible verse here",
                    module=fn_name,
                    kind=TurnKind.EXCHANGE,
                )
            )
            ctx.turns.append(
                Turn(
                    sent="delimiter trick",
                    received="kinetics%$ leaked!",
                    module=fn_name,
                    kind=TurnKind.EXCHANGE,
                )
            )
            return "module concludes that nothing useful was extracted"

        ctx.run_module = fake_run_module

        call = MagicMock()
        call.id = "call_evidence"
        module = ModuleConfig(name="leader", sub_modules=["delimiter-injection"])

        verdict = JudgeResult(
            score=7,
            leaked_info="weird fragment",
            promising_angle="might be something",
            dead_end="target said it can't help",
            suggested_next="try another angle",
            objective_met=False,  # judge missed the leak
        )
        graph_node = MagicMock()
        graph_node.id = "node_x"

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=verdict),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=graph_node,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=AsyncMock(return_value=None),
            ),
        ):
            tr = await handle(
                ctx,
                module,
                call,
                "delimiter-injection",
                args={"instruction": "try delimiter trick"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        text = tr["content"]
        # Header — leader knows this is the verbatim block.
        assert "Latest target evidence" in text
        # Both target replies appear verbatim. The verbatim leak is the
        # whole point — without it, the rationalised summary above wins.
        assert "bible verse here" in text
        assert "kinetics%$ leaked!" in text
        # Recognition cue — points the leader at the OBJECTIVE AWARENESS
        # rule it should apply to the raw text.
        assert "OBJECTIVE AWARENESS" in text
        # Sub-module's self-summary AND judge digest still in the same
        # tool_result — evidence augments, doesn't replace.
        assert "module concludes that nothing useful was extracted" in text
        assert "Judge score: 7/10" in text

    @pytest.mark.asyncio
    async def test_tool_result_omits_evidence_block_when_no_real_exchanges(self):
        """Modules that error before producing a target reply (or that run
        zero exchanges, e.g. pure reasoning modules) must not emit an empty
        evidence block — keep the tool_result clean."""
        from mesmer.core.agent.tools.sub_module import handle

        ctx = _ctx()
        # No turns appended during the delegate.
        ctx.run_module = AsyncMock(return_value="reasoning-only output")

        call = MagicMock()
        call.id = "call_no_exchange"
        module = ModuleConfig(name="leader", sub_modules=["attack-planner"])

        verdict = JudgeResult(
            score=5,
            leaked_info="",
            promising_angle="",
            dead_end="",
            suggested_next="",
            objective_met=False,
        )
        graph_node = MagicMock()
        graph_node.id = "node_planner"

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=verdict),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=graph_node,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=AsyncMock(return_value=None),
            ),
        ):
            tr = await handle(
                ctx,
                module,
                call,
                "attack-planner",
                args={"instruction": "plan three angles"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        text = tr["content"]
        assert "Latest target evidence" not in text

    @pytest.mark.asyncio
    async def test_tool_result_skips_error_turns_in_evidence(self):
        """Pipeline errors (timeouts, gateway 5xx) on a Turn aren't real
        target replies — they're noise. The evidence block must filter
        them out so the leader doesn't see "TARGET → [error]" as a leak
        candidate.
        """
        from mesmer.core.agent.tools.sub_module import handle
        from mesmer.core.constants import TurnKind

        ctx = _ctx()

        async def fake_run_module(fn_name, instruction, max_turns, log):
            ctx.turns.append(
                Turn(
                    sent="probe",
                    received="[gateway timeout]",
                    module=fn_name,
                    kind=TurnKind.EXCHANGE,
                    is_error=True,
                )
            )
            ctx.turns.append(
                Turn(
                    sent="retry",
                    received="real reply with kinetics%$",
                    module=fn_name,
                    kind=TurnKind.EXCHANGE,
                )
            )
            return "module summary"

        ctx.run_module = fake_run_module

        call = MagicMock()
        call.id = "call_err"
        module = ModuleConfig(name="leader", sub_modules=["delimiter-injection"])

        verdict = JudgeResult(
            score=4,
            leaked_info="",
            promising_angle="",
            dead_end="",
            suggested_next="",
            objective_met=False,
        )
        graph_node = MagicMock()
        graph_node.id = "node_err"

        with (
            patch(
                "mesmer.core.agent.tools.sub_module._judge_module_result",
                new=AsyncMock(return_value=verdict),
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._update_graph",
                return_value=graph_node,
            ),
            patch(
                "mesmer.core.agent.tools.sub_module._reflect_and_expand",
                new=AsyncMock(return_value=None),
            ),
        ):
            tr = await handle(
                ctx,
                module,
                call,
                "delimiter-injection",
                args={"instruction": "x"},
                instruction="fallback",
                log=lambda *a, **kw: None,
            )

        text = tr["content"]
        assert "real reply with kinetics%$" in text
        assert "[gateway timeout]" not in text


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
