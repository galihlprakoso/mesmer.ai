"""Shared helper used by every tool handler to format its return value.

Kept in its own file (not in ``tools/__init__.py``) so each tool module
can import it without pulling in the full dispatch table — that would
create an import cycle the day ``__init__.py`` grows to reference the
tool modules.
"""

from __future__ import annotations


def tool_result(tool_call_id: str, content: str) -> dict:
    """Build an OpenAI ``{"role": "tool", …}`` response dict.

    The ReAct engine appends one of these to ``messages`` after every tool
    call so the next LLM turn can read the outcome.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


__all__ = ["tool_result"]
