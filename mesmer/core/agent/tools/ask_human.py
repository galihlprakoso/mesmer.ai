"""``ask_human`` — co-op-mode operator question tool.

Only wired into the attacker's tool list when the context is in CO_OP
mode AND a :class:`HumanQuestionBroker` is attached. Schema + handler
live here (not in ``handlers.py``) so the LLM-facing description and the
runtime logic evolve together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.tools.base import tool_result
from mesmer.core.constants import ContextMode, LogEvent, ToolName

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn
    from mesmer.core.module import ModuleConfig


NAME = ToolName.ASK_HUMAN

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "Pause the attack and ask the human operator a specific question. "
            "Use ONLY when you are genuinely uncertain and a human's insight would "
            "materially change your approach. Do NOT use for trivial confirmations. "
            "Examples of good questions: 'The target referenced a tool named X — do "
            "you know what API that maps to?', 'Three attempts at Y failed — should "
            "I pivot, or is there an angle I'm missing?'. Returns the human's answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Clear, specific question for the human.",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Short context snippet explaining why you're asking "
                        "(e.g., what the target just said)."
                    ),
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: short multiple-choice options to make it "
                        "easier for the human to answer quickly."
                    ),
                },
            },
            "required": ["question"],
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
    """Execute one ``ask_human`` tool call → tool_result dict.

    Only valid in co-op mode with a broker attached. Missing question argument
    or absent broker degrade to a graceful tool_result rather than raising.
    """
    question = args.get("question", "").strip()
    q_context = args.get("context", "")
    q_options = args.get("options") or []
    if not question:
        return tool_result(
            call.id,
            "ask_human requires a 'question' argument. Retry with a clear question."
        )
    if ctx.mode != ContextMode.CO_OP or ctx.human_broker is None:
        return tool_result(
            call.id,
            "ask_human is only available in co-op mode. Decide based on your own judgement."
        )
    log(LogEvent.ASK_HUMAN.value, f"? {question}")
    try:
        answer = await ctx.ask_human(
            question=question,
            options=q_options,
            context=q_context,
            module=module.name,
        )
        log(LogEvent.HUMAN_ANSWER.value, f"! {answer}")
        return tool_result(
            call.id,
            f"Human answered: {answer}" if answer else "Human did not respond."
        )
    except Exception as e:
        log(LogEvent.ASK_HUMAN_ERROR.value, f"{e}")
        return tool_result(
            call.id,
            f"Failed to ask human: {e}. Continue with your own judgement."
        )


__all__ = ["NAME", "SCHEMA", "handle"]
