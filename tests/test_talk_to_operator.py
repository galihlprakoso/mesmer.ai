"""Tests for the leader-only talk_to_operator tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mesmer.core.agent.tools import build_tool_list, talk_to_operator
from mesmer.core.actor import ActorRole, ReactActorSpec, ToolPolicySpec
from mesmer.core.constants import LogEvent, ToolName


class _Call:
    def __init__(self, call_id="call_2"):
        self.id = call_id


def _make_ctx(*, depth=0, target_memory=None):
    ctx = MagicMock()
    ctx.depth = depth
    ctx.target_memory = target_memory
    ctx.human_broker = None
    ctx.registry = MagicMock()
    return ctx


def _make_actor(name="my-leader", *, role=ActorRole.MODULE):
    builtin = [ToolName.CONCLUDE.value]
    if role is ActorRole.EXECUTIVE:
        builtin = [
            ToolName.ASK_HUMAN.value,
            ToolName.TALK_TO_OPERATOR.value,
            ToolName.LIST_ARTIFACTS.value,
            ToolName.READ_ARTIFACT.value,
            ToolName.SEARCH_ARTIFACTS.value,
            ToolName.UPDATE_ARTIFACT.value,
            ToolName.CONCLUDE.value,
        ]
    return ReactActorSpec(
        name=name,
        role=role,
        tool_policy=ToolPolicySpec(builtin=builtin),
    )


@pytest.mark.asyncio
async def test_persists_to_chat_and_emits_event():
    appended = []

    class StubMemory:
        def append_chat(self, role, content, ts):
            appended.append((role, content))

    ctx = _make_ctx(target_memory=StubMemory())
    log = MagicMock()

    result = await talk_to_operator.handle(
        ctx, _make_actor(), _Call(), {"text": "I'm pivoting to authority framing."}, log,
    )

    assert appended == [("assistant", "I'm pivoting to authority framing.")]
    log.assert_called_with(LogEvent.OPERATOR_REPLY.value, "I'm pivoting to authority framing.")
    assert "Sent to operator" in result["content"]


@pytest.mark.asyncio
async def test_rejects_empty_text():
    ctx = _make_ctx()
    log = MagicMock()

    result = await talk_to_operator.handle(ctx, _make_actor(), _Call(), {"text": ""}, log)
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
        ctx, _make_actor(), _Call(), {"text": "still goes through live UI"}, log,
    )

    # Event was emitted despite disk failure (live UI still gets it).
    log.assert_called_with(LogEvent.OPERATOR_REPLY.value, "still goes through live UI")
    assert "Sent to operator" in result["content"]


@pytest.mark.asyncio
async def test_works_without_target_memory():
    ctx = _make_ctx(target_memory=None)
    log = MagicMock()

    result = await talk_to_operator.handle(
        ctx, _make_actor(), _Call(), {"text": "hi op"}, log,
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
        _make_actor("scenario:executive", role=ActorRole.EXECUTIVE), exec_ctx
    )
    manager_tools = build_tool_list(
        _make_actor("system-prompt-extraction", role=ActorRole.MODULE), manager_ctx
    )

    exec_names = [t["function"]["name"] for t in exec_tools]
    manager_names = [t["function"]["name"] for t in manager_tools]

    assert ToolName.TALK_TO_OPERATOR.value in exec_names
    assert ToolName.TALK_TO_OPERATOR.value not in manager_names
