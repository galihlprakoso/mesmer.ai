"""``update_scratchpad`` — leader-only persistent-notes tool.

The leader runs every iteration with its slot of the scratchpad
(``ctx.scratchpad[module.name]``) seeded from
``~/.mesmer/targets/{hash}/scratchpad.md`` at run start. This tool lets
the leader rewrite that slot AND persist to disk so the next run starts
with whatever the leader learned.

Sub-modules don't get this tool — their slots are auto-written by the
framework after each ``conclude()`` and intentionally ephemeral. Only
the leader's working notes survive across runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.constants import LogEvent, ToolName

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn
    from mesmer.core.module import ModuleConfig


NAME = ToolName.UPDATE_SCRATCHPAD

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "Rewrite your persistent working notes for this target. "
            "Whatever you pass becomes the new scratchpad — both in your "
            "current iteration's prompt AND on disk for the next run. "
            "Use this to commit hard-won lessons, refusal patterns, "
            "winning angles, or anything you'd want a future run against "
            "this target to know. Overwrites the previous scratchpad — "
            "include the parts you want to keep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The full new scratchpad content (markdown). "
                        "Pass empty string to clear."
                    ),
                },
            },
            "required": ["content"],
        },
    },
}


async def handle(
    ctx: Context,
    module: ModuleConfig,
    call,
    args: dict,
    log: LogFn,
) -> dict:
    """Update ``ctx.scratchpad[module.name]`` and persist scratchpad.md."""
    content = args.get("content", "")
    if not isinstance(content, str):
        return tool_result(
            call.id,
            "update_scratchpad requires a string 'content' argument."
        )
    ctx.scratchpad.set(module.name, content)
    if ctx.target_memory is not None:
        try:
            ctx.target_memory.save_scratchpad(content)
        except OSError as e:
            log(LogEvent.SCRATCHPAD_UPDATED.value, f"persist_failed: {e}")
            return tool_result(
                call.id,
                f"Scratchpad updated in memory but disk persist failed: {e}"
            )
    log(LogEvent.SCRATCHPAD_UPDATED.value, f"{len(content)} chars")
    return tool_result(
        call.id,
        f"Scratchpad updated ({len(content)} chars). Future iterations and "
        "the next run against this target will see the new content."
    )


__all__ = ["NAME", "SCHEMA", "handle"]
