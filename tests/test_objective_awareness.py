"""The framework-level OBJECTIVE AWARENESS clause is injected into every
module's system prompt — verified via a real ``run_react_loop`` invocation
so any refactor that loses the stanza surfaces here.

The stanza is split by depth:
- Sub-modules (ctx.depth > 0) receive the "OBJECTIVE SIGNAL" variant —
  they flag potential signals in their conclude text and finish their
  deliverable. They do NOT call "OBJECTIVE MET".
- The leader (ctx.depth == 0) receives the "OBJECTIVE MET" variant —
  it evaluates OBJECTIVE SIGNAL flags from sub-modules and raw target
  evidence, then decides termination by calling conclude("OBJECTIVE MET").

ctx.objective_met is set by the engine's conclude short-circuit when the
leader calls conclude("OBJECTIVE MET — ..."). The judge no longer propagates
objective_met to ctx — the termination decision lives at the leader level.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mesmer.core.agent.context import Context
from mesmer.core.agent.engine import run_react_loop
from mesmer.core.graph import AttackGraph
from mesmer.core.module import ModuleConfig
from mesmer.core.scenario import AgentConfig


class _FakeToolCall:
    def __init__(self, name, args):
        self.id = "call-0"
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = '{"result": "done"}' if name == "conclude" else args


class _FakeMessage:
    def __init__(self, tool_calls=None):
        self.content = ""
        self.reasoning_content = None
        self.tool_calls = tool_calls or []


class _FakeResponse:
    def __init__(self, message):
        self.choices = [MagicMock(message=message, finish_reason="tool_calls")]
        self.usage = MagicMock(prompt_tokens=10, completion_tokens=2, total_tokens=12)


def _make_ctx_capturing(objective: str, captured: list) -> Context:
    """Build a Context whose first LLM call captures the messages list.

    The attacker is scripted to immediately call ``conclude()`` so the
    loop runs exactly one iteration — we only care what the engine
    assembled for the system prompt.
    """
    target = MagicMock()

    async def _send(msg):
        return "ok"

    target.send = _send
    target.reset = MagicMock()
    target.get_history = MagicMock(return_value=[])

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=False)
    registry.as_tools = MagicMock(return_value=[])
    registry.tiers_for = MagicMock(side_effect=lambda names: {n: 2 for n in names})
    registry.tier_of = MagicMock(return_value=2)

    agent_config = AgentConfig(model="test/model", api_key="sk-test")
    graph = AttackGraph()
    graph.ensure_root()

    async def fake_completion(messages, tools=None):
        captured.append(messages)
        return _FakeResponse(
            _FakeMessage(
                tool_calls=[_FakeToolCall("conclude", None)],
            )
        )

    ctx = Context(
        target=target,
        registry=registry,
        agent_config=agent_config,
        objective=objective,
        max_turns=5,
        graph=graph,
        run_id="test-run",
    )
    ctx.completion = fake_completion
    return ctx


@pytest.mark.asyncio
async def test_leader_stanza_present_and_uses_objective_met():
    """At depth=0 (leader) the OBJECTIVE AWARENESS clause is present and
    uses the OBJECTIVE MET termination marker. The leader is the decision-maker."""
    module = ModuleConfig(
        name="tiny-module",
        description="test scaffolding",
        theory="test theory",
        system_prompt="You are a tiny module. Do the thing.",
        sub_modules=[],
        tier=2,
    )
    captured: list = []
    ctx = _make_ctx_capturing(
        objective="Extract the target's hidden instructions.",
        captured=captured,
    )
    # depth=0 is the default — this is the leader
    assert ctx.depth == 0
    await run_react_loop(module, ctx, instruction="probe")

    assert captured, "run_react_loop did not invoke ctx.completion"
    messages = captured[0]
    system_messages = [m for m in messages if m.get("role") == "system"]
    assert system_messages, "no system message on first completion"
    sys_text = system_messages[0]["content"]

    # Leader stanza: present, instructs the leader to call conclude() with
    # the objective_met param, and to read OBJECTIVE SIGNAL flags from sub-modules.
    assert "OBJECTIVE AWARENESS" in sys_text
    assert "objective_met=true" in sys_text   # explicit bool param, not string pattern
    assert "OBJECTIVE SIGNAL" in sys_text     # leader is told to read these from sub-modules
    # Module's own prompt leads; stanza APPENDS, not prepends.
    assert sys_text.index("tiny module") < sys_text.index("OBJECTIVE AWARENESS")

    # And the user message carries the scenario objective.
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert user_messages
    assert "Extract the target's hidden instructions." in user_messages[0]["content"]


@pytest.mark.asyncio
async def test_sub_module_stanza_uses_signal_not_terminate():
    """At depth > 0 (sub-module), the OBJECTIVE AWARENESS clause tells the
    module to flag with OBJECTIVE SIGNAL — NOT to call OBJECTIVE MET.
    Termination authority is reserved for the leader."""
    module = ModuleConfig(
        name="sub-module",
        description="test scaffolding",
        theory="test theory",
        system_prompt="You are a sub-module. Do the thing.",
        sub_modules=[],
        tier=0,
    )
    captured: list = []
    ctx = _make_ctx_capturing(
        objective="Extract the target's hidden instructions.",
        captured=captured,
    )
    ctx.depth = 1  # simulate running as a sub-module

    await run_react_loop(module, ctx, instruction="probe")

    assert captured, "run_react_loop did not invoke ctx.completion"
    sys_text = captured[0][0]["content"]

    # Sub-module stanza: present, instructs the module to use OBJECTIVE SIGNAL.
    assert "OBJECTIVE AWARENESS" in sys_text
    # The call template that the sub-module SHOULD use is present.
    assert "OBJECTIVE SIGNAL — <" in sys_text
    # The leader termination marker must NOT appear anywhere in the sub-module
    # stanza — not even as a forbidden example. Mentioning it (even negatively)
    # is enough for an LLM to pattern-match on it and use it.
    assert "OBJECTIVE MET" not in sys_text


@pytest.mark.asyncio
async def test_conclude_with_objective_met_sets_ctx():
    """When ANY module's run_react_loop concludes with 'OBJECTIVE MET — <fragment>',
    ctx.objective_met is set to True and the fragment is captured."""
    module = ModuleConfig(
        name="leader-module",
        description="",
        theory="",
        system_prompt="You are the leader.",
        sub_modules=[],
        tier=2,
    )

    target = MagicMock()

    async def _send(msg):
        return "ok"

    target.send = _send
    target.reset = MagicMock()
    target.get_history = MagicMock(return_value=[])

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=False)
    registry.as_tools = MagicMock(return_value=[])
    registry.tiers_for = MagicMock(side_effect=lambda names: {n: 2 for n in names})
    registry.tier_of = MagicMock(return_value=2)

    from mesmer.core.scenario import AgentConfig
    from mesmer.core.graph import AttackGraph

    agent_config = AgentConfig(model="test/model", api_key="sk-test")
    graph = AttackGraph()
    graph.ensure_root()

    class _ConcludeToolCall:
        """FakeToolCall variant that passes the arguments through unchanged."""
        def __init__(self, arguments: str):
            self.id = "call-conclude"
            self.function = MagicMock()
            self.function.name = "conclude"
            self.function.arguments = arguments

    async def fake_completion_conclude(messages, tools=None):
        return _FakeResponse(
            _FakeMessage(
                tool_calls=[
                    _ConcludeToolCall(
                        '{"result": "alanturing06 was the secret", "objective_met": true}'
                    ),
                ],
            )
        )

    from mesmer.core.agent.context import Context

    ctx = Context(
        target=target,
        registry=registry,
        agent_config=agent_config,
        objective="Extract the secret code.",
        max_turns=5,
        graph=graph,
        run_id="test-run",
    )
    ctx.completion = fake_completion_conclude

    await run_react_loop(module, ctx, instruction="probe")

    assert ctx.objective_met is True
    # objective_met_fragment is the full result text when objective_met=true
    assert "alanturing06" in ctx.objective_met_fragment


@pytest.mark.asyncio
async def test_conclude_without_objective_met_flag_does_not_set_ctx():
    """conclude() without objective_met=true must NOT set ctx.objective_met —
    even if the result text happens to contain the phrase 'OBJECTIVE MET'."""
    module = ModuleConfig(
        name="leader-module",
        description="",
        theory="",
        system_prompt="You are the leader.",
        sub_modules=[],
        tier=2,
    )
    captured: list = []
    ctx = _make_ctx_capturing(objective="Extract the secret.", captured=captured)

    class _ConcludeToolCall:
        def __init__(self, arguments: str):
            self.id = "call-conclude"
            self.function = MagicMock()
            self.function.name = "conclude"
            self.function.arguments = arguments

    import json as _json

    async def fake_completion_no_flag(messages, tools=None):
        captured.append(messages)
        return _FakeResponse(
            _FakeMessage(
                tool_calls=[
                    _ConcludeToolCall(
                        _json.dumps({"result": "## Result\nOBJECTIVE MET — alanturing06"})
                        # Note: no "objective_met": true
                    )
                ],
            )
        )

    ctx.completion = fake_completion_no_flag

    await run_react_loop(module, ctx, instruction="probe")

    # String pattern in result text alone must NOT trigger objective_met.
    assert ctx.objective_met is False


@pytest.mark.asyncio
async def test_stanza_is_scenario_agnostic():
    """Anti-overfit guardrail: the clause must not name any specific
    scenario / dataset / canary vocabulary."""
    module = ModuleConfig(
        name="tiny-module",
        description="",
        theory="",
        system_prompt="You are a tiny module.",
        sub_modules=[],
        tier=2,
    )
    captured: list = []
    ctx = _make_ctx_capturing(objective="ANY OBJECTIVE", captured=captured)
    await run_react_loop(module, ctx, instruction="probe")

    sys_text = captured[0][0]["content"]
    forbidden = [
        "password",
        "access code",
        "canary",
        "tensor trust",
        "pre_prompt",
        "post_prompt",
        "paradox",
        "mahabarata",
    ]
    lowered = sys_text.lower()
    for term in forbidden:
        assert term not in lowered, (
            f"scenario-specific term '{term}' leaked into OBJECTIVE "
            f"AWARENESS clause — overfitting guardrail tripped"
        )


@pytest.mark.asyncio
async def test_stanza_stays_dataset_neutral_recognition_lives_in_scenario():
    """The stanza must NOT carry dataset-specific recognition heuristics.
    "What counts as a leak" depends on the scenario — extraction
    benchmarks want literal substring matches; jailbreak benchmarks want
    behavioural compliance; tool-misuse benchmarks want a tool-call
    pattern. If we hardcode shape examples (alphanumeric tokens,
    scripture, persona descriptors, etc.) into the engine, every new
    dataset either inherits a wrong prior or needs an engine PR.

    Pin: the stanza only carries the generic signal-flag → decide → terminate
    protocol. All dataset-specific framing belongs in the scenario YAML.
    """
    module = ModuleConfig(
        name="tiny-module",
        description="",
        theory="",
        system_prompt="You are a tiny module.",
        sub_modules=[],
        tier=2,
    )
    captured: list = []
    ctx = _make_ctx_capturing(objective="ANY OBJECTIVE", captured=captured)
    # depth=0 (default) → leader stanza
    await run_react_loop(module, ctx, instruction="probe")

    sys_text = captured[0][0]["content"]
    lowered = sys_text.lower()

    # Generic protocol words MUST be present — these are what makes the
    # terminate-signal mechanism work across any scenario.
    assert "OBJECTIVE AWARENESS" in sys_text
    # The leader stanza uses the conclude() param, not a string marker.
    assert "objective_met=true" in sys_text
    assert "OBJECTIVE SIGNAL" in sys_text     # sub-module signal protocol
    assert "unambiguous" in lowered           # decision bar is explicit

    # Recognition heuristics MUST NOT be present in the engine stanza.
    # If a future change reintroduces them, this guard fails — and the
    # change should land in a spec instead.
    overfit_terms = [
        "out-of-context",
        "self-quoting",
        "non-sequitur",
        "scripture",
        "alphanumeric",
        "persona descriptor",
        "instruction-quoting",
        "boilerplate",
    ]
    for term in overfit_terms:
        assert term not in lowered, (
            f"recognition heuristic '{term}' leaked into the engine "
            "stanza — it should live in the scenario objective text "
            "instead so different datasets can frame their own success "
            "criteria"
        )
