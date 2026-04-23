"""Prompt text loaded from sibling .prompt.md files.

Keeps agent code scannable — multi-line instruction text lives next to the
code in its own file instead of drowning module bodies. Prompts are static;
each file is read once at package import.
"""

from pathlib import Path

_DIR = Path(__file__).parent


def _load(stem: str) -> str:
    """Read ``<stem>.prompt.md`` and strip the trailing newline."""
    return (_DIR / f"{stem}.prompt.md").read_text().rstrip("\n")


CONTINUATION_PREAMBLE = _load("continuation")
JUDGE_SYSTEM = _load("judge_system")
CONTINUOUS_JUDGE_ADDENDUM = _load("judge_continuous_addendum")
JUDGE_USER = _load("judge_user")
REFINE_APPROACH_PROMPT = _load("refine_approach")
REFLECT_PROMPT = _load("reflect")
SUMMARY_SYSTEM = _load("summary_system")


__all__ = [
    "CONTINUATION_PREAMBLE",
    "JUDGE_SYSTEM",
    "CONTINUOUS_JUDGE_ADDENDUM",
    "JUDGE_USER",
    "REFINE_APPROACH_PROMPT",
    "REFLECT_PROMPT",
    "SUMMARY_SYSTEM",
]
