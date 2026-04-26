"""Tests for the leader-only update_scratchpad tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mesmer.core.agent.tools import update_scratchpad
from mesmer.core.agent.tools import build_tool_list
from mesmer.core.agent.tools.base import tool_result  # noqa: F401  (sanity import)
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


def _make_module(name="my-leader", *, is_executive=False):
    mod = MagicMock()
    mod.name = name
    mod.sub_modules = []
    mod.is_executive = is_executive
    return mod


@pytest.mark.asyncio
async def test_writes_to_scratchpad_slot_and_disk(tmp_path):
    """Tool writes both ctx.scratchpad[module.name] AND scratchpad.md."""
    persisted = []

    class StubMemory:
        def save_scratchpad(self, content):
            persisted.append(content)

    ctx = _make_ctx(target_memory=StubMemory())
    module = _make_module(name="my-leader")
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx, module, _Call(), {"content": "Lessons:\n- avoid identity claims"}, log,
    )

    assert ctx.scratchpad.get("my-leader") == "Lessons:\n- avoid identity claims"
    assert persisted == ["Lessons:\n- avoid identity claims"]
    # tool_result returns a dict with the call id and content text.
    assert result["tool_call_id"] == "call_1"
    assert "Scratchpad updated" in result["content"]


@pytest.mark.asyncio
async def test_works_without_target_memory():
    """Direct invocations / tests with no memory still update the slot."""
    ctx = _make_ctx(target_memory=None)
    module = _make_module()
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx, module, _Call(), {"content": "in-memory only"}, log,
    )

    assert ctx.scratchpad.get(module.name) == "in-memory only"
    assert "Scratchpad updated" in result["content"]


@pytest.mark.asyncio
async def test_empty_string_clears_the_slot():
    """Empty content is a valid clear (matches the SCHEMA description)."""
    ctx = _make_ctx()
    module = _make_module()
    ctx.scratchpad.set(module.name, "previous content")
    log = MagicMock()

    result = await update_scratchpad.handle(ctx, module, _Call(), {"content": ""}, log)
    assert "Scratchpad updated" in result["content"]
    assert ctx.scratchpad.get(module.name) == ""


@pytest.mark.asyncio
async def test_rejects_non_string_content():
    """Wrong type (int, list, etc.) is rejected — would corrupt the slot."""
    ctx = _make_ctx()
    module = _make_module()
    log = MagicMock()

    for bad in (123, ["a", "b"], None):
        result = await update_scratchpad.handle(ctx, module, _Call(), {"content": bad}, log)
        assert "requires a string 'content'" in result["content"]
    assert ctx.scratchpad.get(module.name) == ""


@pytest.mark.asyncio
async def test_disk_failure_preserves_in_memory_write():
    class FailingMemory:
        def save_scratchpad(self, content):
            raise OSError("disk full")

    ctx = _make_ctx(target_memory=FailingMemory())
    module = _make_module()
    log = MagicMock()

    result = await update_scratchpad.handle(
        ctx, module, _Call(), {"content": "won't persist"}, log,
    )

    # In-memory write succeeded even though disk failed.
    assert ctx.scratchpad.get(module.name) == "won't persist"
    assert "disk persist failed" in result["content"]


def test_executive_only_in_build_tool_list():
    """SCHEMA appears only for the synthesized executive, not for managers."""
    from mesmer.core.agent.tools import update_scratchpad as us
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

    assert ToolName.UPDATE_SCRATCHPAD.value in exec_names
    assert us.SCHEMA in exec_tools
    assert ToolName.UPDATE_SCRATCHPAD.value not in manager_names
