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
from mesmer.core.constants import ToolName

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
    ToolName.UPDATE_SCRATCHPAD: update_scratchpad.handle,
    ToolName.TALK_TO_OPERATOR: talk_to_operator.handle,
}


def build_tool_list(module: ModuleConfig, ctx: Context) -> list[dict]:
    """Assemble the OpenAI ``tools=`` list for a module + context.

    Two roles, two tool shapes (gated by ``module.is_executive``):

      - **Executive** (synthesized at run start, always at depth=0): owns
        the operator conversation. Gets ``ask_human``,
        ``talk_to_operator``, ``update_scratchpad``, and dispatch tools
        for every manager in ``module.sub_modules``. **Does NOT get
        ``send_message``** — the executive never talks to the target
        directly; that's a manager's job.
      - **Manager / employee** (registry-loaded, ``is_executive=False``):
        runs heads-down. Gets any sub-module dispatch tools. It also gets
        ``send_message`` unless the module opts out with
        ``parameters.allow_target_access: false``. Pure planning modules
        use that opt-out so the prompt does not have to fight an available
        target-I/O tool. Non-executives never get ``ask_human`` /
        ``talk_to_operator`` / ``update_scratchpad`` — only the executive
        talks to the operator.

    Order matters only for prompt compactness; the LLM sees all schemas
    at once. Sub-module tools come first because they're the meaningful
    attack surface.
    """
    tools: list[dict] = []
    if module.sub_modules:
        tools.extend(ctx.registry.as_tools(module.sub_module_names))
    if module.is_executive:
        tools.append(ask_human.SCHEMA)
        tools.append(talk_to_operator.SCHEMA)
        tools.append(update_scratchpad.SCHEMA)
    elif module.parameters.get("allow_target_access", True) is not False:
        tools.append(send_message.SCHEMA)
    tools.append(conclude.SCHEMA)
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
