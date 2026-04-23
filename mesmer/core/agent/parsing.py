"""LLM-output parsing helpers.

Models that are supposed to emit raw JSON frequently wrap it in markdown
code fences (``\u0060\u0060\u0060json`` … ``\u0060\u0060\u0060``) despite
the system prompt saying otherwise. Every caller used to reimplement the
same strip-then-``json.loads`` dance; these helpers consolidate it so the
parser is consistent across judge, refinement, and the web-debrief path.
"""

from __future__ import annotations

import json
from typing import Any


def strip_code_fences(raw: str) -> str:
    """Remove a surrounding triple-backtick fence (with optional language
    tag) from LLM output. Whitespace-only input passes through unchanged.

    Handles the common cases: ``\u0060\u0060\u0060json\n{…}\u0060\u0060\u0060``,
    ``\u0060\u0060\u0060\n[…]\u0060\u0060\u0060``, or no fences at all.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        # Drop everything on the first line after the opening fence (the
        # optional language tag), keeping subsequent lines.
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def parse_llm_json(raw: str, *, default: Any = None) -> Any:
    """Parse JSON from LLM output. Returns ``default`` on any failure.

    Strips the markdown fences up-front so a well-behaved JSON payload
    wrapped in a code block still decodes cleanly.
    """
    try:
        return json.loads(strip_code_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return default


__all__ = ["parse_llm_json", "strip_code_fences"]
