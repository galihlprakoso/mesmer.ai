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
SUMMARY_SYSTEM = _load("summary_system")
EXECUTIVE_SYSTEM = _load("executive")

# Belief Attack Graph (Session 1) — extractor + hypothesis generator.
EXTRACT_EVIDENCE_SYSTEM = _load("extract_evidence_system")
EXTRACT_EVIDENCE_USER = _load("extract_evidence_user")
GENERATE_HYPOTHESES_SYSTEM = _load("generate_hypotheses_system")
GENERATE_HYPOTHESES_USER = _load("generate_hypotheses_user")


__all__ = [
    "CONTINUATION_PREAMBLE",
    "JUDGE_SYSTEM",
    "CONTINUOUS_JUDGE_ADDENDUM",
    "JUDGE_USER",
    "REFINE_APPROACH_PROMPT",
    "SUMMARY_SYSTEM",
    "EXECUTIVE_SYSTEM",
    "EXTRACT_EVIDENCE_SYSTEM",
    "EXTRACT_EVIDENCE_USER",
    "GENERATE_HYPOTHESES_SYSTEM",
    "GENERATE_HYPOTHESES_USER",
]
