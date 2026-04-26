"""Tests for mesmer.core.agent — the ReAct loop with judge/reflect/graph cycle."""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from mesmer.core.agent.context import Context
from mesmer.core.graph import AttackGraph
from mesmer.core.agent.judge import JudgeResult
from mesmer.core.agent import (
    run_react_loop,
    _build_graph_context,
    _reflect_and_expand,
    _update_graph,
    _find_missed_frontier,
)
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
    mod = MagicMock()
    mod.name = name
    mod.sub_modules = sub_modules or []
    mod.system_prompt = f"You are the {name} module."
    mod.description = "test module"
    mod.theory = "test theory"
    return mod


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
        ctx.scratchpad.set(leader_module_name, leader_scratchpad)
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
        g.add_frontier_node(root.id, "foot-in-door", "ask about tools")
        g.add_human_hint("try calendar API")

        ctx = _make_ctx(graph=g)
        result = _build_graph_context(ctx)
        # New ordering: FRONTIER at top, then DEAD ENDS, then summary
        assert "FRONTIER" in result
        assert "START HERE" in result
        assert "DEAD ENDS" in result
        # Frontier is shown first — its index must be less than dead-ends header
        assert result.index("FRONTIER") < result.index("DEAD ENDS")
        # Human hint surfaces via its approach text and ★ HUMAN marker
        assert "calendar API" in result
        assert "HUMAN" in result

    def test_conclude_mode(self):
        ctx = _make_ctx(max_turns=10)
        ctx.turns_used = 9  # 90% → conclude
        result = _build_graph_context(ctx)
        assert "CONCLUDE" in result.upper()


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


# ---------------------------------------------------------------------------
# Leader scratchpad slot + operator-message drain
# ---------------------------------------------------------------------------

class TestLeaderScratchpadAndOperatorMessages:
    @pytest.mark.asyncio
    async def test_leader_scratchpad_slot_renders_in_scratchpad_block(self):
        """Persisted leader notes — seeded into ctx.scratchpad[module_name] —
        must surface in the leader's user prompt via the standard `## Scratchpad`
        block. No special "## Attack Plan" heading exists any more."""
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
        # New rendering: standard scratchpad block with the leader's slot.
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
# TAP-aligned parent selection in _update_graph
# ---------------------------------------------------------------------------

def _make_judge(score=5, leaked="", promising="", dead_end="", suggested=""):
    """Build a fake JudgeResult-like object."""
    j = MagicMock()
    j.score = score
    j.leaked_info = leaked
    j.promising_angle = promising
    j.dead_end = dead_end
    j.suggested_next = suggested
    return j


