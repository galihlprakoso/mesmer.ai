"""``list_artifacts`` — list durable Markdown artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.artifacts import artifact_list_items
from mesmer.core.constants import ToolName

if TYPE_CHECKING:
    from mesmer.core.actor import ReactActorSpec
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


NAME = ToolName.LIST_ARTIFACTS

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": "List durable Markdown artifacts available for this target.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of artifacts to return.",
                },
            },
        },
    },
}


async def handle(
    ctx: "Context",
    module: "ReactActorSpec",
    call,
    args: dict,
    log: "LogFn",
) -> dict:
    limit = max(1, min(int(args.get("limit") or 50), 100))
    items = [
        item.to_dict()
        for item in artifact_list_items(ctx.artifacts, ctx.artifact_specs)[:limit]
    ]
    return tool_result(call.id, {"items": items})


__all__ = ["NAME", "SCHEMA", "handle"]
