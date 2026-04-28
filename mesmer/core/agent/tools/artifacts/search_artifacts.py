"""``search_artifacts`` — grep-like search over Markdown artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.artifacts import declared_artifact_ids
from mesmer.core.constants import ToolName

if TYPE_CHECKING:
    from mesmer.core.actor import ReactActorSpec
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


NAME = ToolName.SEARCH_ARTIFACTS

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": "Search across durable Markdown artifacts and return section-level hits.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional artifact ids to restrict the search.",
                },
                "limit": {"type": "integer"},
            },
            "required": ["query"],
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
    artifact_ids = args.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        artifact_ids = None
    allowed = declared_artifact_ids(ctx.artifact_specs)
    if allowed:
        if artifact_ids is None:
            artifact_ids = sorted(allowed)
        else:
            requested = {str(item) for item in artifact_ids}
            unknown = sorted(requested - allowed)
            if unknown:
                allowed_text = ", ".join(f"`{item}`" for item in sorted(allowed))
                return tool_result(
                    call.id,
                    "Artifact search rejected: this scenario declares an "
                    f"artifact contract. Use one of: {allowed_text}",
                )
            artifact_ids = [item for item in artifact_ids if item in allowed]
    hits = ctx.artifacts.search(
        args.get("query") or "",
        artifact_ids=artifact_ids,
        limit=int(args.get("limit") or 8),
    )
    return tool_result(call.id, {"items": [hit.to_dict() for hit in hits]})


__all__ = ["NAME", "SCHEMA", "handle"]
