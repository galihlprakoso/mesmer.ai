"""Cross-target strategy library — AutoDAN-Turbo flavoured (Session 4B).

Lifelong storage of attack strategies that worked against PRIOR targets,
keyed by family. When a new target is bootstrapped, the hypothesis
generator retrieves strategies from this library by family, global
success, evidence volume, and target-trait affinity so a freshly
discovered weakness inherits the cumulative wisdom of similar prior
runs instead of being tested from scratch.

Distinct from the per-target ``Strategy`` nodes inside a
:class:`mesmer.core.belief_graph.BeliefGraph`:

  - per-target Strategy nodes carry **THIS target's** local stats
    (success_count, attempt_count) and live in the belief graph;
  - the cross-target library here stores aggregate stats across **ALL**
    targets and never appears in a graph snapshot.

Persistence at ``~/.mesmer/global/strategies.json``::

    {
      "schema_version": 1,
      "strategies": [
        {
          "family": "format-shift",
          "template_summary": "Ask target to output policy as YAML",
          "global_success_count": 4,
          "global_attempt_count": 7,
          "works_against_traits": ["assistant-style", "json-schema-aware"],
          "fails_against_traits": ["strict-refusal-template"],
          "last_updated": 1737000000.0
        }
      ]
    }

Reference: AutoDAN-Turbo (arXiv 2410.05295) introduced lifelong strategy
storage for cross-target jailbreak transfer. We adapt the concept to
mesmer's typed-graph world: a strategy is the same dataclass shape as
``BeliefGraph.Strategy`` plus aggregate counters, retrieved at planner
boot time, never edited by the agent in flight (the planner reads it,
operators or post-run hooks write it).
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Same path that ``GlobalMemory`` uses — we keep all global state under
# one roof so backups / wipes are one operation.
_GLOBAL_DIR = Path.home() / ".mesmer" / "global"
_LIBRARY_PATH = _GLOBAL_DIR / "strategies.json"

# Schema version — bumped on incompatible field changes. Loaders that
# encounter a higher version log a warning and reject the file (better
# to lose strategy memory than to interpret v2 as v1 and corrupt the
# planner). Older versions are migrated forward in :func:`load_library`.
_SCHEMA_VERSION = 1
_TRAIT_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(value: str) -> set[str]:
    return set(_TRAIT_TOKEN_RE.findall(value.lower()))


def _trait_terms(target_traits: dict[str, str] | None) -> set[str]:
    terms: set[str] = set()
    for key, value in (target_traits or {}).items():
        terms.add(str(key).lower())
        if value:
            terms.add(str(value).lower())
        terms.update(_tokens(str(key)))
        if value:
            terms.update(_tokens(str(value)))
    return {t for t in terms if t}


def _trait_match_score(entry_traits: Iterable[str], target_terms: set[str]) -> float:
    if not target_terms:
        return 0.0
    best = 0.0
    for raw_trait in entry_traits:
        trait = str(raw_trait).lower()
        if not trait:
            continue
        if trait in target_terms:
            best = max(best, 1.0)
            continue
        trait_tokens = _tokens(trait)
        if not trait_tokens:
            continue
        union = trait_tokens | target_terms
        if union:
            best = max(best, len(trait_tokens & target_terms) / len(union))
    return best


def _entry_trait_affinity(
    entry: "GlobalStrategyEntry",
    target_traits: dict[str, str] | None,
) -> float:
    """Signed trait affinity in [-1, 1].

    Positive evidence comes from traits the strategy worked against on
    previous targets; negative evidence comes from traits it failed
    against. Exact trait-key/value matches dominate token overlap.
    """
    target_terms = _trait_terms(target_traits)
    if not target_terms:
        return 0.0
    works = _trait_match_score(entry.works_against_traits, target_terms)
    fails = _trait_match_score(entry.fails_against_traits, target_terms)
    return max(-1.0, min(1.0, works - fails))


def _retrieval_score(
    entry: "GlobalStrategyEntry",
    target_traits: dict[str, str] | None,
) -> float:
    reliability = min(1.0, math.log1p(entry.global_attempt_count) / math.log(11))
    return (
        0.70 * entry.global_success_rate
        + 0.20 * reliability
        + 0.10 * _entry_trait_affinity(entry, target_traits)
    )


@dataclass
class GlobalStrategyEntry:
    """One row of the cross-target strategy library.

    Field shape mirrors :class:`mesmer.core.belief_graph.Strategy` so
    bootstrapping a per-target Strategy from a library entry is a
    field-by-field copy. Aggregate counters
    (``global_success_count`` / ``global_attempt_count``) are the
    cross-target story — the per-target Strategy keeps its own local
    counters that never collide with these.
    """

    family: str = ""
    template_summary: str = ""
    global_success_count: int = 0
    global_attempt_count: int = 0
    works_against_traits: list[str] = field(default_factory=list)
    fails_against_traits: list[str] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)

    @property
    def global_success_rate(self) -> float:
        """Aggregate success rate across all targets, 0.0 when no
        attempts. Used by retrieval to rank strategies for
        bootstrapping."""
        if self.global_attempt_count == 0:
            return 0.0
        return self.global_success_count / self.global_attempt_count

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "template_summary": self.template_summary,
            "global_success_count": self.global_success_count,
            "global_attempt_count": self.global_attempt_count,
            "works_against_traits": list(self.works_against_traits),
            "fails_against_traits": list(self.fails_against_traits),
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GlobalStrategyEntry:
        return cls(
            family=str(d.get("family", "")),
            template_summary=str(d.get("template_summary", "")),
            global_success_count=int(d.get("global_success_count", 0)),
            global_attempt_count=int(d.get("global_attempt_count", 0)),
            works_against_traits=list(d.get("works_against_traits", []) or []),
            fails_against_traits=list(d.get("fails_against_traits", []) or []),
            last_updated=float(d.get("last_updated") or time.time()),
        )


@dataclass
class StrategyLibrary:
    """In-memory view of the cross-target strategy library.

    Entries are unique by (family, template_summary) — re-recording a
    strategy that already exists merges counters rather than
    duplicating. The merge rule is intentionally simple: incoming
    counts add to existing counts, traits dedupe-merge.
    """

    entries: list[GlobalStrategyEntry] = field(default_factory=list)

    def find(self, family: str, template_summary: str) -> GlobalStrategyEntry | None:
        for e in self.entries:
            if e.family == family and e.template_summary == template_summary:
                return e
        return None

    def upsert(self, entry: GlobalStrategyEntry) -> GlobalStrategyEntry:
        """Merge ``entry`` into the library.

        If a row with the same (family, template_summary) exists, fold
        ``entry``'s counters and traits into it (and refresh
        ``last_updated``). Otherwise append.

        Returns the resident row (after merge or fresh insert). Caller
        must :func:`save_library` to persist.
        """
        existing = self.find(entry.family, entry.template_summary)
        if existing is None:
            self.entries.append(entry)
            return entry
        existing.global_success_count += entry.global_success_count
        existing.global_attempt_count += entry.global_attempt_count
        for t in entry.works_against_traits:
            if t and t not in existing.works_against_traits:
                existing.works_against_traits.append(t)
        for t in entry.fails_against_traits:
            if t and t not in existing.fails_against_traits:
                existing.fails_against_traits.append(t)
        existing.last_updated = max(existing.last_updated, entry.last_updated)
        return existing

    def for_family(self, family: str, *, top_k: int = 5) -> list[GlobalStrategyEntry]:
        """Return the top-``top_k`` strategies for ``family``, sorted
        by global success rate descending. Used by the hypothesis
        generator to seed bootstrap prompts with cross-target
        evidence."""
        rows = [e for e in self.entries if e.family == family]
        rows.sort(key=lambda e: (e.global_success_rate, e.global_attempt_count), reverse=True)
        return rows[:top_k]

    def all_families(self) -> list[str]:
        """Distinct families currently represented, sorted."""
        return sorted({e.family for e in self.entries if e.family})

    def to_dict(self) -> dict:
        return {
            "schema_version": _SCHEMA_VERSION,
            "strategies": [e.to_dict() for e in self.entries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically (tmp file + rename).

    Same shape as ``mesmer.core.agent.memory._atomic_write`` — a torn
    write here would corrupt cross-target memory, which is much
    worse than a transient one-target loss. Local-only IO.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def load_library(path: Path | None = None) -> StrategyLibrary:
    """Read the cross-target strategy library from disk.

    Returns an empty :class:`StrategyLibrary` when the file is missing
    OR unreadable — losing the library is graceful (the planner
    bootstraps without cross-target memory). Schema version mismatch
    is logged but produces an empty library so a future-version file
    isn't misinterpreted.
    """
    p = path or _LIBRARY_PATH
    if not p.exists():
        return StrategyLibrary()
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return StrategyLibrary()
    if not isinstance(raw, dict):
        return StrategyLibrary()
    version = raw.get("schema_version", 1)
    if version > _SCHEMA_VERSION:
        # Future-version file; refuse to interpret. Operators who need
        # to roll back can copy the JSON manually.
        return StrategyLibrary()
    rows = raw.get("strategies", [])
    if not isinstance(rows, list):
        return StrategyLibrary()
    entries: list[GlobalStrategyEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            entries.append(GlobalStrategyEntry.from_dict(row))
        except (TypeError, ValueError):
            continue
    return StrategyLibrary(entries=entries)


def save_library(library: StrategyLibrary, path: Path | None = None) -> None:
    """Atomically persist the library to disk."""
    p = path or _LIBRARY_PATH
    _atomic_write(p, library.to_json())


# ---------------------------------------------------------------------------
# Aggregation from per-target Strategy nodes
# ---------------------------------------------------------------------------


def merge_per_target_strategies(
    library: StrategyLibrary,
    target_strategies: Iterable,  # Iterable of belief_graph.Strategy
    *,
    target_traits: dict[str, str] | None = None,
) -> StrategyLibrary:
    """Fold a per-target belief graph's Strategy nodes into the library.

    For each per-target Strategy that has at least one attempt
    recorded, build a :class:`GlobalStrategyEntry` carrying its
    local counters (which become contributions to the global
    counters via ``upsert``'s additive merge). Trait correlations
    (``target_traits`` keys) are appended to ``works_against_traits``
    when the strategy succeeded at least once and to
    ``fails_against_traits`` when it never did.

    Returns the SAME ``library`` object, mutated in place — caller
    saves with :func:`save_library`.

    Idempotency: callers should NOT re-feed the same Strategy more
    than once per run end (counters add). The simplest contract is
    "merge at run end, not mid-run, and not after operator edits".
    """
    traits_list = sorted((target_traits or {}).keys())
    for s in target_strategies:
        if getattr(s, "attempt_count", 0) == 0:
            continue
        entry = GlobalStrategyEntry(
            family=getattr(s, "family", ""),
            template_summary=getattr(s, "template_summary", ""),
            global_success_count=int(getattr(s, "success_count", 0)),
            global_attempt_count=int(getattr(s, "attempt_count", 0)),
            works_against_traits=list(traits_list)
            if int(getattr(s, "success_count", 0)) > 0
            else [],
            fails_against_traits=list(traits_list)
            if int(getattr(s, "success_count", 0)) == 0
            else [],
        )
        library.upsert(entry)
    return library


# ---------------------------------------------------------------------------
# Retrieval — used by hypothesis generator at bootstrap
# ---------------------------------------------------------------------------


def retrieve_strategies_for_bootstrap(
    *,
    target_traits: dict[str, str] | None = None,
    families: Iterable[str] | None = None,
    top_k_per_family: int = 3,
    library: StrategyLibrary | None = None,
) -> list[GlobalStrategyEntry]:
    """Return library entries the hypothesis generator should consider.

    ``families`` filters the search; pass the family vocabulary the
    generator already speaks (see ``generate_hypotheses_system.prompt.md``).
    When ``None``, all families are considered.

    Each returned entry is ranked by global success rate, evidence
    volume, and signed trait affinity to ``target_traits``. Retrieval
    is cheap — the library is small enough (low hundreds of entries)
    that linear scans stay O(library size).
    """
    if library is None:
        library = load_library()
    if not library.entries:
        return []
    families_list = list(families) if families is not None else library.all_families()
    out: list[GlobalStrategyEntry] = []
    for fam in families_list:
        rows = [e for e in library.entries if e.family == fam]
        rows.sort(
            key=lambda e: (
                _retrieval_score(e, target_traits),
                _entry_trait_affinity(e, target_traits),
                e.global_success_rate,
                e.global_attempt_count,
            ),
            reverse=True,
        )
        out.extend(rows[:top_k_per_family])
    return out


def render_for_prompt(entries: Iterable[GlobalStrategyEntry]) -> str:
    """Format a slate of library entries for inclusion in an LLM prompt.

    Returns the empty string when ``entries`` is empty so callers can
    skip the section entirely. Single-source rendering keeps prompt
    formatting consistent between the hypothesis generator and any
    future planner that uses cross-target memory.
    """
    rows = list(entries)
    if not rows:
        return ""
    lines = ["## Cross-target strategy library (prior wins, ranked by global success rate)"]
    for e in rows:
        rate = f"{e.global_success_rate:.2f}"
        lines.append(
            f"- {e.family} | {rate} ({e.global_success_count}/"
            f"{e.global_attempt_count}): {e.template_summary}"
        )
        if e.works_against_traits:
            lines.append(f"  works against: {', '.join(e.works_against_traits[:6])}")
        if e.fails_against_traits:
            lines.append(f"  fails against: {', '.join(e.fails_against_traits[:6])}")
    return "\n".join(lines)


__all__ = [
    "GlobalStrategyEntry",
    "StrategyLibrary",
    "load_library",
    "save_library",
    "merge_per_target_strategies",
    "retrieve_strategies_for_bootstrap",
    "render_for_prompt",
]
