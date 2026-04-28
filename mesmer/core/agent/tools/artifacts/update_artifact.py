"""``update_artifact`` — patch durable Markdown artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.artifacts import (
    ArtifactError,
    ArtifactPatchMode,
    ArtifactUpdate,
    declared_artifact_ids,
)
from mesmer.core.constants import LogEvent, ToolName
from mesmer.core.patching import MarkdownPatchOperation

if TYPE_CHECKING:
    from mesmer.core.actor import ReactActorSpec
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


NAME = ToolName.UPDATE_ARTIFACT

_OP_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": [operation.value for operation in MarkdownPatchOperation],
        },
        "heading": {"type": "string"},
        "level": {"type": "integer"},
        "content": {"type": "string"},
        "match": {"type": "string"},
        "replacement": {"type": "string"},
        "all": {"type": "boolean"},
    },
    "required": ["op"],
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "Create or update a durable Markdown artifact. Use operations for "
            "small edits; use content only when intentionally replacing the "
            "whole artifact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "content": {
                    "type": "string",
                    "description": "Complete replacement Markdown content.",
                },
                "operations": {
                    "type": "array",
                    "items": _OP_SCHEMA,
                    "description": "Markdown patch operations.",
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
    declared_ids = declared_artifact_ids(ctx.artifact_specs)
    if declared_ids and artifact_id not in declared_ids:
        allowed = ", ".join(f"`{artifact_id}`" for artifact_id in sorted(declared_ids))
        return tool_result(
            call.id,
            "Artifact update rejected: this scenario declares an artifact "
            f"contract. Use one of: {allowed}",
        )
    has_content = "content" in args
    has_operations = "operations" in args
    if has_content == has_operations:
        return tool_result(call.id, "Artifact update rejected: provide exactly one of content or operations")
    update = ArtifactUpdate(
        artifact_id=artifact_id,
        mode=ArtifactPatchMode.REPLACE if has_content else ArtifactPatchMode.PATCH,
        content=args.get("content") if has_content else None,
        operations=args.get("operations") if has_operations else None,
    )
    try:
        result = ctx.artifacts.update(update)
    except ArtifactError as e:
        return tool_result(call.id, f"Artifact update rejected: {e}")

    if ctx.target_memory is not None:
        try:
            ctx.target_memory.save_artifacts(ctx.artifacts)
        except OSError as e:
            log(LogEvent.ARTIFACT_UPDATED.value, f"persist_failed: {e}")
            return tool_result(
                call.id,
                f"Artifact updated in memory but disk persist failed: {e}",
            )

    log(LogEvent.ARTIFACT_UPDATED.value, artifact_id)
    return tool_result(
        call.id,
        result.to_dict(),
    )


__all__ = ["NAME", "SCHEMA", "handle"]
