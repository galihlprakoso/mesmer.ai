"""Tests for mesmer.core.agent — the ReAct loop with judge/reflect/graph cycle."""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from mesmer.core.agent.context import Context
from mesmer.core.actor import ActorRole, ReactActorSpec, ToolPolicySpec
from mesmer.core.graph import AttackGraph
from mesmer.core.agent.judge import JudgeResult
from mesmer.core.agent import (
    run_react_loop,
    _build_graph_context,
    _update_graph,
)
from mesmer.core.agent.prompt import _build_learned_experience_context
from mesmer.core.scenario import AgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeToolCall:
    """Mimics OpenAI tool call object."""
    def __init__(self, name, arguments, call_id="call_1"):
        self.id = call_id
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = json.dumps(arguments)


class FakeMessage:
    """Mimics OpenAI message object."""
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResponse:
    def __init__(self, message):
        self.choices = [FakeChoice(message)]


def _make_module(name="test-module", sub_modules=None):
    return ReactActorSpec(
        name=name,
        role=ActorRole.MODULE,
        sub_modules=sub_modules or [],
        system_prompt=f"You are the {name} module.",
        description="test module",
        theory="test theory",
        tool_policy=ToolPolicySpec(
            dispatch_submodules=bool(sub_modules),
            builtin=["send_message", "conclude"],
        ),
    )


def _make_ctx(
    max_turns=20,
    completion_responses=None,
    target_replies=None,
    graph=None,
    captured_messages=None,
    operator_messages=None,
    leader_scratchpad=None,
    leader_module_name=None,
):
    """Create a Context with scripted LLM + target responses.

    If `captured_messages` is a list, every LLM messages payload is appended
    to it. Useful for asserting on injected context.
    """
    target = MagicMock()
    _target_replies = list(target_replies or ["target says hi"])
    _target_idx = [0]

    async def fake_send(msg):
        reply = _target_replies[min(_target_idx[0], len(_target_replies) - 1)]
        _target_idx[0] += 1
        return reply

    target.send = fake_send

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=False)
    registry.as_tools = MagicMock(return_value=[])
    # Return a real dict so `_build_graph_context`'s `min()` / `max()` over
    # tier values doesn't compare MagicMock instances. Default tier 2 for
    # any module the tests reference.
    registry.tiers_for = MagicMock(side_effect=lambda names: {n: 2 for n in names})
    registry.tier_of = MagicMock(return_value=2)

    agent_config = AgentConfig(model="test/model", api_key="sk-test")

    _responses = list(completion_responses or [])
    _resp_idx = [0]

    async def fake_completion(messages, tools=None):
        if captured_messages is not None:
            captured_messages.append(messages)
        if _resp_idx[0] >= len(_responses):
            # Auto-conclude if we run out of scripted responses
            return FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "auto-concluded"})]
            ))
        resp = _responses[_resp_idx[0]]
        _resp_idx[0] += 1
        return resp

    if graph is None:
        graph = AttackGraph()
        graph.ensure_root()

    ctx = Context(
        target=target,
        registry=registry,
        agent_config=agent_config,
        objective="Extract system prompt",
        max_turns=max_turns,
        graph=graph,
        run_id="test-run",
        operator_messages=list(operator_messages) if operator_messages else None,
    )
    ctx.completion = fake_completion
    if leader_scratchpad is not None and leader_module_name:
        ctx.scratchpad.update(leader_scratchpad)
    return ctx


# ---------------------------------------------------------------------------
# _build_graph_context
# ---------------------------------------------------------------------------

class TestBuildGraphContext:
    def test_empty_graph(self):
        ctx = _make_ctx()
        result = _build_graph_context(ctx)
        # Should still have budget info
        assert "Budget" in result or "explore" in result.lower()

    def test_populated_graph(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "authority-bias", "Stanford", score=1, reflection="detected")
        g.add_node(root.id, "foot-in-door", "philosophy", score=7, leaked_info="design")
        g.add_human_hint("try calendar API")

        ctx = _make_ctx(graph=g)
        result = _build_graph_context(ctx)
        assert "Attack graph" in result
        assert "foot-in-door" in result
        assert "authority-bias" in result

    def test_conclude_mode(self):
        ctx = _make_ctx(max_turns=10)
        ctx.turns_used = 9  # 90% → conclude
        result = _build_graph_context(ctx)
        assert "CONCLUDE" in result.upper()


