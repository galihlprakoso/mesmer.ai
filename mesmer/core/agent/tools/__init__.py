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

from mesmer.core.agent.tools import ask_human, conclude, send_message, sub_module
from mesmer.core.agent.tools.base import tool_result
from mesmer.core.constants import ContextMode, ToolName

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn
    from mesmer.core.module import ModuleConfig


# Static built-in handlers keyed by enum name. ``conclude`` is absent
# because it terminates the loop — the engine short-circuits on it
# rather than appending a tool_result. Sub-module tools are dynamic
# (their names come from the registry) and dispatched separately.
_BUILTIN_HANDLERS = {
    ToolName.SEND_MESSAGE: send_message.handle,
    ToolName.ASK_HUMAN: ask_human.handle,
}


def build_tool_list(module: ModuleConfig, ctx: Context) -> list[dict]:
    """Assemble the OpenAI ``tools=`` list for a leader module + context.

    Order matters only for prompt compactness — the LLM sees all schemas
    at once. Sub-module tools come first because they're the meaningful
    attack surface; built-ins round out the toolbox.
    """
    tools: list[dict] = []
    if module.sub_modules:
        tools.extend(ctx.registry.as_tools(module.sub_module_names))
    tools.append(send_message.SCHEMA)
    tools.append(conclude.SCHEMA)
    if ctx.mode == ContextMode.CO_OP and ctx.human_broker is not None:
        tools.append(ask_human.SCHEMA)
    return tools


async def dispatch_tool_call(
    fn_name: str,
    ctx: Context,
    module: ModuleConfig,
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
        return await _BUILTIN_HANDLERS[name](ctx, module, call, args, log)
    if fn_name in ctx.registry:
        return await sub_module.handle(
            ctx, module, call, fn_name, args, instruction, log
        )
    return tool_result(call.id, f"Unknown tool: {fn_name}")


__all__ = [
    "build_tool_list",
    "dispatch_tool_call",
    "tool_result",
]
