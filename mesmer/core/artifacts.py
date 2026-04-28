"""Markdown artifact store.

Artifacts are Mesmer's durable knowledge layer. Each artifact is a Markdown
document keyed by an id such as ``target-profiler`` or ``system_prompt``.
The AttackGraph remains the raw execution log; artifacts are the current
materialized state agents and humans read, search, and patch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from mesmer.core.patching import (
    MarkdownPatchError,
    MarkdownPatchOperationPayload,
    apply_markdown_patch,
)


ARTIFACT_FILE_SUFFIX = ".md"
ARTIFACT_PROMPT_HEADING = "## Artifact Brief"
ARTIFACT_TOOL_HINT = (
    "Use `list_artifacts`, `search_artifacts`, and `read_artifact` "
    "for full details. Use `update_artifact` to patch durable knowledge."
)
DEFAULT_ARTIFACT_SEARCH_LIMIT = 8
MAX_ARTIFACT_SEARCH_LIMIT = 50
DEFAULT_ARTIFACT_BRIEF_ITEMS = 12
MAX_ARTIFACT_HEADINGS = 12
SNIPPET_CHARS = 260

ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class StandardArtifactId(str, Enum):
    """Well-known artifact ids used by framework prompts and UI."""

    OPERATOR_NOTES = "operator_notes"


class ArtifactPatchMode(str, Enum):
    """How an artifact update is represented."""

    REPLACE = "replace"
    PATCH = "patch"


class ArtifactUpdateStatus(str, Enum):
    """Status values emitted by artifact update tools."""

    SAVED = "saved"


class ArtifactError(ValueError):
    """Raised when an artifact operation cannot be applied."""


@dataclass(frozen=True)
class ArtifactSpec:
    """Declarative artifact expected by a scenario."""

    id: str
    title: str = ""
    format: str = "markdown"
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", validate_artifact_id(self.id))
        object.__setattr__(self, "format", (self.format or "markdown").strip().lower())
        if self.format != "markdown":
            raise ArtifactError(
                f"artifact {self.id!r} uses unsupported format {self.format!r}"
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "format": self.format,
            "description": self.description,
        }


@dataclass(frozen=True)
class ArtifactSummary:
    id: str
    title: str
    chars: int
    headings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "chars": self.chars,
            "headings": self.headings,
        }


@dataclass(frozen=True)
class ArtifactListItem:
    id: str
    title: str
    format: str
    description: str
    declared: bool
    exists: bool
    chars: int
    headings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "format": self.format,
            "description": self.description,
            "declared": self.declared,
            "exists": self.exists,
            "chars": self.chars,
            "headings": self.headings,
        }


@dataclass(frozen=True)
class ArtifactSearchHit:
    artifact_id: str
    title: str
    heading: str
    score: int
    snippet: str

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "title": self.title,
            "heading": self.heading,
            "score": self.score,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class ArtifactUpdate:
    artifact_id: str
    mode: ArtifactPatchMode
    content: str | None = None
    operations: Sequence[MarkdownPatchOperationPayload] | None = None


@dataclass(frozen=True)
class ArtifactUpdateResult:
    artifact_id: str
    summaries: list[str]
    chars: int

    def to_dict(self) -> dict:
        return {
            "status": ArtifactUpdateStatus.SAVED.value,
            "artifact_id": self.artifact_id,
            "summaries": self.summaries,
            "chars": self.chars,
        }


def validate_artifact_id(artifact_id: str) -> str:
    artifact_id = str(artifact_id or "").strip()
    if not artifact_id:
        raise ArtifactError("artifact_id is required")
    if not ARTIFACT_ID_RE.match(artifact_id):
        raise ArtifactError(
            "artifact_id must use only letters, numbers, '.', '_', ':', or '-'"
        )
    return artifact_id


def artifact_title(artifact_id: str) -> str:
    return validate_artifact_id(artifact_id).replace("_", " ").replace("-", " ").title()


def declared_artifact_ids(specs: Sequence[ArtifactSpec]) -> set[str]:
    return {spec.id for spec in specs}


def render_artifact_contract(specs: Sequence[ArtifactSpec]) -> str:
    specs = list(specs or [])
    if not specs:
        return ""
    lines = [
        "## Artifact Contract",
        "These are the scenario-declared durable Markdown documents. "
        "Use `update_artifact` with these exact artifact_id values when "
        "you have curated knowledge worth preserving. Manager conclude text "
        "stays in Module Conversation History; artifacts are consolidated outputs.",
    ]
    for spec in specs:
        title = f" — {spec.title}" if spec.title else ""
        desc = f": {spec.description}" if spec.description else ""
        lines.append(f"- `{spec.id}`{title} ({spec.format}){desc}")
    return "\n".join(lines)


def artifact_list_items(
    store: "ArtifactStore",
    specs: Sequence[ArtifactSpec] | None = None,
) -> list[ArtifactListItem]:
    specs = list(specs or [])
    summaries = {summary.id: summary for summary in store.summaries()}
    items: list[ArtifactListItem] = []
    for spec in specs:
        summary = summaries.pop(spec.id, None)
        items.append(
            ArtifactListItem(
                id=spec.id,
                title=summary.title if summary else (spec.title or artifact_title(spec.id)),
                format=spec.format,
                description=spec.description,
                declared=True,
                exists=summary is not None,
                chars=summary.chars if summary else 0,
                headings=summary.headings if summary else [],
            )
        )
    if specs:
        return sorted(items, key=lambda item: item.id)
    for summary in summaries.values():
        items.append(
            ArtifactListItem(
                id=summary.id,
                title=summary.title,
                format="markdown",
                description="",
                declared=False,
                exists=True,
                chars=summary.chars,
                headings=summary.headings,
            )
        )
    return sorted(items, key=lambda item: (not item.declared, item.id))


def _headings(markdown: str, *, limit: int = MAX_ARTIFACT_HEADINGS) -> list[str]:
    out: list[str] = []
    for match in HEADING_RE.finditer(markdown or ""):
        out.append(match.group(2).strip())
        if len(out) >= limit:
            break
    return out


def _section(markdown: str, heading: str) -> str | None:
    wanted = str(heading or "").strip().lstrip("#").strip().casefold()
    if not wanted:
        return None
    lines = (markdown or "").splitlines()
    start: int | None = None
    level = 0
    for idx, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        if start is None:
            if match.group(2).strip().casefold() == wanted:
                start = idx
                level = len(match.group(1))
            continue
        if len(match.group(1)) <= level:
            return "\n".join(lines[start:idx]).rstrip() + "\n"
    if start is None:
        return None
    return "\n".join(lines[start:]).rstrip() + "\n"


def _snippet(text: str, query_terms: list[str], *, cap: int = SNIPPET_CHARS) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= cap:
        return compact
    lowered = compact.casefold()
    first = min(
        (lowered.find(t) for t in query_terms if t and lowered.find(t) >= 0),
        default=0,
    )
    start = max(0, first - 80)
    end = min(len(compact), start + cap)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix


class ArtifactStore:
    """In-memory artifact collection with Markdown patch operations."""

    def __init__(self, artifacts: Mapping[str, str] | None = None) -> None:
        self._docs: dict[str, str] = {}
        for artifact_id, content in (artifacts or {}).items():
            self.set(artifact_id, content)

    def __contains__(self, artifact_id: str) -> bool:
        return validate_artifact_id(artifact_id) in self._docs

    def is_empty(self) -> bool:
        return not any(v.strip() for v in self._docs.values())

    def ids(self) -> list[str]:
        return sorted(self._docs)

    def get(self, artifact_id: str, default: str = "") -> str:
        return self._docs.get(validate_artifact_id(artifact_id), default)

    def set(self, artifact_id: str, content: str) -> None:
        if not isinstance(content, str):
            raise ArtifactError("artifact content must be a string")
        self._docs[validate_artifact_id(artifact_id)] = content

    def delete(self, artifact_id: str) -> None:
        self._docs.pop(validate_artifact_id(artifact_id), None)

    def summaries(self) -> list[ArtifactSummary]:
        return [
            ArtifactSummary(
                id=artifact_id,
                title=artifact_title(artifact_id),
                chars=len(content),
                headings=_headings(content),
            )
            for artifact_id, content in sorted(self._docs.items())
            if content.strip()
        ]

    def update(
        self,
        update: ArtifactUpdate,
    ) -> ArtifactUpdateResult:
        artifact_id = validate_artifact_id(update.artifact_id)
        if update.mode is ArtifactPatchMode.REPLACE:
            if not isinstance(update.content, str):
                raise ArtifactError("content must be a string")
            self._docs[artifact_id] = update.content
            summaries = [f"replaced artifact {artifact_id!r}"]
            return ArtifactUpdateResult(artifact_id, summaries, len(self._docs[artifact_id]))
        if update.mode is not ArtifactPatchMode.PATCH:
            raise ArtifactError(f"unsupported artifact patch mode: {update.mode}")
        try:
            patch = apply_markdown_patch(
                self._docs.get(artifact_id, ""),
                list(update.operations or []),
            )
        except MarkdownPatchError as e:
            raise ArtifactError(str(e)) from e
        self._docs[artifact_id] = patch.content
        return ArtifactUpdateResult(artifact_id, patch.summaries, len(patch.content))

    def read(self, artifact_id: str, *, sections: list[str] | None = None) -> str:
        content = self.get(artifact_id)
        if not sections:
            return content
        parts: list[str] = []
        for heading in sections:
            section = _section(content, heading)
            if section:
                parts.append(section)
        return "\n\n".join(parts).rstrip() + ("\n" if parts else "")

    def search(
        self,
        query: str,
        *,
        artifact_ids: list[str] | None = None,
        limit: int = DEFAULT_ARTIFACT_SEARCH_LIMIT,
    ) -> list[ArtifactSearchHit]:
        terms = [t.casefold() for t in re.findall(r"[\w:-]+", query or "") if t.strip()]
        ids = [validate_artifact_id(i) for i in artifact_ids] if artifact_ids else self.ids()
        hits: list[ArtifactSearchHit] = []
        for artifact_id in ids:
            content = self._docs.get(artifact_id, "")
            if not content.strip():
                continue
            chunks = self._section_chunks(content)
            for heading_path, chunk in chunks:
                haystack = (heading_path + "\n" + chunk).casefold()
                if terms:
                    score = sum(haystack.count(t) for t in terms)
                    if score <= 0:
                        continue
                else:
                    score = 1
                hits.append(
                    ArtifactSearchHit(
                        artifact_id=artifact_id,
                        title=artifact_title(artifact_id),
                        heading=heading_path,
                        score=score,
                        snippet=_snippet(chunk, terms),
                    )
                )
        hits.sort(key=lambda h: (-h.score, h.artifact_id, h.heading))
        return hits[: max(1, min(int(limit or DEFAULT_ARTIFACT_SEARCH_LIMIT), MAX_ARTIFACT_SEARCH_LIMIT))]

    def render_brief_for_prompt(self, *, max_items: int = DEFAULT_ARTIFACT_BRIEF_ITEMS) -> str:
        summaries = self.summaries()[:max_items]
        if not summaries:
            return ""
        lines = [ARTIFACT_PROMPT_HEADING]
        for item in summaries:
            heading = f"; sections: {', '.join(item.headings[:4])}" if item.headings else ""
            lines.append(f"- `{item.id}` ({item.chars} chars{heading})")
        lines.append("\n" + ARTIFACT_TOOL_HINT)
        return "\n".join(lines)

    def to_files(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        existing = {p.stem for p in directory.glob(f"*{ARTIFACT_FILE_SUFFIX}")}
        current = set(self._docs)
        for stale in existing - current:
            (directory / f"{stale}.md").unlink(missing_ok=True)
        for artifact_id, content in self._docs.items():
            path = directory / f"{artifact_id}{ARTIFACT_FILE_SUFFIX}"
            if content.strip():
                path.write_text(content, encoding="utf-8")
            else:
                path.unlink(missing_ok=True)

    @classmethod
    def from_files(cls, directory: Path) -> "ArtifactStore":
        docs: dict[str, str] = {}
        if directory.exists():
            for path in sorted(directory.glob(f"*{ARTIFACT_FILE_SUFFIX}")):
                try:
                    artifact_id = validate_artifact_id(path.stem)
                except ArtifactError:
                    continue
                docs[artifact_id] = path.read_text(encoding="utf-8")
        return cls(docs)

    def to_storage(self, storage, prefix: str) -> None:
        """Persist artifacts via a storage provider.

        ``prefix`` is the directory-like storage key that contains
        ``{artifact_id}.md`` files. The on-disk shape remains identical to
        :meth:`to_files`; only the IO backend changes.
        """
        from mesmer.core.persistence import join_storage_key

        existing: set[str] = set()
        for key in storage.list_files(prefix, suffix=ARTIFACT_FILE_SUFFIX):
            stem = Path(key).stem
            try:
                existing.add(validate_artifact_id(stem))
            except ArtifactError:
                continue
        current = set(self._docs)
        for stale in existing - current:
            storage.delete(join_storage_key(prefix, f"{stale}{ARTIFACT_FILE_SUFFIX}"))
        for artifact_id, content in self._docs.items():
            key = join_storage_key(prefix, f"{artifact_id}{ARTIFACT_FILE_SUFFIX}")
            if content.strip():
                storage.write_text(key, content)
            else:
                storage.delete(key, missing_ok=True)

    @classmethod
    def from_storage(cls, storage, prefix: str) -> "ArtifactStore":
        """Load artifacts via a storage provider."""
        docs: dict[str, str] = {}
        for key in storage.list_files(prefix, suffix=ARTIFACT_FILE_SUFFIX):
            try:
                artifact_id = validate_artifact_id(Path(key).stem)
            except ArtifactError:
                continue
            docs[artifact_id] = storage.read_text(key)
        return cls(docs)

    @staticmethod
    def _section_chunks(markdown: str) -> list[tuple[str, str]]:
        lines = (markdown or "").splitlines()
        chunks: list[tuple[str, str]] = []
        current_heading = "(root)"
        current: list[str] = []
        for line in lines:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                if current:
                    chunks.append((current_heading, "\n".join(current).strip()))
                current_heading = match.group(2).strip()
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append((current_heading, "\n".join(current).strip()))
        return chunks


__all__ = [
    "ArtifactError",
    "ArtifactPatchMode",
    "ArtifactListItem",
    "ArtifactSpec",
    "ArtifactStore",
    "ArtifactSearchHit",
    "ArtifactSummary",
    "ArtifactUpdate",
    "ArtifactUpdateResult",
    "ArtifactUpdateStatus",
    "StandardArtifactId",
    "ARTIFACT_FILE_SUFFIX",
    "ARTIFACT_PROMPT_HEADING",
    "ARTIFACT_TOOL_HINT",
    "artifact_list_items",
    "artifact_title",
    "declared_artifact_ids",
    "render_artifact_contract",
    "validate_artifact_id",
]
