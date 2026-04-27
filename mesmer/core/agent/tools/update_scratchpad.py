"""``update_scratchpad`` — shared whiteboard tool.

The scratchpad is a single shared markdown whiteboard seeded from
``scratchpad.md``. This tool can either replace the whiteboard with full
markdown content or apply structured patch operations to the current content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.constants import LogEvent, ToolName
from mesmer.core.patching import MarkdownPatchError, apply_markdown_patch

if TYPE_CHECKING:
    from mesmer.core.actor import ReactActorSpec
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


NAME = ToolName.UPDATE_SCRATCHPAD

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "Update the shared markdown scratchpad for this target. "
            "Use `operations` for small edits such as adding evidence under "
            "a heading. Use `content` only when you intentionally want to "
            "replace the entire scratchpad."
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
                "operations": {
                    "type": "array",
                    "description": (
                        "Structured markdown patch operations. Provide either "
                        "`content` or `operations`, not both."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "append_section",
                                    "replace_section",
                                    "delete_section",
                                    "delete_matching_line",
                                    "replace_matching_line",
                                    "insert_after",
                                    "insert_before",
                                ],
                            },
                            "heading": {
                                "type": "string",
                                "description": "Markdown heading text for section operations.",
                            },
                            "content": {
                                "type": "string",
                                "description": "Markdown content to insert or write.",
                            },
                            "match": {
                                "type": "string",
                                "description": "Line substring to match for line operations.",
                            },
                            "replacement": {
                                "type": "string",
                                "description": "Replacement line for replace_matching_line.",
                            },
                            "all": {
                                "type": "boolean",
                                "description": "When true, apply line operation to all matching lines.",
                            },
                        },
                        "required": ["op"],
                    },
                },
            },
        },
    },
}


async def handle(
    ctx: Context,
    module: "ReactActorSpec",
    call,
    args: dict,
    log: LogFn,
) -> dict:
    """Update the shared scratchpad whiteboard and persist scratchpad.md."""
    has_content = "content" in args
    has_operations = "operations" in args
    if has_content == has_operations:
        return tool_result(
            call.id,
            "update_scratchpad requires exactly one of 'content' or 'operations'."
        )

    summaries: list[str] = []
    if has_content:
        content = args.get("content", "")
        if not isinstance(content, str):
            return tool_result(
                call.id,
                "update_scratchpad requires string 'content' when using full replacement."
            )
        summaries.append("replaced scratchpad")
    else:
        try:
            patch = apply_markdown_patch(
                ctx.scratchpad.content,
                args.get("operations"),
            )
        except MarkdownPatchError as e:
            return tool_result(call.id, f"Scratchpad patch rejected: {e}")
        content = patch.content
        summaries.extend(patch.summaries)

    ctx.scratchpad.update(content)
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
    summary = "; ".join(summaries)
    return tool_result(
        call.id,
        f"Scratchpad updated ({len(content)} chars; {summary}). Future iterations and "
        "the next run against this target will see the new content."
    )


__all__ = ["NAME", "SCHEMA", "handle"]
