"""Small markdown patch engine for AI-facing edit tools.

The API is operation based instead of line-number based. That makes it a
better fit for LLM tool calls: the model can say "append this bullet under
Evidence" without having to regenerate the whole document or produce a brittle
unified diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Callable, TypedDict


class MarkdownPatchOperation(str, Enum):
    """Supported structured edit operations for Markdown artifacts."""

    APPEND_SECTION = "append_section"
    REPLACE_SECTION = "replace_section"
    DELETE_SECTION = "delete_section"
    DELETE_MATCHING_LINE = "delete_matching_line"
    REPLACE_MATCHING_LINE = "replace_matching_line"
    INSERT_AFTER = "insert_after"
    INSERT_BEFORE = "insert_before"


class MarkdownPatchOperationPayload(TypedDict, total=False):
    op: str
    heading: str
    level: int
    content: str
    match: str
    replacement: str
    all: bool


class MarkdownPatchError(ValueError):
    """Raised when a markdown patch operation is invalid or cannot apply."""


@dataclass(frozen=True)
class MarkdownPatchResult:
    content: str
    summaries: list[str]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _normalize_heading(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        raise MarkdownPatchError("heading is required")
    match = _HEADING_RE.match(raw)
    if match:
        return match.group(2).strip()
    return raw.lstrip("#").strip()


def _heading_line(heading: str, level: int = 2) -> str:
    heading = _normalize_heading(heading)
    level = max(1, min(6, int(level or 2)))
    return f"{'#' * level} {heading}"


def _split_lines(text: str) -> list[str]:
    return (text or "").splitlines()


def _join_lines(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _find_section(lines: list[str], heading: str) -> tuple[int, int, int, int] | None:
    wanted = _normalize_heading(heading).casefold()
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        if match.group(2).strip().casefold() != wanted:
            continue
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            other = _HEADING_RE.match(lines[j])
            if other and len(other.group(1)) <= level:
                end = j
                break
        return idx, idx + 1, end, level
    return None


def _content_lines(content: Any) -> list[str]:
    if not isinstance(content, str):
        raise MarkdownPatchError("content must be a string")
    return content.strip("\n").splitlines()


PatchHandler = Callable[[list[str], MarkdownPatchOperationPayload], str]


def _append_section(lines: list[str], op: MarkdownPatchOperationPayload) -> str:
    heading = _normalize_heading(op.get("heading"))
    content = _content_lines(op.get("content", ""))
    section = _find_section(lines, heading)
    if section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(_heading_line(heading, op.get("level", 2)))
        if content:
            lines.extend(content)
        return f"created section {heading!r}"

    _start, _body_start, end, _level = section
    insert = content
    if insert:
        if end > 0 and lines[end - 1].strip():
            insert = ["", *insert]
        lines[end:end] = insert
    return f"appended to section {heading!r}"


def _replace_section(lines: list[str], op: MarkdownPatchOperationPayload) -> str:
    heading = _normalize_heading(op.get("heading"))
    content = _content_lines(op.get("content", ""))
    section = _find_section(lines, heading)
    if section is None:
        raise MarkdownPatchError(f"section not found: {heading}")
    start, _body_start, end, level = section
    lines[start:end] = [_heading_line(heading, level), *content]
    return f"replaced section {heading!r}"


def _delete_section(lines: list[str], op: MarkdownPatchOperationPayload) -> str:
    heading = _normalize_heading(op.get("heading"))
    section = _find_section(lines, heading)
    if section is None:
        raise MarkdownPatchError(f"section not found: {heading}")
    start, _body_start, end, _level = section
    del lines[start:end]
    while start < len(lines) and start > 0 and not lines[start].strip() and not lines[start - 1].strip():
        del lines[start]
    return f"deleted section {heading!r}"


def _matching_indexes(lines: list[str], match: Any) -> list[int]:
    if not isinstance(match, str) or not match:
        raise MarkdownPatchError("match must be a non-empty string")
    return [idx for idx, line in enumerate(lines) if match in line]


def _line_scope(op: MarkdownPatchOperationPayload) -> bool:
    return bool(op.get("all", False))


def _delete_matching_line(lines: list[str], op: MarkdownPatchOperationPayload) -> str:
    indexes = _matching_indexes(lines, op.get("match"))
    if not indexes:
        raise MarkdownPatchError(f"matching line not found: {op.get('match')!r}")
    targets = indexes if _line_scope(op) else indexes[:1]
    for idx in reversed(targets):
        del lines[idx]
    return f"deleted {len(targets)} matching line(s)"


def _replace_matching_line(lines: list[str], op: MarkdownPatchOperationPayload) -> str:
    replacement = op.get("replacement")
    if not isinstance(replacement, str):
        raise MarkdownPatchError("replacement must be a string")
    indexes = _matching_indexes(lines, op.get("match"))
    if not indexes:
        raise MarkdownPatchError(f"matching line not found: {op.get('match')!r}")
    targets = indexes if _line_scope(op) else indexes[:1]
    for idx in targets:
        lines[idx] = replacement
    return f"replaced {len(targets)} matching line(s)"


def _insert_relative(
    lines: list[str],
    op: MarkdownPatchOperationPayload,
    *,
    after: bool,
) -> str:
    content = _content_lines(op.get("content", ""))
    indexes = _matching_indexes(lines, op.get("match"))
    if not indexes:
        raise MarkdownPatchError(f"matching line not found: {op.get('match')!r}")
    idx = indexes[0] + (1 if after else 0)
    lines[idx:idx] = content
    direction = "after" if after else "before"
    return f"inserted {direction} matching line"


_OP_HANDLERS: dict[MarkdownPatchOperation, PatchHandler] = {
    MarkdownPatchOperation.APPEND_SECTION: _append_section,
    MarkdownPatchOperation.REPLACE_SECTION: _replace_section,
    MarkdownPatchOperation.DELETE_SECTION: _delete_section,
    MarkdownPatchOperation.DELETE_MATCHING_LINE: _delete_matching_line,
    MarkdownPatchOperation.REPLACE_MATCHING_LINE: _replace_matching_line,
    MarkdownPatchOperation.INSERT_AFTER: lambda lines, op: _insert_relative(lines, op, after=True),
    MarkdownPatchOperation.INSERT_BEFORE: lambda lines, op: _insert_relative(
        lines,
        op,
        after=False,
    ),
}


def _coerce_operation_name(name: object) -> MarkdownPatchOperation:
    try:
        return MarkdownPatchOperation(str(name))
    except ValueError as e:
        raise MarkdownPatchError(f"unsupported operation {name!r}") from e


def _coerce_operation_payload(op: object, index: int) -> MarkdownPatchOperationPayload:
    if not isinstance(op, dict):
        raise MarkdownPatchError(f"operation {index} must be an object")
    return MarkdownPatchOperationPayload(**op)


def apply_markdown_patch(
    text: str,
    operations: list[MarkdownPatchOperationPayload],
) -> MarkdownPatchResult:
    """Apply structured markdown operations and return patched content."""

    if not isinstance(operations, list) or not operations:
        raise MarkdownPatchError("operations must be a non-empty list")

    lines = _split_lines(text)
    summaries: list[str] = []
    for i, op in enumerate(operations, start=1):
        payload = _coerce_operation_payload(op, i)
        handler = _OP_HANDLERS[_coerce_operation_name(payload.get("op"))]
        summaries.append(handler(lines, payload))
    return MarkdownPatchResult(content=_join_lines(lines), summaries=summaries)


__all__ = [
    "MarkdownPatchError",
    "MarkdownPatchOperation",
    "MarkdownPatchOperationPayload",
    "MarkdownPatchResult",
    "apply_markdown_patch",
]
