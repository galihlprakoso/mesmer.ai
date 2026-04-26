"""Tests for the leader-only talk_to_operator tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mesmer.core.agent.tools import build_tool_list, talk_to_operator
from mesmer.core.constants import LogEvent, ToolName


class _Call:
    def __init__(self, call_id="call_2"):
        self.id = call_id


def _make_ctx(*, depth=0, target_memory=None):
    from mesmer.core.scratchpad import Scratchpad
    ctx = MagicMock()
    ctx.depth = depth
    ctx.scratchpad = Scratchpad()
    ctx.target_memory = target_memory
    ctx.human_broker = None
    ctx.registry = MagicMock()
    return ctx


def _make_module(name="my-leader", *, is_executive=False):
    mod = MagicMock()
    mod.name = name
    mod.sub_modules = []
    mod.is_executive = is_executive
    return mod


@pytest.mark.asyncio
async def test_persists_to_chat_and_emits_event():
    appended = []

    class StubMemory:
        def append_chat(self, role, content, ts):
            appended.append((role, content))

    ctx = _make_ctx(target_memory=StubMemory())
    log = MagicMock()

    result = await talk_to_operator.handle(
        ctx, _make_module(), _Call(), {"text": "I'm pivoting to authority framing."}, log,
    )

    assert appended == [("assistant", "I'm pivoting to authority framing.")]
    log.assert_called_with(LogEvent.OPERATOR_REPLY.value, "I'm pivoting to authority framing.")
    assert "Sent to operator" in result["content"]


@pytest.mark.asyncio
async def test_rejects_empty_text():
    ctx = _make_ctx()
    log = MagicMock()

    result = await talk_to_operator.handle(ctx, _make_module(), _Call(), {"text": ""}, log)
    assert "non-empty 'text'" in result["content"]
    log.assert_not_called()


@pytest.mark.asyncio
async def test_disk_failure_still_emits_event():
    class FailingMemory:
        def append_chat(self, role, content, ts):
            raise OSError("disk full")

    ctx = _make_ctx(target_memory=FailingMemory())
    log = MagicMock()

    result = await talk_to_operator.handle(
        ctx, _make_module(), _Call(), {"text": "still goes through live UI"}, log,
    )

    # Event was emitted despite disk failure (live UI still gets it).
    log.assert_called_with(LogEvent.OPERATOR_REPLY.value, "still goes through live UI")
    assert "Sent to operator" in result["content"]


@pytest.mark.asyncio
async def test_works_without_target_memory():
    ctx = _make_ctx(target_memory=None)
    log = MagicMock()

    result = await talk_to_operator.handle(
        ctx, _make_module(), _Call(), {"text": "hi op"}, log,
    )

    log.assert_called_with(LogEvent.OPERATOR_REPLY.value, "hi op")
    assert "Sent to operator" in result["content"]


def test_executive_only_in_build_tool_list():
    """SCHEMA appears only for the synthesized executive, not for managers."""
    exec_ctx = _make_ctx(depth=0)
    exec_ctx.registry.as_tools = MagicMock(return_value=[])
    manager_ctx = _make_ctx(depth=1)
    manager_ctx.registry.as_tools = MagicMock(return_value=[])

    exec_tools = build_tool_list(
        _make_module("scenario:executive", is_executive=True), exec_ctx
    )
    manager_tools = build_tool_list(
        _make_module("system-prompt-extraction", is_executive=False), manager_ctx
    )

    exec_names = [t["function"]["name"] for t in exec_tools]
    manager_names = [t["function"]["name"] for t in manager_tools]

    assert ToolName.TALK_TO_OPERATOR.value in exec_names
    assert ToolName.TALK_TO_OPERATOR.value not in manager_names