class TestLearnedExperienceContext:
    def test_executive_gets_only_dispatchable_manager_outcomes(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        graph.add_node(root.id, "system-prompt-extraction", "manager", score=8)
        graph.add_node(root.id, "target-profiler", "child", score=2)
        ctx = _make_ctx(graph=graph)
        executive = ReactActorSpec(
            name="scenario:executive",
            role=ActorRole.EXECUTIVE,
            sub_modules=["system-prompt-extraction"],
        )

        rendered = _build_learned_experience_context(ctx, executive)

        assert "dispatchable modules" in rendered
        assert "`system-prompt-extraction` (best 8)" in rendered
        assert "target-profiler" not in rendered

    def test_manager_gets_only_dispatchable_child_outcomes(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        graph.add_node(root.id, "system-prompt-extraction", "manager", score=8)
        graph.add_node(root.id, "target-profiler", "child", score=2)
        ctx = _make_ctx(graph=graph)
        manager = _make_module(
            name="system-prompt-extraction",
            sub_modules=["target-profiler"],
        )

        rendered = _build_learned_experience_context(ctx, manager)

        assert "dispatchable modules" in rendered
        assert "`target-profiler`" in rendered
        assert "system-prompt-extraction" not in rendered

    def test_leaf_gets_reusable_evidence_without_module_outcome_advice(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        graph.add_node(
            root.id,
            "direct-ask",
            "ask plainly",
            score=9,
            leaked_info="I follow internal research policy.",
        )
        ctx = _make_ctx(graph=graph)
        leaf = _make_module(name="direct-ask")

        rendered = _build_learned_experience_context(ctx, leaf)

        assert "reusable evidence" in rendered
        assert "I follow internal research policy." in rendered
        assert "Modules that worked" not in rendered
        assert "Low-yield modules" not in rendered

    @pytest.mark.asyncio
    async def test_leaf_prompt_does_not_receive_parent_or_sibling_outcome_advice(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        graph.add_node(root.id, "system-prompt-extraction", "manager", score=2)
        graph.add_node(root.id, "target-profiler", "sibling", score=2)
        captured = []
        ctx = _make_ctx(
            graph=graph,
            captured_messages=captured,
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
        )

        await run_react_loop(_make_module(name="direct-ask"), ctx, "test", log=None)

        combined = "\n".join(
            m.get("content", "") for m in captured[0] if m.get("role") == "user"
        )
        assert "Low-yield modules" not in combined
        assert "Modules that worked" not in combined


# ---------------------------------------------------------------------------
# run_react_loop — basic flows
# ---------------------------------------------------------------------------

class TestReactLoopBasic:
    @pytest.mark.asyncio
    async def test_immediate_conclude(self):
        """Module immediately calls conclude()."""
        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "I found nothing."})]
            ))
        ]
        ctx = _make_ctx(completion_responses=responses)
        module = _make_module()

        result = await run_react_loop(module, ctx, "test instruction")
        assert result == "I found nothing."

    @pytest.mark.asyncio
    async def test_send_then_conclude(self):
        """Module sends a message, gets reply, then concludes."""
        responses = [
            # First: send_message
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "hello target"})]
            )),
            # Second: conclude
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "Target said hi back."})]
            )),
        ]
        ctx = _make_ctx(completion_responses=responses, target_replies=["hi back!"])
        module = _make_module()

        result = await run_react_loop(module, ctx, "test")
        assert "Target said hi back" in result
        assert len(ctx.turns) == 1
        assert ctx.turns[0].sent == "hello target"

    @pytest.mark.asyncio
    async def test_objective_met_short_circuits_before_next_iteration(self):
        """When ctx.objective_met is set during a tool dispatch (judge says
        run's objective has been met), the engine must auto-conclude
        immediately — no more attacker iterations.

        Regression guard for the "leader keeps delegating after a 10/10 win"
        failure observed in the Tensor Trust trace.
        """
        responses = [
            # First iteration: call send_message. The fake dispatch will
            # mark objective_met BEFORE returning — simulating the judge
            # flagging objective_met inside sub_module.handle.
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "probe"})],
            )),
            # Second iteration should NEVER fire. If it does, the fake
            # returns a bogus response that would make the test pass
            # incorrectly — so we count calls to ctx.completion instead.
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "lingering"})],
            )),
        ]
        ctx = _make_ctx(completion_responses=responses, target_replies=["leaked"])

        # Wrap dispatch so the flag gets set mid-tool — matches what
        # _judge_module_result does in real runs.
        from mesmer.core.agent import tools as _tools
        original_dispatch = _tools.dispatch_tool_call

        async def flag_setting_dispatch(fn_name, ctx, module, call, args, instruction, log):
            out = await original_dispatch(fn_name, ctx, module, call, args, instruction, log)
            ctx.objective_met = True
            ctx.objective_met_fragment = "LEAK FRAGMENT"
            return out

        with patch("mesmer.core.agent.engine.dispatch_tool_call",
                   new=flag_setting_dispatch):
            module = _make_module()
            result = await run_react_loop(module, ctx, "test")

        assert "LEAK FRAGMENT" in result
        assert "Objective met" in result
        # Only ONE attacker iteration should have fired — the second
        # scripted response proves the loop correctly short-circuited.
        # ctx.completion is called once per attacker iteration.
        assert ctx.completion.__wrapped__ if hasattr(ctx.completion, "__wrapped__") else True
        # Only one Turn was sent (the probe); the second-iteration fake
        # "lingering" probe never reached the target.
        assert len(ctx.turns) == 1
        assert ctx.turns[0].sent == "probe"

    @pytest.mark.asyncio
    async def test_max_iterations(self):
        """Loop hits max iterations without conclude."""
        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "attempt"})]
            ))
            for _ in range(100)
        ]
        ctx = _make_ctx(
            completion_responses=responses,
            target_replies=["reply"] * 100,
            max_turns=100,
        )
        module = _make_module()

        result = await run_react_loop(module, ctx, "test", max_iterations=3)
        assert "Max iterations" in result


