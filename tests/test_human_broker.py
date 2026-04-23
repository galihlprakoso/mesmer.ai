"""Tests for HumanQuestionBroker and Context.ask_human (co-op mode)."""

import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest

from mesmer.core.agent.context import (
    Context,
    HumanQuestionBroker,
    HumanQuestionTimeout,
)
from mesmer.core.scenario import AgentConfig


# ---------------------------------------------------------------------------
# HumanQuestionBroker
# ---------------------------------------------------------------------------

class TestBroker:
    @pytest.mark.asyncio
    async def test_create_and_answer(self):
        broker = HumanQuestionBroker()
        qid = broker.create_question("What next?")
        assert qid
        # Answer in a background task
        async def _answer():
            await asyncio.sleep(0.01)
            broker.answer(qid, "try foot-in-door")
        asyncio.create_task(_answer())
        result = await broker.wait_for_answer(qid, timeout=2.0)
        assert result == "try foot-in-door"

    @pytest.mark.asyncio
    async def test_on_question_hook_fires(self):
        captured = []
        broker = HumanQuestionBroker(on_question=lambda q: captured.append(q))
        broker.create_question("Hello?", options=["yes", "no"], module="probe")
        assert len(captured) == 1
        assert captured[0]["question"] == "Hello?"
        assert captured[0]["options"] == ["yes", "no"]
        assert captured[0]["module"] == "probe"

    @pytest.mark.asyncio
    async def test_timeout(self):
        broker = HumanQuestionBroker()
        qid = broker.create_question("No one will answer")
        with pytest.raises(HumanQuestionTimeout):
            await broker.wait_for_answer(qid, timeout=0.05)
        # Question is cleaned up after timeout
        assert broker.pending_count == 0

    @pytest.mark.asyncio
    async def test_answer_unknown_id_returns_false(self):
        broker = HumanQuestionBroker()
        assert broker.answer("no-such-id", "hi") is False

    @pytest.mark.asyncio
    async def test_cancel_all_aborts_pending(self):
        broker = HumanQuestionBroker()
        qid = broker.create_question("first")
        broker.create_question("second")
        assert broker.pending_count == 2

        async def _wait():
            return await broker.wait_for_answer(qid, timeout=5.0)

        task = asyncio.create_task(_wait())
        await asyncio.sleep(0.01)
        broker.cancel_all("run stopped")
        with pytest.raises(HumanQuestionTimeout):
            await task
        assert broker.pending_count == 0


# ---------------------------------------------------------------------------
# Context.ask_human
# ---------------------------------------------------------------------------

def _make_ctx(mode="autonomous", broker=None):
    return Context(
        target=MagicMock(send=AsyncMock(return_value="reply")),
        registry=MagicMock(),
        agent_config=AgentConfig(model="test/model"),
        mode=mode,
        human_broker=broker,
    )


class TestContextAskHuman:
    @pytest.mark.asyncio
    async def test_autonomous_returns_empty_no_broker(self):
        ctx = _make_ctx(mode="autonomous")
        result = await ctx.ask_human("Should I continue?")
        assert result == ""

    @pytest.mark.asyncio
    async def test_autonomous_with_broker_still_skips(self):
        """Even if a broker exists, ask_human in autonomous mode is a no-op."""
        broker = HumanQuestionBroker()
        ctx = _make_ctx(mode="autonomous", broker=broker)
        result = await ctx.ask_human("Hi?")
        assert result == ""
        assert broker.pending_count == 0

    @pytest.mark.asyncio
    async def test_coop_mode_awaits_answer(self):
        broker = HumanQuestionBroker()
        ctx = _make_ctx(mode="co-op", broker=broker)

        async def _ask():
            return await ctx.ask_human("What next?", timeout=2.0)

        task = asyncio.create_task(_ask())
        await asyncio.sleep(0.01)
        # The broker should have exactly one pending question
        assert broker.pending_count == 1
        qid = list(broker._pending.keys())[0]
        broker.answer(qid, "try anchoring")
        result = await task
        assert result == "try anchoring"

    @pytest.mark.asyncio
    async def test_coop_timeout_returns_fallback(self):
        broker = HumanQuestionBroker()
        ctx = _make_ctx(mode="co-op", broker=broker)
        result = await ctx.ask_human("?", timeout=0.05)
        # Context swallows timeout and returns a fallback string so the loop
        # keeps going instead of crashing.
        assert "no response" in result.lower()

    @pytest.mark.asyncio
    async def test_child_context_inherits_mode_and_broker(self):
        broker = HumanQuestionBroker()
        parent = _make_ctx(mode="co-op", broker=broker)
        child = parent.child()
        assert child.mode == "co-op"
        assert child.human_broker is broker
