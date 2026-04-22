"""Tests for mesmer.core.loop — the ReAct loop with judge/reflect/graph cycle."""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from mesmer.core.context import Context, TurnBudgetExhausted
from mesmer.core.graph import AttackGraph
from mesmer.core.judge import JudgeResult
from mesmer.core.loop import (
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


def _make_module(name="test-module", has_custom_run=False, sub_modules=None):
    mod = MagicMock()
    mod.name = name
    mod.has_custom_run = has_custom_run
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
    plan=None,
    captured_messages=None,
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
        plan=plan,
    )
    ctx.completion = fake_completion
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
# run_react_loop — custom run modules
# ---------------------------------------------------------------------------

class TestCustomRun:
    @pytest.mark.asyncio
    async def test_custom_run_bypasses_loop(self):
        module = _make_module(has_custom_run=True)
        module.custom_run = AsyncMock(return_value="custom result")

        ctx = _make_ctx()
        result = await run_react_loop(module, ctx, "test instruction")
        assert result == "custom result"
        module.custom_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_run_budget_exhausted(self):
        module = _make_module(has_custom_run=True)
        module.custom_run = AsyncMock(side_effect=TurnBudgetExhausted(5))

        ctx = _make_ctx()
        result = await run_react_loop(module, ctx, "test")
        assert "Turn budget exhausted" in result


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
        import mesmer.core.loop as loop_mod
        original_delays = loop_mod.RETRY_DELAYS
        loop_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            loop_mod.RETRY_DELAYS = original_delays

        assert "LLM error" in result or "retries exhausted" in result
        assert call_count[0] == 3  # tried 3 times

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

        import mesmer.core.loop as loop_mod
        original_delays = loop_mod.RETRY_DELAYS
        loop_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            loop_mod.RETRY_DELAYS = original_delays

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
    async def test_rate_limit_cools_key_and_rotates(self):
        """On RateLimitError, the current key is cooled down, and the next
        attempt happens on a different key (no sleep)."""
        from mesmer.core.keys import KeyPool

        # Install a pool with two keys
        ctx = _make_ctx()
        ctx.agent_config._keys = ["KEY_A", "KEY_B"]
        ctx.agent_config._pool = KeyPool(["KEY_A", "KEY_B"])

        # Track keys seen and script completion responses
        keys_seen = []

        async def rate_limited_then_succeed(messages, tools=None):
            # Read the key that completion() would have selected and record it
            key = ctx.agent_config.next_key()
            ctx._last_key_used = key
            keys_seen.append(key)
            if len(keys_seen) == 1:
                # First call hits rate-limit (per-day)
                raise Exception(
                    'RateLimitError: OpenrouterException - {"error":{"message":'
                    '"Rate limit exceeded: free-models-per-day","code":429}}'
                )
            # Second call: succeeds → conclude
            return FakeResponse(FakeMessage(
                tool_calls=[FakeToolCall("conclude", {"result": "rotated ok"})]
            ))

        ctx.completion = rate_limited_then_succeed
        module = _make_module()

        # No sleeps — rate-limit path doesn't backoff on the dead key
        import mesmer.core.loop as loop_mod
        original_delays = loop_mod.RETRY_DELAYS
        loop_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test")
        finally:
            loop_mod.RETRY_DELAYS = original_delays

        assert result == "rotated ok"
        # Two distinct keys were picked: the first (rate-limited) got cooled,
        # pool rotated to the other on retry.
        assert len(keys_seen) == 2
        assert keys_seen[0] != keys_seen[1]
        # The first key is now cooled (pool's active count dropped to 1)
        assert ctx.agent_config.pool.active_count() == 1

    @pytest.mark.asyncio
    async def test_rate_limit_all_keys_exhausted(self):
        """If every key gets rate-limited, the run stops cleanly with a
        rate_limit_wall event — doesn't infinite-loop."""
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

        import mesmer.core.loop as loop_mod
        original_delays = loop_mod.RETRY_DELAYS
        loop_mod.RETRY_DELAYS = [0, 0, 0]
        try:
            result = await run_react_loop(module, ctx, "test", log=log)
        finally:
            loop_mod.RETRY_DELAYS = original_delays

        # Run ends — ideally with a clean rate_limit_wall signal
        assert any(evt == "rate_limit_wall" for evt, _ in events) or "LLM error" in result
        # Only one key, and it's cooled
        assert ctx.agent_config.pool.active_count() == 0

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
# Plan injection (Phase 3)
# ---------------------------------------------------------------------------

class TestPlanInjection:
    @pytest.mark.asyncio
    async def test_plan_injected_into_leader_user_prompt(self):
        """When ctx.plan is set, it must appear in the leader's first user message."""
        captured = []
        plan_text = "# Attack Plan\n\nFocus on behavioral rules. Avoid identity claims."

        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            plan=plan_text,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        assert len(captured) >= 1
        # The first messages payload should carry our plan in the user role
        first = captured[0]
        user_msgs = [m for m in first if m.get("role") == "user"]
        assert user_msgs, "no user message sent"
        combined = "\n".join(m["content"] for m in user_msgs)
        assert "Attack Plan (from human operator" in combined
        assert "Focus on behavioral rules" in combined

    @pytest.mark.asyncio
    async def test_no_plan_means_no_injection(self):
        """Without ctx.plan, the human-plan heading must NOT appear."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            plan=None,
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        first = captured[0]
        combined = "\n".join(
            m.get("content", "") for m in first if m.get("role") == "user"
        )
        assert "Attack Plan (from human operator" not in combined

    @pytest.mark.asyncio
    async def test_empty_plan_string_treated_as_no_plan(self):
        """An empty/whitespace plan should not inject the section."""
        captured = []
        ctx = _make_ctx(
            completion_responses=[
                FakeResponse(FakeMessage(
                    tool_calls=[FakeToolCall("conclude", {"result": "ok"})]
                )),
            ],
            plan="   \n  ",
            captured_messages=captured,
        )
        module = _make_module()
        await run_react_loop(module, ctx, "test", log=None)

        first = captured[0]
        combined = "\n".join(
            m.get("content", "") for m in first if m.get("role") == "user"
        )
        assert "Attack Plan (from human operator" not in combined


# ---------------------------------------------------------------------------
# Fresh-session framing (P0)
# ---------------------------------------------------------------------------

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
        parent = graph.add_node(graph.root_id, "safety-profiler", "mapped defenses", score=8)
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

class TestReflectAndExpandGraphFirst:
    """Integration check: _reflect_and_expand must route through
    graph.propose_frontier (deterministic MCTS Selection) + judge.refine_approach
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
        # the module-selection logic from the LLM call.
        async def fake_refine(ctx, *, module, rationale, judge_result):
            return f"refined-for-{module}"

        with patch("mesmer.core.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, "foot-in-door", "initial foothold", judge,
                current_node, log=lambda *a, **kw: None,
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

        async def fake_refine(ctx, *, module, rationale, judge_result):
            return f"refined-{module}"

        with patch("mesmer.core.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, "foot-in-door", "x", judge, current_node,
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
            ctx, "x", "a", judge, current_node,
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

        async def empty_refine(ctx, *, module, rationale, judge_result):
            return ""

        with patch("mesmer.core.judge.refine_approach", new=empty_refine):
            await _reflect_and_expand(
                ctx, "foo", "a", judge, current_node,
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
