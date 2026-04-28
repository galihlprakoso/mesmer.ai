"""``read_artifact`` — read a Markdown artifact or selected sections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.artifacts import declared_artifact_ids
from mesmer.core.constants import ToolName

if TYPE_CHECKING:
    from mesmer.core.actor import ReactActorSpec
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


NAME = ToolName.READ_ARTIFACT

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "Read one durable Markdown artifact. Optionally request specific "
            "section headings to avoid loading the whole document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional Markdown heading names to return.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return.",
                },
            },
            "required": ["artifact_id"],
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
    artifact_id = args.get("artifact_id") or ""
    allowed = declared_artifact_ids(ctx.artifact_specs)
    if allowed and artifact_id not in allowed:
        allowed_text = ", ".join(f"`{item}`" for item in sorted(allowed))
        return tool_result(
            call.id,
            "Artifact read rejected: this scenario declares an artifact "
            f"contract. Use one of: {allowed_text}",
        )
    declared = next((spec for spec in ctx.artifact_specs if spec.id == artifact_id), None)
    sections = args.get("sections") if isinstance(args.get("sections"), list) else None
    value = ctx.artifacts.read(artifact_id, sections=sections)
    max_chars = max(1, min(int(args.get("max_chars") or 8000), 50000))
    truncated = len(value) > max_chars
    if truncated:
        value = value[:max_chars].rstrip() + "\n\n[truncated]"
    return tool_result(
        call.id,
        {
            "artifact_id": artifact_id,
            "content": value,
            "truncated": truncated,
            "declared": declared is not None,
            "exists": bool(value.strip()),
            "title": declared.title if declared else "",
            "description": declared.description if declared else "",
        },
    )


__all__ = ["NAME", "SCHEMA", "handle"]