# ---------------------------------------------------------------------------
# run_react_loop — circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_reasoning_only_forces_tool(self):
        """After 3 reasoning turns, circuit breaker injects force message."""
        log_events = []

        def log(event, detail=""):
            log_events.append(event)

        responses = [
            # 3 reasoning-only turns
            FakeResponse(FakeMessage(content="Let me think...")),
            FakeResponse(FakeMessage(content="Still thinking...")),
            FakeResponse(FakeMessage(content="More thinking...")),
            # After circuit breaker forces, model concludes
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "forced to conclude"})]
            )),
        ]
        ctx = _make_ctx(completion_responses=responses)
        module = _make_module()

        result = await run_react_loop(module, ctx, "test", log=log)
        assert "forced to conclude" in result
        assert "circuit_break" in log_events

    @pytest.mark.asyncio
    async def test_hard_stop_after_6_reasoning(self):
        """After 6 reasoning turns, hard stop auto-concludes."""
        responses = [
            FakeResponse(FakeMessage(content=f"Thinking {i}..."))
            for i in range(7)
        ]
        ctx = _make_ctx(completion_responses=responses)
        module = _make_module()

        result = await run_react_loop(module, ctx, "test")
        assert "refused" in result.lower()


# ---------------------------------------------------------------------------
# run_react_loop — error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error_non_transient(self):
        """Non-transient error → fails immediately."""
        ctx = _make_ctx()
        ctx.completion = AsyncMock(side_effect=Exception("Invalid API key"))
        module = _make_module()

        result = await run_react_loop(module, ctx, "test")
        assert "LLM error" in result

    @pytest.mark.asyncio
    async def test_llm_error_transient_retries(self):
        """Transient provider error → retries then gives up."""
        call_count = [0]
        async def failing_completion(messages, tools=None):
            call_count[0] += 1
            raise Exception("Provider returned error 503")

        ctx = _make_ctx()
        ctx.completion = failing_completion
        module = _make_module()

        # Patch the retry delays to be instant for tests
        import mesmer.core.agent as agent_mod
        original_delays = agent_mod.RETRY_DELAYS
        agent_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            agent_mod.RETRY_DELAYS = original_delays

        assert "LLM error" in result or "retries exhausted" in result
        assert call_count[0] == 3  # tried 3 times

    @pytest.mark.asyncio
    async def test_empty_choices_retries_then_recovers(self):
        """Provider sometimes ships a 200 OK with empty `choices` (Gemini does
        this on safety blocks / 0-token completions). LiteLLM passes that
        through unchanged — no exception is raised — so the retry layer's
        exception path doesn't catch it. Without an explicit empty-choices
        guard the engine indexes into [] and the leader's run dies with
        ``Error: list index out of range``.

        The fix lives in retry.py: empty choices is treated as a transient
        failure (same path as 503/timeout) and retried with backoff."""
        call_count = [0]

        class _EmptyChoicesResponse:
            choices: list = []

        async def empty_then_conclude(messages, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: provider returns empty choices.
                return _EmptyChoicesResponse()
            # Retry: model concludes normally.
            return FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "done"})]
            ))

        ctx = _make_ctx()
        ctx.completion = empty_then_conclude
        module = _make_module()

        # Patch retry delays to be instant.
        import mesmer.core.agent as agent_mod
        original_delays = agent_mod.RETRY_DELAYS
        agent_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            agent_mod.RETRY_DELAYS = original_delays

        assert result == "done"
        assert call_count[0] == 2  # empty response triggered retry, retry succeeded

    @pytest.mark.asyncio
    async def test_empty_choices_exhausts_retries_returns_clean_error(self):
        """When the provider keeps shipping empty choices past the retry
        budget, the loop must return a clean LLM error string — not crash
        with IndexError."""
        call_count = [0]

        class _EmptyChoicesResponse:
            choices: list = []

        async def always_empty(messages, tools=None):
            call_count[0] += 1
            return _EmptyChoicesResponse()

        ctx = _make_ctx()
        ctx.completion = always_empty
        module = _make_module()

        import mesmer.core.agent as agent_mod
        original_delays = agent_mod.RETRY_DELAYS
        agent_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            agent_mod.RETRY_DELAYS = original_delays

        # Same path as exhausted transient retries: clean string, no crash.
        assert "LLM error" in result or "retries exhausted" in result
        assert call_count[0] == 3  # tried MAX_LLM_RETRIES times

    @pytest.mark.asyncio
    async def test_llm_error_transient_recovers(self):
        """Transient error on first call, succeeds on retry."""
        call_count = [0]
        async def flaky_completion(messages, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Provider returned error 502")
            # Succeed on retry: conclude immediately
            return FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "recovered!"})]
            ))

        ctx = _make_ctx()
        ctx.completion = flaky_completion
        module = _make_module()

        import mesmer.core.agent as agent_mod
        original_delays = agent_mod.RETRY_DELAYS
        agent_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            agent_mod.RETRY_DELAYS = original_delays

        assert result == "recovered!"
        assert call_count[0] == 2  # failed once, succeeded on retry

    @pytest.mark.asyncio
    async def test_target_send_error(self):
        """Target send fails → error in tool result, loop continues."""
        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "hello"})]
            )),
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "done despite error"})]
            )),
        ]
        ctx = _make_ctx(completion_responses=responses)
        # Override target to fail
        ctx.target.send = AsyncMock(side_effect=Exception("Connection dropped"))

        module = _make_module()
        result = await run_react_loop(module, ctx, "test")
        assert "done despite error" in result

    @pytest.mark.asyncio
    async def test_rate_limit_retries_same_key_without_rotation(self):
        """On RateLimitError, retry with backoff on the same configured key."""
        from mesmer.core.keys import KeyPool

        ctx = _make_ctx()
        ctx.agent_config.api_key = "KEY_A"
        ctx.agent_config._keys = ["KEY_A"]
        ctx.agent_config._pool = KeyPool(["KEY_A"])

        keys_seen = []

        async def rate_limited_then_succeed(messages, tools=None):
            key = ctx.agent_config.next_key()
            ctx._last_key_used = key
            keys_seen.append(key)
            if len(keys_seen) == 1:
                raise Exception(
                    'RateLimitError: OpenrouterException - {"error":{"message":'
                    '"Rate limit exceeded: free-models-per-day","code":429}}'
                )
            return FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "retried ok"})]
            ))

        ctx.completion = rate_limited_then_succeed
        module = _make_module()

        import mesmer.core.agent as agent_mod
        original_delays = agent_mod.RETRY_DELAYS
        agent_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            agent_mod.RETRY_DELAYS = original_delays

        assert result == "retried ok"
        assert len(keys_seen) == 2
        assert keys_seen[0] == keys_seen[1] == "KEY_A"
        assert ctx.agent_config.pool.active_count() == 1

    @pytest.mark.asyncio
    async def test_rate_limit_all_keys_exhausted(self):
        """If the configured key keeps rate-limiting, the run stops cleanly."""
        from mesmer.core.keys import KeyPool

        ctx = _make_ctx()
        ctx.agent_config._keys = ["KEY_A"]
        ctx.agent_config._pool = KeyPool(["KEY_A"])

        async def always_rate_limited(messages, tools=None):
            ctx._last_key_used = ctx.agent_config.next_key()
            raise Exception(
                'RateLimitError: Rate limit exceeded: free-models-per-day, 429'
            )

        ctx.completion = always_rate_limited
        module = _make_module()

        events = []
        def log(event, detail=""):
            events.append((event, detail))

        import mesmer.core.agent as agent_mod
        original_delays = agent_mod.RETRY_DELAYS
        agent_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test", log=log)
        finally:
            agent_mod.RETRY_DELAYS = original_delays

        assert "LLM error" in result
        assert not any(evt == "rate_limit_wall" for evt, _ in events)
        assert ctx.agent_config.pool.active_count() == 1

    @pytest.mark.asyncio
    async def test_budget_exhausted_during_send(self):
        """Turn budget runs out mid-loop → agent told to conclude."""
        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "1"})]
            )),
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "2"})]
            )),
            # After budget exhaustion, model should conclude
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "budget ran out"})]
            )),
        ]
        ctx = _make_ctx(
            completion_responses=responses,
            target_replies=["reply1", "reply2"],
            max_turns=1,  # only 1 turn allowed
        )
        module = _make_module()

        result = await run_react_loop(module, ctx, "test")
        assert "budget" in result.lower() or "conclude" in result.lower()


