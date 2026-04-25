"""``talk_to_operator`` — leader-only mid-run reply tool.

Sends a message back to the human operator who's watching the run via
the web UI. Use it to acknowledge a queued operator message, ask a
short clarifying question (when ``ask_human`` would be overkill), or
report a finding without waiting for the run to conclude.

The reply lands in the operator chat panel via the
``LogEvent.OPERATOR_REPLY`` event AND is persisted to ``chat.jsonl``
so it survives a page refresh.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.constants import LogEvent, ToolName

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn
    from mesmer.core.module import ModuleConfig


NAME = ToolName.TALK_TO_OPERATOR

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "Send a short message back to the human operator watching "
            "the run. Use to acknowledge a queued operator message, share "
            "an interim finding, or flag uncertainty. Does NOT pause the "
            "run — the operator may or may not be watching, and you keep "
            "iterating either way. For genuinely blocking questions in "
            "co-op mode, use ask_human instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message to send to the operator.",
                },
            },
            "required": ["text"],
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
    """Persist the reply to chat.jsonl and emit OPERATOR_REPLY."""
    text = (args.get("text") or "").strip()
    if not text:
        return tool_result(
            call.id,
            "talk_to_operator requires a non-empty 'text' argument."
        )
    ts = time.time()
    if ctx.target_memory is not None:
        try:
            ctx.target_memory.append_chat("assistant", text, ts)
        except OSError as e:
            log(LogEvent.OPERATOR_REPLY.value, f"persist_failed: {e}")
            # Continue — emitting the event still surfaces it in the live UI.
    log(LogEvent.OPERATOR_REPLY.value, text)
    return tool_result(
        call.id,
        "Sent to operator. Continue with your work — no pause."
    )


__all__ = ["NAME", "SCHEMA", "handle"]