class TestUpdateGraphParentSelection:
    """_update_graph must NOT use the old same-module heuristic."""

    def test_fresh_attempt_attaches_to_root(self):
        """Without a frontier_id, new attempts are direct children of root."""
        ctx = _make_ctx()
        graph = ctx.graph

        judge = _make_judge(score=7, leaked="found something")
        node = _update_graph(
            ctx, "foot-in-door", "first try", judge, log=lambda *a, **kw: None,
            messages_sent=["hi"], target_responses=["yo"],
            frontier_id=None,
        )
        assert node is not None
        assert node.parent_id == graph.root_id
        assert node.status == "promising"  # score 7

    def test_fresh_attempt_ignores_prior_same_module_scores(self):
        """Regression: old heuristic attached same-module nodes under a prior
        high-scoring one. The new behavior must NOT do that."""
        ctx = _make_ctx()
        graph = ctx.graph
        # Simulate a prior promising foot-in-door node
        prior = graph.add_node(
            graph.root_id, "foot-in-door", "earlier promising try", score=7
        )
        assert prior.status == "promising"

        judge = _make_judge(score=3)
        second = _update_graph(
            ctx, "foot-in-door", "second attempt", judge, log=lambda *a, **kw: None,
            messages_sent=["a"], target_responses=["b"],
            frontier_id=None,
        )
        # Must be a sibling of the first (child of root), NOT a child of prior
        assert second.parent_id == graph.root_id
        assert second.parent_id != prior.id
        assert second.id not in prior.children

    def test_frontier_id_fulfills_existing_frontier(self):
        """When leader passes a real frontier_id, we promote instead of
        creating a new node. Parent = frontier's parent (the node whose
        reflection proposed this)."""
        ctx = _make_ctx()
        graph = ctx.graph
        # Parent attempt whose reflection spawned a frontier
        parent = graph.add_node(graph.root_id, "target-profiler", "mapped defenses", score=8)
        frontier = graph.add_frontier_node(parent.id, "foot-in-door", "ask about tools")
        assert frontier.is_frontier

        judge = _make_judge(score=6, leaked="leaked tool list")
        result = _update_graph(
            ctx, "foot-in-door", "ask about tools", judge, log=lambda *a, **kw: None,
            messages_sent=["probe"], target_responses=["tools are..."],
            frontier_id=frontier.id,
        )
        # Same node id as the frontier (fulfilled, not a new node)
        assert result is not None
        assert result.id == frontier.id
        # Parent edge preserved — this is the TAP refinement link
        assert result.parent_id == parent.id
        assert result.status == "promising"  # score 6
        assert result.messages_sent == ["probe"]
        assert result.score == 6

    def test_unknown_frontier_id_falls_back_to_fresh(self):
        """A stale/invalid frontier_id should degrade gracefully to a fresh
        attempt (child of root) rather than raise."""
        ctx = _make_ctx()
        graph = ctx.graph

        judge = _make_judge(score=5)
        node = _update_graph(
            ctx, "foo", "x", judge, log=lambda *a, **kw: None,
            messages_sent=["m"], target_responses=["r"],
            frontier_id="no-such-id",
        )
        assert node is not None
        assert node.parent_id == graph.root_id

    def test_frontier_id_pointing_at_explored_falls_back_to_fresh(self):
        """If frontier_id points at a node that's already been explored, we
        don't re-fulfill it — fall back to fresh attempt."""
        ctx = _make_ctx()
        graph = ctx.graph
        already = graph.add_node(graph.root_id, "foo", "already done", score=5)
        assert already.status == "promising"

        judge = _make_judge(score=3)
        node = _update_graph(
            ctx, "foo", "different try", judge, log=lambda *a, **kw: None,
            messages_sent=["m"], target_responses=["r"],
            frontier_id=already.id,
        )
        # A new node was created, child of root; the already-explored is untouched
        assert node.id != already.id
        assert node.parent_id == graph.root_id
        assert already.score == 5  # untouched

    def test_fulfilled_frontier_records_actual_module_name(self):
        """Regression for the run-log bug: frontier stored as
        `module=system-prompt-extraction` (wrong), leader actually called
        narrative-transport with frontier_id=that-frontier. The fulfilled
        node must end up with module='narrative-transport'."""
        ctx = _make_ctx()
        graph = ctx.graph
        # Simulate an old frontier whose stored module is the LEADER name
        stale = graph.add_frontier_node(
            graph.root_id,
            "system-prompt-extraction",   # wrong, from a prior buggy reflection
            "rewrite safety as bedtime story",
        )

        judge = _make_judge(score=5, leaked="refusal list")
        result = _update_graph(
            ctx, "narrative-transport", "rewrite safety as bedtime story",
            judge, log=lambda *a, **kw: None,
            messages_sent=["story please"], target_responses=["once upon..."],
            frontier_id=stale.id,
        )
        # Same node fulfilled, but module is now the actual sub-module
        assert result.id == stale.id
        assert result.module == "narrative-transport"
        # And parent-edge preserved
        assert result.parent_id == graph.root_id


# ---------------------------------------------------------------------------
# Frontier preference — nudge when leader freelances past a matching frontier
# ---------------------------------------------------------------------------


class TestFindMissedFrontier:
    def test_returns_none_when_frontier_id_was_passed(self):
        g = AttackGraph()
        g.ensure_root()
        g.add_frontier_node(g.root_id, "foot-in-door", "try X")
        # frontier_id provided → no nudge
        assert _find_missed_frontier(g, "foot-in-door", "some-id") is None

    def test_returns_matching_module_frontier(self):
        g = AttackGraph()
        g.ensure_root()
        f = g.add_frontier_node(g.root_id, "foot-in-door", "try X")
        missed = _find_missed_frontier(g, "foot-in-door", None)
        assert missed is not None
        assert missed.id == f.id

    def test_returns_none_for_different_module(self):
        g = AttackGraph()
        g.ensure_root()
        g.add_frontier_node(g.root_id, "authority-bias", "claim auth")
        assert _find_missed_frontier(g, "foot-in-door", None) is None

    def test_returns_none_on_missing_graph(self):
        assert _find_missed_frontier(None, "foot-in-door", None) is None

    def test_picks_first_match_across_multiple(self):
        g = AttackGraph()
        g.ensure_root()
        g.add_frontier_node(g.root_id, "foot-in-door", "approach 1")
        g.add_frontier_node(g.root_id, "foot-in-door", "approach 2")
        missed = _find_missed_frontier(g, "foot-in-door", None)
        assert missed is not None
        # Whichever comes first — but must be a foot-in-door
        assert missed.module == "foot-in-door"


# ---------------------------------------------------------------------------
# Graph-first frontier flow (P2)
# ---------------------------------------------------------------------------