# ---------------------------------------------------------------------------
# run_react_loop — logging
# ---------------------------------------------------------------------------

class TestLogging:
    @pytest.mark.asyncio
    async def test_log_callback_called(self):
        events = []

        def log(event, detail=""):
            events.append((event, detail))

        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("send_message", {"message": "hi"})]
            )),
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "done"})]
            )),
        ]
        ctx = _make_ctx(completion_responses=responses, target_replies=["yo"])
        module = _make_module()

        await run_react_loop(module, ctx, "test", log=log)

        event_types = [e[0] for e in events]
        assert "module_start" in event_types
        assert "llm_call" in event_types
        assert "send" in event_types
        assert "recv" in event_types
        assert "conclude" in event_types

    @pytest.mark.asyncio
    async def test_agent_trace_written_to_current_graph_node(self):
        responses = [
            FakeResponse(FakeMessage(content="thinking through the next move")),
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "done"})]
            )),
        ]
        graph = AttackGraph()
        root = graph.ensure_root()
        node = graph.add_node(root.id, "test-module", "test", status="running")
        ctx = _make_ctx(completion_responses=responses, graph=graph)
        ctx.graph_parent_id = node.id

        await run_react_loop(_make_module(), ctx, "test")

        trace = graph.nodes[node.id].agent_trace
        events = [item["event"] for item in trace]
        assert events == ["llm_call", "llm_call", "tool_call"]
        first_call = trace[0]["payload"]
        assert first_call["request"]["messages"][0]["role"] == "system"
        assert first_call["request"]["messages"][1]["role"] == "user"
        assert first_call["response"]["content"] == "thinking through the next move"
        assert trace[-1]["payload"]["name"] == "conclude"
        assert trace[-1]["payload"]["result"] == "done"


