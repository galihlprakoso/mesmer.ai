"""The agent's built-in tools + dispatch.

Every file in this subpackage defines *one* tool: its OpenAI function
schema and — for tools with a runtime handler — the async function that
executes the call. Engine glue (tool-list assembly, name-to-handler
dispatch) lives here so the engine itself stays lean.

Why per-file?

  - Cohesion: the schema the LLM sees and the code that runs when it's
    called change together, so they live together.
  - Readability: the old ``handlers.py`` put three unrelated tools in one
    file, which meant any change to one tool's prompt hid in a 200-line
    blob next to the other two.
  - Extensibility: adding a tool is now one file + two lines in the
    dispatch table below, not a diff spread across three files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools import (
    ask_human,
    conclude,
    send_message,
    sub_module,
    talk_to_operator,
    update_scratchpad,
)
from mesmer.core.agent.tools.base import tool_result
from mesmer.core.actor import ReactActorSpec, ToolPolicySpec, ensure_actor
from mesmer.core.constants import ToolName

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn


# Static built-in handlers keyed by enum name. ``conclude`` is absent
# because it terminates the loop — the engine short-circuits on it
# rather than appending a tool_result. Sub-module tools are dynamic
# (their names come from the registry) and dispatched separately.
_BUILTIN_HANDLERS = {
    ToolName.SEND_MESSAGE: send_message.handle,
    ToolName.ASK_HUMAN: ask_human.handle,
    ToolName.UPDATE_SCRATCHPAD: update_scratchpad.handle,
    ToolName.TALK_TO_OPERATOR: talk_to_operator.handle,
}

_BUILTIN_SCHEMAS = {
    ToolName.SEND_MESSAGE.value: send_message.SCHEMA,
    ToolName.ASK_HUMAN.value: ask_human.SCHEMA,
    ToolName.UPDATE_SCRATCHPAD.value: update_scratchpad.SCHEMA,
    ToolName.TALK_TO_OPERATOR.value: talk_to_operator.SCHEMA,
    ToolName.CONCLUDE.value: conclude.SCHEMA,
}


def resolve_tool_policy(actor: ReactActorSpec) -> ToolPolicySpec:
    """Resolve the declarative tool policy for an actor."""

    actor = ensure_actor(actor)
    if actor.tool_policy is None:
        raise ValueError(f"Actor {actor.name!r} has no tool_policy")
    return actor.tool_policy


def build_tool_list(actor: ReactActorSpec, ctx: Context) -> list[dict]:
    """Materialize an actor's declarative tool policy into OpenAI schemas."""
    actor = ensure_actor(actor)
    policy = resolve_tool_policy(actor)
    tools: list[dict] = []

    if policy.dispatch_submodules and actor.sub_modules:
        tools.extend(ctx.registry.as_tools(actor.sub_module_names))

    for name in policy.builtin:
        schema = _BUILTIN_SCHEMAS.get(str(name))
        if schema is None:
            raise ValueError(f"Unknown built-in tool grant: {name!r}")
        tools.append(schema)

    if policy.external:
        raise ValueError(
            "External tool grants are declared but no external tool resolver is configured."
        )

    return tools


async def dispatch_tool_call(
    fn_name: str,
    ctx: Context,
    actor: ReactActorSpec,
    call,
    args: dict,
    instruction: str,
    log: LogFn,
) -> dict:
    """Route one tool call to its handler → tool_result dict.

    Does NOT handle ``conclude`` — the engine intercepts that upstream
    because it short-circuits the loop. Anything this function sees is
    expected to append to ``messages`` and let the loop continue.
    """
    try:
        name = ToolName(fn_name)
    except ValueError:
        name = None

    if name in _BUILTIN_HANDLERS:
        return await _BUILTIN_HANDLERS[name](ctx, actor, call, args, log)
    if fn_name in ctx.registry:
        return await sub_module.handle(
            ctx, actor, call, fn_name, args, instruction, log
        )
    return tool_result(call.id, f"Unknown tool: {fn_name}")


__all__ = [
    "build_tool_list",
    "dispatch_tool_call",
    "resolve_tool_policy",
    "tool_result",
]