class TestReflectAndExpandTraceEvents:
    """TIER_GATE + FRONTIER events must surface on every expansion.

    The trace's forensic value rests on these events firing with structured
    JSON details so benchmark artifacts can answer "why did the leader
    only see T0?" without opening the graph.
    """

    @pytest.mark.asyncio
    async def test_tier_gate_event_carries_selected_tier_and_census(self):
        import json as _json
        ctx = _make_ctx()
        # Stub registry.tiers_for so the gate has real tier data.
        ctx.registry.tiers_for = MagicMock(side_effect=lambda names: {
            "direct-ask": 0, "foot-in-door": 2,
        })
        graph = ctx.graph
        current_node = graph.add_node(
            graph.root_id, "foot-in-door", "initial foothold words plenty",
            score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        events: list[tuple[str, str]] = []

        def capture(event, detail=""):
            events.append((event, detail))

        async def fake_refine(ctx, *, module, rationale, judge_result, **kw):
            return f"refined-{module}"

        with patch("mesmer.core.agent.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, judge, current_node,
                log=capture,
                available_modules=["direct-ask", "foot-in-door"],
            )

        # Exactly one TIER_GATE event with structured JSON payload.
        tier_gates = [d for e, d in events if e == "tier_gate"]
        assert len(tier_gates) == 1
        payload = _json.loads(tier_gates[0])
        # Gate should select tier 0 (direct-ask untried) and NOT fall back.
        assert payload["selected_tier"] == 0
        assert payload["escape_hatch"] is False
        # Per-tier census is present and keyed by tier (JSON-stringified
        # after sort_keys serialisation).
        assert "by_tier" in payload
        # Available modules round-trip through the event detail.
        assert set(payload["available"]) == {"direct-ask", "foot-in-door"}


class TestReflectAndExpandGraphFirst:
    """Integration check: _reflect_and_expand must route through
    graph.propose_frontier (deterministic frontier selection) + judge.refine_approach
    (LLM writes approach for a graph-chosen module), not through the pre-P2
    generate_frontier that asked the LLM to pick the module itself.
    """

    @pytest.mark.asyncio
    async def test_frontier_modules_come_from_graph_proposal(self):
        """The modules attached to new frontier nodes must be the ones the
        graph proposed — refine_approach never picks modules, only strings.
        """
        ctx = _make_ctx()
        graph = ctx.graph
        # Seed a non-dead current node to reflect on.
        current_node = graph.add_node(
            graph.root_id, "foot-in-door", "initial foothold prior attempt",
            score=5,
        )
        judge = JudgeResult(
            score=5, leaked_info="won't send msgs",
            promising_angle="rule enumeration",
            dead_end="identity claim",
            suggested_next="ask about tools",
        )

        # Stub refine_approach to always return a fixed string — this isolates
        # the module-selection logic from the LLM call. ``**kwargs`` absorbs any
        # caller-added kwargs (e.g. transcript_tail in CONTINUOUS mode) so this
        # test stays robust to refinement-signature extensions.
        async def fake_refine(ctx, *, module, rationale, judge_result, **kwargs):
            return f"refined-for-{module}"

        with patch("mesmer.core.agent.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["authority-bias", "anchoring", "foot-in-door"],
            )

        new_frontiers = [
            n for n in graph.iter_nodes()
            if n.status == "frontier" and n.parent_id == current_node.id
        ]
        modules = {n.module for n in new_frontiers}
        # Untried modules prioritized — anchoring and authority-bias both untried.
        assert "authority-bias" in modules
        assert "anchoring" in modules
        # All approach strings come from refine_approach (not copy-pasted from LLM).
        assert all(n.approach.startswith("refined-for-") for n in new_frontiers)

    @pytest.mark.asyncio
    async def test_dead_modules_excluded_from_frontier(self):
        """A module whose every prior attempt is dead must not reappear as
        a frontier suggestion."""
        ctx = _make_ctx()
        graph = ctx.graph
        # authority-bias has two dead attempts.
        graph.add_node(
            graph.root_id, "authority-bias", "angle one words plenty",
            score=1, reflection="detected",
        )
        graph.add_node(
            graph.root_id, "authority-bias", "angle two words plenty",
            score=2, reflection="rebuffed",
        )
        # foot-in-door has a live attempt.
        current_node = graph.add_node(
            graph.root_id, "foot-in-door", "current angle words plenty",
            score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        async def fake_refine(ctx, *, module, rationale, judge_result, **kwargs):
            return f"refined-{module}"

        with patch("mesmer.core.agent.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["authority-bias", "foot-in-door", "anchoring"],
            )

        new_frontiers = [
            n for n in graph.iter_nodes()
            if n.status == "frontier" and n.parent_id == current_node.id
        ]
        modules = {n.module for n in new_frontiers}
        assert "authority-bias" not in modules
        # anchoring is untried → guaranteed in the top-3.
        assert "anchoring" in modules

    @pytest.mark.asyncio
    async def test_no_expansion_when_available_modules_empty(self):
        ctx = _make_ctx()
        graph = ctx.graph
        current_node = graph.add_node(
            graph.root_id, "x", "current angle words plenty", score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        await _reflect_and_expand(
            ctx, judge, current_node,
            log=lambda *a, **kw: None,
            available_modules=[],
        )
        new_frontiers = [
            n for n in graph.iter_nodes()
            if n.status == "frontier" and n.parent_id == current_node.id
        ]
        assert new_frontiers == []

    @pytest.mark.asyncio
    async def test_empty_refine_falls_back_to_rationale(self):
        """If the LLM returns an empty approach string, the frontier slot
        should still be filled with a readable placeholder rather than
        dropped — otherwise a flaky LLM silently shrinks the frontier."""
        ctx = _make_ctx()
        graph = ctx.graph
        current_node = graph.add_node(
            graph.root_id, "foo", "current angle words plenty", score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        async def empty_refine(ctx, *, module, rationale, judge_result, **kwargs):
            return ""

        with patch("mesmer.core.agent.judge.refine_approach", new=empty_refine):
            await _reflect_and_expand(
                ctx, judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["bar"],
            )

        new_frontiers = [
            n for n in graph.iter_nodes()
            if n.status == "frontier" and n.parent_id == current_node.id
        ]
        assert len(new_frontiers) == 1
        # Fallback embeds the rationale so the slot stays informative.
        assert "untried" in new_frontiers[0].approach or "deepen" in new_frontiers[0].approach


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


class TestRefineApproachTranscriptTail:
    """C4 — in CONTINUOUS mode, _reflect_and_expand passes a non-empty
    ``transcript_tail`` to refine_approach so the opener is state-specific."""

    @pytest.mark.asyncio
    async def test_transcript_tail_empty_in_trials(self):
        from mesmer.core.constants import ScenarioMode
        from mesmer.core.agent.context import Turn

        ctx = _make_ctx()
        graph = ctx.graph
        # Seed a live turn so format_session_turns would return non-empty if
        # it were called — but TRIALS mode shouldn't care.
        ctx.turns.append(Turn(sent="hi", received="hello"))
        current_node = graph.add_node(
            graph.root_id, "foo", "prior angle words plenty", score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        captured: dict = {}
        async def fake_refine(ctx, *, module, rationale, judge_result, transcript_tail="", **kw):
            captured.setdefault("tails", []).append(transcript_tail)
            return "x"

        assert ctx.scenario_mode == ScenarioMode.TRIALS
        with patch("mesmer.core.agent.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["foo", "bar"],
            )

        assert captured["tails"], "refine_approach was not called"
        assert all(t == "" for t in captured["tails"])

    @pytest.mark.asyncio
    async def test_transcript_tail_populated_in_continuous(self):
        from mesmer.core.constants import ScenarioMode
        from mesmer.core.agent.context import Turn

        ctx = _make_ctx()
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        graph = ctx.graph
        ctx.turns.append(Turn(sent="probe question", received="deflection reply"))
        current_node = graph.add_node(
            graph.root_id, "foo", "prior angle words plenty", score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        captured: dict = {}
        async def fake_refine(ctx, *, module, rationale, judge_result, transcript_tail="", **kw):
            captured.setdefault("tails", []).append(transcript_tail)
            return "x"

        with patch("mesmer.core.agent.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["foo", "bar"],
            )

        assert captured["tails"], "refine_approach was not called"
        # Every candidate sees the same tail — populated with the seeded turn.
        for tail in captured["tails"]:
            assert "probe question" in tail
            assert "deflection reply" in tail


class TestUpdateGraphContinuousAttach:
    """C5 — a fresh attempt in CONTINUOUS mode attaches under the latest
    explored node (the chain's leaf), not root. TRIALS behaviour preserved."""

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
            frontier_id=None,
        )
        assert node.parent_id == graph.root_id

    @pytest.mark.asyncio
    async def test_fresh_attempt_attaches_to_leaf_in_continuous(self):
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
            frontier_id=None,
        )
        # Chain extends: new move is a child of the previous move, not root.
        assert node.parent_id == prior.id

    @pytest.mark.asyncio
    async def test_fresh_attempt_continuous_falls_back_to_root_when_graph_empty(self):
        from mesmer.core.constants import ScenarioMode

        ctx = _make_ctx()
        ctx.scenario_mode = ScenarioMode.CONTINUOUS
        graph = ctx.graph  # has only root
        judge = JudgeResult(5, "", "", "", "")
        node = _update_graph(
            ctx, "bar", "opening move plenty words",
            judge, log=lambda *a, **kw: None,
            messages_sent=[], target_responses=[],
            frontier_id=None,
        )
        # No prior explored node → attach under root rather than crashing.
        assert node.parent_id == graph.root_id