# ---------------------------------------------------------------------------
# Leader scratchpad slot + operator-message drain
# ---------------------------------------------------------------------------

class TestLeaderScratchpadAndOperatorMessages:
    @pytest.mark.asyncio
    async def test_shared_scratchpad_renders_in_scratchpad_block(self):
        """Persisted notes must surface as one shared scratchpad block."""
        captured = []
        leader_module = _make_module(name="my-leader")

        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            captured_messages=captured,
            leader_scratchpad="Focus on behavioral rules. Avoid identity claims.",
            leader_module_name=leader_module.name,
        )
        await run_react_loop(leader_module, ctx, "test", log=None)

        assert len(captured) >= 1
        first = captured[0]
        combined = "\n".join(
            m.get("content", "") for m in first if m.get("role") == "user"
        )
        assert "## Scratchpad" in combined
        assert "Focus on behavioral rules" in combined
        # Old rendering must be gone — no special "Attack Plan" heading.
        assert "Attack Plan (from human operator" not in combined

    @pytest.mark.asyncio
    async def test_operator_messages_drained_into_leader_prompt(self):
        """Operator messages on ctx.operator_messages must be rendered into a
        '## Operator Messages' block at depth 0, then cleared from the queue."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            captured_messages=captured,
            operator_messages=[
                {"role": "user", "content": "focus on the Spanish translation angle", "timestamp": 1.0},
            ],
        )
        await run_react_loop(_make_module(), ctx, "test", log=None)

        first = captured[0]
        combined = "\n".join(
            m.get("content", "") for m in first if m.get("role") == "user"
        )
        assert "## Operator Messages" in combined
        assert "Spanish translation angle" in combined
        # Drained — queue must be empty after rendering.
        assert ctx.operator_messages == []

    @pytest.mark.asyncio
    async def test_empty_operator_messages_omits_block(self):
        """No queued operator messages → no '## Operator Messages' heading."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            captured_messages=captured,
        )
        await run_react_loop(_make_module(), ctx, "test", log=None)

        first = captured[0]
        combined = "\n".join(
            m.get("content", "") for m in first if m.get("role") == "user"
        )
        assert "## Operator Messages" not in combined


