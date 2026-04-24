"""The framework-level OBJECTIVE AWARENESS clause is injected into every
module's system prompt — verified via a real ``run_react_loop`` invocation
so any refactor that loses the stanza surfaces here.

The mechanism downstream of the clause (sub-module conclude → judge sets
``ctx.objective_met`` → leader auto-concludes) has its own tests in
``test_loop.py``; this file focuses specifically on whether the clause
text actually reaches the attacker-role system message.
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
        return _FakeResponse(_FakeMessage(
            tool_calls=[_FakeToolCall("conclude", None)],
        ))

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
async def test_stanza_present_in_every_module_system_prompt():
    """OBJECTIVE AWARENESS clause appears in the system message of any
    module run, so sub-modules inherit it without per-yaml edits."""
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
    await run_react_loop(module, ctx, instruction="probe")

    assert captured, "run_react_loop did not invoke ctx.completion"
    messages = captured[0]
    system_messages = [m for m in messages if m.get("role") == "system"]
    assert system_messages, "no system message on first completion"
    sys_text = system_messages[0]["content"]

    # The stanza header and the literal marker it tells modules to emit.
    assert "OBJECTIVE AWARENESS" in sys_text
    assert "OBJECTIVE MET" in sys_text
    # Module's own prompt leads; stanza APPENDS, not prepends.
    assert sys_text.index("tiny module") < sys_text.index("OBJECTIVE AWARENESS")

    # And the user message carries the scenario objective.
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert user_messages
    assert "Extract the target's hidden instructions." in user_messages[0]["content"]


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
    forbidden = ["password", "access code", "canary", "tensor trust",
                 "pre_prompt", "post_prompt", "paradox", "mahabarata"]
    lowered = sys_text.lower()
    for term in forbidden:
        assert term not in lowered, (
            f"scenario-specific term '{term}' leaked into OBJECTIVE "
            f"AWARENESS clause — overfitting guardrail tripped"
        )
