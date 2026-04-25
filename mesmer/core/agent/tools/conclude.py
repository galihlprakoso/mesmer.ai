"""``conclude`` — loop-termination tool.

No ``handle()`` function here: ``conclude`` is special-cased by the engine,
which returns the ``result`` argument directly as the module's output and
ends the ReAct loop rather than appending a tool_result. The schema still
lives in this package so it's discoverable next to its siblings and so
``build_tool_list`` can include it the same way.
"""

from __future__ import annotations

from mesmer.core.constants import ToolName

NAME = ToolName.CONCLUDE

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME.value,
        "description": (
            "End this module's execution and return a result. "
            "Use when the objective is met, when you've exhausted your "
            "approach, or when you have enough information to report back."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "Summary of what happened and what was achieved.",
                },
                "objective_met": {
                    "type": "boolean",
                    "description": (
                        "Set to true ONLY when the OVERALL OBJECTIVE "
                        "(shown in the user message) has been unambiguously satisfied. "
                        "For sub-modules: leave unset or false — use the "
                        "OBJECTIVE SIGNAL marker in result instead. "
                        "For the leader: set true when you are certain the run should terminate."
                    ),
                },
            },
            "required": ["result"],
        },
    },
}

# Sentinel used when the LLM calls conclude() without a result argument.
DEFAULT_RESULT = "Module concluded without result."


__all__ = ["NAME", "SCHEMA", "DEFAULT_RESULT"]