# ---------------------------------------------------------------------------
# Fresh-session framing (P0)
# ---------------------------------------------------------------------------

class TestLogHygiene:
    """P6 — user-facing log details must not truncate mid-sentence, iteration
    labels must expose depth, and budget exhaustion must explain itself."""

    @pytest.mark.asyncio
    async def test_llm_call_log_includes_depth(self):
        events = []

        def _log(event, detail=""):
            events.append((event, detail))

        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
        )
        # Simulate a nested module: depth = 2
        ctx.depth = 2
        module = _make_module(name="narrative-transport")

        await run_react_loop(module, ctx, "test", log=_log)

        llm_events = [d for (e, d) in events if e == "llm_call"]
        assert llm_events
        assert "depth=2" in llm_events[0]
        assert "narrative-transport" in llm_events[0]

    @pytest.mark.asyncio
    async def test_send_log_not_truncated(self):
        events = []

        def _log(event, detail=""):
            events.append((event, detail))

        long_message = "x" * 500
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": long_message})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "done"})]
                )),
            ],
            target_replies=["y" * 500],
            max_turns=5,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=_log)

        send_events = [d for (e, d) in events if e == "send"]
        recv_events = [d for (e, d) in events if e == "recv"]
        assert send_events and long_message in send_events[0]
        assert recv_events and ("y" * 500) in recv_events[0]

    @pytest.mark.asyncio
    async def test_budget_exhausted_log_explains_next_action(self):
        events = []

        def _log(event, detail=""):
            events.append((event, detail))

        # max_turns=1 → after the first send, the next send raises.
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": "first"})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": "second"})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "done"})]
                )),
            ],
            target_replies=["ack"],
            max_turns=1,
        )
        module = _make_module(name="cognitive-overload")
        await run_react_loop(module, ctx, "test", log=_log)

        budget_events = [d for (e, d) in events if e == "budget"]
        assert budget_events, "budget exhaustion must be logged"
        detail = budget_events[0]
        assert "cognitive-overload" in detail
        assert "1/1" in detail or "1 " in detail
        # Must explain: this module concludes, parent leader can still delegate
        assert "MUST conclude" in detail
        assert "parent" in detail.lower()


