"""Tests for the shared update_scratchpad tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mesmer.core.agent.tools import update_scratchpad
from mesmer.core.agent.tools import build_tool_list
from mesmer.core.agent.tools.base import tool_result  # noqa: F401  (sanity import)
from mesmer.core.actor import ActorRole, ReactActorSpec, ToolPolicySpec
from mesmer.core.constants import ToolName


class _Call:
    def __init__(self, call_id="call_1"):
        self.id = call_id


def _make_ctx(*, depth=0, target_memory=None):
    """Minimal context double — only the fields update_scratchpad reads."""
    from mesmer.core.scratchpad import Scratchpad
    ctx = MagicMock()
    ctx.depth = depth
    ctx.scratchpad = Scratchpad()
    ctx.target_memory = target_memory
    ctx.human_broker = None
    ctx.registry = MagicMock()
    return ctx


def _make_actor(name="my-leader", *, role=ActorRole.MODULE):
    builtin = [ToolName.UPDATE_SCRATCHPAD.value, ToolName.CONCLUDE.value]
    if role is ActorRole.EXECUTIVE:
        builtin = [
            ToolName.ASK_HUMAN.value,
            ToolName.TALK_TO_OPERATOR.value,
            ToolName.UPDATE_SCRATCHPAD.value,
            ToolName.CONCLUDE.value,
        ]
    return ReactActorSpec(
        name=name,
        role=role,
        tool_policy=ToolPolicySpec(builtin=builtin),
    )


@pytest.mark.asyncio
async def test_replaces_scratchpad_and_persists(tmp_path):
    """Full content mode rewrites the shared whiteboard and scratchpad.md."""
    persisted = []

    class StubMemory:
        def save_scratchpad(self, content):
            persisted.append(content)

    ctx = _make_ctx(target_memory=StubMemory())
    module = _make_actor(name="my-leader")
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx, module, _Call(), {"content": "Lessons:\n- avoid identity claims"}, log,
    )

    assert ctx.scratchpad.content == "Lessons:\n- avoid identity claims"
    assert persisted == ["Lessons:\n- avoid identity claims"]
    # tool_result returns a dict with the call id and content text.
    assert result["tool_call_id"] == "call_1"
    assert "Scratchpad updated" in result["content"]


@pytest.mark.asyncio
async def test_works_without_target_memory():
    """Direct invocations / tests with no memory still update the whiteboard."""
    ctx = _make_ctx(target_memory=None)
    module = _make_actor()
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx, module, _Call(), {"content": "in-memory only"}, log,
    )

    assert ctx.scratchpad.content == "in-memory only"
    assert "Scratchpad updated" in result["content"]


@pytest.mark.asyncio
async def test_empty_string_clears_the_whiteboard():
    """Empty content is a valid clear (matches the SCHEMA description)."""
    ctx = _make_ctx()
    module = _make_actor()
    ctx.scratchpad.update("previous content")
    log = MagicMock()

    result = await update_scratchpad.handle(ctx, module, _Call(), {"content": ""}, log)
    assert "Scratchpad updated" in result["content"]
    assert ctx.scratchpad.content == ""


@pytest.mark.asyncio
async def test_rejects_non_string_content():
    """Wrong type (int, list, etc.) is rejected."""
    ctx = _make_ctx()
    module = _make_actor()
    log = MagicMock()

    for bad in (123, ["a", "b"], None):
        result = await update_scratchpad.handle(ctx, module, _Call(), {"content": bad}, log)
        assert "requires string 'content'" in result["content"]
    assert ctx.scratchpad.content == ""


@pytest.mark.asyncio
async def test_rejects_missing_or_ambiguous_mode():
    ctx = _make_ctx()
    module = _make_actor()
    log = MagicMock()

    missing = await update_scratchpad.handle(ctx, module, _Call(), {}, log)
    both = await update_scratchpad.handle(
        ctx,
        module,
        _Call("call_2"),
        {"content": "x", "operations": []},
        log,
    )

    assert "exactly one" in missing["content"]
    assert "exactly one" in both["content"]


@pytest.mark.asyncio
async def test_patch_operations_update_existing_whiteboard_and_persist():
    persisted = []

    class StubMemory:
        def save_scratchpad(self, content):
            persisted.append(content)

    ctx = _make_ctx(target_memory=StubMemory())
    ctx.scratchpad.update("## Evidence\n- target can search\n")
    module = _make_actor()
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx,
        module,
        _Call(),
        {
            "operations": [
                {
                    "op": "append_section",
                    "heading": "Evidence",
                    "content": "- target acknowledged email capability",
                },
                {
                    "op": "append_section",
                    "heading": "Next Step",
                    "content": "Run email-exfiltration-proof.",
                },
            ]
        },
        log,
    )

    assert "appended to section" in result["content"]
    assert "created section" in result["content"]
    assert "- target can search" in ctx.scratchpad.content
    assert "- target acknowledged email capability" in ctx.scratchpad.content
    assert "## Next Step" in ctx.scratchpad.content
    assert persisted == [ctx.scratchpad.content]


@pytest.mark.asyncio
async def test_patch_rejection_preserves_existing_whiteboard():
    ctx = _make_ctx()
    ctx.scratchpad.update("## Evidence\n- keep me\n")
    module = _make_actor()
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx,
        module,
        _Call(),
        {"operations": [{"op": "delete_section", "heading": "Missing"}]},
        log,
    )

    assert "patch rejected" in result["content"]
    assert ctx.scratchpad.content == "## Evidence\n- keep me\n"


@pytest.mark.asyncio
async def test_disk_failure_preserves_in_memory_write():
    class FailingMemory:
        def save_scratchpad(self, content):
            raise OSError("disk full")

    ctx = _make_ctx(target_memory=FailingMemory())
    module = _make_actor()
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx, module, _Call(), {"content": "won't persist"}, log,
    )

    # In-memory write succeeded even though disk failed.
    assert ctx.scratchpad.content == "won't persist"
    assert "disk persist failed" in result["content"]


def test_build_tool_list_exposes_update_scratchpad_when_policy_grants_it():
    """SCHEMA exposure is policy-driven."""
    from mesmer.core.agent.tools import update_scratchpad as us
    exec_ctx = _make_ctx(depth=0)
    exec_ctx.registry.as_tools = MagicMock(return_value=[])
    manager_ctx = _make_ctx(depth=1)
    manager_ctx.registry.as_tools = MagicMock(return_value=[])

    exec_tools = build_tool_list(
        _make_actor("scenario:executive", role=ActorRole.EXECUTIVE), exec_ctx
    )
    manager_tools = build_tool_list(_make_actor("system-prompt-extraction"), manager_ctx)

    exec_names = [t["function"]["name"] for t in exec_tools]
    manager_names = [t["function"]["name"] for t in manager_tools]

    assert ToolName.UPDATE_SCRATCHPAD.value in exec_names
    assert us.SCHEMA in exec_tools
    assert ToolName.UPDATE_SCRATCHPAD.value in manager_names