class TestFreshSessionFraming:
    """When ctx.target_fresh_session is True, the attacker LLM prompt must
    clearly distinguish prior-intel from current-session state, and must not
    mislead the attacker into treating the target as if it remembered old turns.
    """

    @pytest.mark.asyncio
    async def test_fresh_session_banner_injected(self):
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            captured_messages=captured,
        )
        ctx.target_fresh_session = True
        module = _make_module()

        await run_react_loop(module, ctx, "test", log=None)

        combined = "\n".join(
            m.get("content", "") for m in captured[0] if m.get("role") == "user"
        )
        assert "Fresh target session" in combined

    @pytest.mark.asyncio
    async def test_fresh_session_reframes_prior_turns_as_intel(self):
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            captured_messages=captured,
        )
        # Simulate prior sibling-module activity
        await ctx.send("sibling probed earlier", module_name="sibling")
        ctx.target_fresh_session = True
        module = _make_module()

        await run_react_loop(module, ctx, "test", log=None)

        combined = "\n".join(
            m.get("content", "") for m in captured[0] if m.get("role") == "user"
        )
        assert "Prior intel from sibling modules" in combined
        # Must warn against referencing prior turns back to the target
        assert "do NOT reference them" in combined
        assert "Conversation so far:" not in combined

    @pytest.mark.asyncio
    async def test_budget_banner_emphasises_one_shot(self):
        """With max_turns=1 the initial message must make the single-shot
        constraint unmissable — otherwise modules burn their one send on
        warm-up. Repro of the cognitive-overload max_turns=1 trap.
        """
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            max_turns=1,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        combined = "\n".join(
            m.get("content", "") for m in captured[0] if m.get("role") == "user"
        )
        assert "ONE SHOT" in combined
        assert "exactly **1 send_message**" in combined

    @pytest.mark.asyncio
    async def test_budget_banner_default_multi_turn(self):
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            max_turns=5,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        combined = "\n".join(
            m.get("content", "") for m in captured[0] if m.get("role") == "user"
        )
        assert "## Budget" in combined
        assert "**5** times" in combined
        assert "ONE SHOT" not in combined

    @pytest.mark.asyncio
    async def test_send_tool_result_shows_remaining_budget(self):
        """After each send, the attacker must see how many sends remain —
        so it can decide whether to deepen or wrap up."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": "probe 1"})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "done"})]
                )),
            ],
            target_replies=["ack"],
            max_turns=5,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        # The second LLM call sees the tool_result from the first send.
        tool_results = [
            m.get("content", "") for m in captured[1] if m.get("role") == "tool"
        ]
        joined = "\n".join(tool_results)
        assert "Budget:" in joined
        assert "4/5 sends remaining" in joined

    @pytest.mark.asyncio
    async def test_send_tool_result_does_not_include_belief_update_bookkeeping(self):
        """BeliefGraph updates are framework telemetry, not target replies.

        The next LLM call should see a clean send_message result containing
        the target response and budget only; evidence extraction details live
        in logs / BeliefMap.
        """
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": "probe 1"})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "done"})]
                )),
            ],
            target_replies=["ack"],
            max_turns=5,
            captured_messages=captured,
        )

        with patch(
            "mesmer.core.agent.evaluation._update_belief_graph_from_turn",
            new=AsyncMock(return_value=[object()]),
        ):
            await run_react_loop(_make_module(), ctx, "test", log=None)

        tool_results = [
            m.get("content", "") for m in captured[1] if m.get("role") == "tool"
        ]
        joined = "\n".join(tool_results)
        assert "Target replied: ack" in joined
        assert "Belief evidence updated" not in joined
        assert "hidden_instruction_fragment" not in joined

    @pytest.mark.asyncio
    async def test_pipeline_error_not_framed_as_refusal(self):
        """When the target emits a timeout/gateway error, the tool_result
        must NOT say 'Target replied' — that misleads the attacker into
        thinking the technique failed when it never landed (P4)."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": "probe 1"})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "done"})]
                )),
            ],
            target_replies=["(timeout — no response)"],
            max_turns=5,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        tool_results = [
            m.get("content", "") for m in captured[1] if m.get("role") == "tool"
        ]
        joined = "\n".join(tool_results)
        assert "Target-side pipeline error" in joined
        assert "did NOT refuse" in joined
        # Must not fall back to the "Target replied:" wording used for real
        # responses — that's the exact misframing P4 eliminates.
        assert "Target replied:" not in joined

    @pytest.mark.asyncio
    async def test_send_tool_result_last_shot_warning(self):
        """When exactly 1 send remains, the tool_result must flag that
        loudly — not just as a number."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("send_message", {"message": "probe 1"})]
                )),
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "done"})]
                )),
            ],
            target_replies=["ack"],
            max_turns=2,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        tool_results = [
            m.get("content", "") for m in captured[1] if m.get("role") == "tool"
        ]
        joined = "\n".join(tool_results)
        assert "1 send remaining" in joined
        assert "last shot" in joined

    @pytest.mark.asyncio
    async def test_default_still_shows_conversation_so_far(self):
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            captured_messages=captured,
        )
        await ctx.send("prior exchange")
        module = _make_module()

        await run_react_loop(module, ctx, "test", log=None)

        combined = "\n".join(
            m.get("content", "") for m in captured[0] if m.get("role") == "user"
        )
        assert "Conversation so far:" in combined
        assert "Fresh target session" not in combined
        assert "Prior intel from sibling modules" not in combined


# ---------------------------------------------------------------------------
# Continuous-mode framing (C2, C4, C5)
# ---------------------------------------------------------------------------

class TestContinuationPreamble:
    """C2 — CONTINUATION_PREAMBLE prepends to the attacker system message
    only when ctx.scenario_mode == CONTINUOUS."""

    @pytest.mark.asyncio
    async def test_preamble_absent_in_trials(self):
        captured: list = []
        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
            ))
        ]
        ctx = _make_ctx(completion_responses=responses, captured_messages=captured)
        # Default mode is TRIALS.
        from mesmer.core.constants import ScenarioMode
        assert ctx.scenario_mode == ScenarioMode.TRIALS

        module = _make_module()
        await run_react_loop(module, ctx, "probe")

        # First LLM call's system message must NOT contain the preamble.
        system_msg = captured[0][0]["content"]
        assert "Continuous-conversation mode" not in system_msg
        assert "You are one move inside a single ongoing conversation" not in system_msg

    @pytest.mark.asyncio
    async def test_preamble_present_in_continuous(self):
        from mesmer.core.constants import ScenarioMode

        captured: list = []
        responses = [
            FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
            ))
        ]
        ctx = _make_ctx(completion_responses=responses, captured_messages=captured)
        ctx.scenario_mode = ScenarioMode.CONTINUOUS

        module = _make_module()
        await run_react_loop(module, ctx, "probe")

        system_msg = captured[0][0]["content"]
        # A meaningful chunk of the preamble must be present.
        assert "Continuous-conversation mode" in system_msg
        assert "ongoing conversation" in system_msg
        # Preamble comes FIRST so the framing dominates the module prompt.
        assert system_msg.index("Continuous-conversation mode") < system_msg.index("test-module")


class TestUpdateGraphParentAttach:
    """AttackGraph parentage follows delegation context, not chronology."""

    @pytest.mark.asyncio
    async def test_fresh_attempt_attaches_to_root_in_trials(self):
        from mesmer.core.constants import ScenarioMode

        ctx = _make_ctx()
        graph = ctx.graph
        # Seed an explored node under root — in TRIALS it's still a sibling
        # relationship: the next fresh attempt ALSO hangs off root, not under
        # the prior node. That's the trials-mode contract.
        graph.add_node(graph.root_id, "foo", "first one words", score=4)
        assert ctx.scenario_mode == ScenarioMode.TRIALS

        judge = JudgeResult(5, "leaked", "angle", "dead", "next")
        node = _update_graph(
            ctx, "bar", "second angle words plenty",
            judge, log=lambda *a, **kw: None,
            messages_sent=["hi"], target_responses=["ok"],
        )
        assert node.parent_id == graph.root_id

    @pytest.mark.asyncio
    async def test_continuous_mode_does_not_infer_parent_from_latest_node(self):
        from mesmer.core.constants import ScenarioMode

        ctx = _make_ctx()
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        graph = ctx.graph
        prior = graph.add_node(graph.root_id, "foo", "first one words", score=4)

        judge = JudgeResult(5, "leaked", "angle", "dead", "next")
        node = _update_graph(
            ctx, "bar", "second angle words plenty",
            judge, log=lambda *a, **kw: None,
            messages_sent=["hi"], target_responses=["ok"],
        )
        assert prior.parent_id == graph.root_id
        assert node.parent_id == graph.root_id

    @pytest.mark.asyncio
    async def test_delegation_parent_context_is_authoritative(self):
        from mesmer.core.constants import ScenarioMode

        ctx = _make_ctx()
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        graph = ctx.graph
        parent = graph.add_node(graph.root_id, "manager", "dispatch owner", score=0)
        ctx.graph_parent_id = parent.id
        judge = JudgeResult(5, "", "", "", "")
        node = _update_graph(
            ctx, "bar", "opening move plenty words",
            judge, log=lambda *a, **kw: None,
            messages_sent=[], target_responses=[],
        )
        assert node.parent_id == parent.id
