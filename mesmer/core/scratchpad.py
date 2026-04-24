"""Scratchpad — short-term per-run memory, injected as prompt context.

Mesmer has two orthogonal shared-state stores:

  * **Graph** (``AttackGraph`` → ``graph.json``) — permanent per-target
    history. Every module execution is a node, every node carries the
    module's raw ``module_output``, every run appends to the same graph
    file. Survives across runs; only ``--fresh`` wipes it. Source of
    truth for *"what have we ever learned about this target?"*.

  * **Scratchpad** (this module) — ephemeral per-run blackboard. A
    dict of named text slots. Populated at run start from the graph's
    latest outputs; auto-updated whenever a sub-module concludes (the
    framework writes ``scratchpad[module.name] = conclude_text``);
    discarded at run end. Source of truth for *"what is the current
    working context THIS run?"*.

Everything is a module: the framework doesn't know what a "profile" or
a "plan" is. It just writes every module's ``conclude()`` text to the
scratchpad under the module's name, and renders the whole scratchpad
as a ``## Scratchpad`` block into every subsequent module's user
message. Module authors decide what their output looks like; module
authors decide what to read from the scratchpad. No typed
abstractions. No framework specializations.

Why not just render the graph directly? Two reasons:

  1. **Name the concept.** "Scratchpad" is a practitioner-intuitive
     label for short-term working memory that humans already use when
     red-teaming. It keeps the engine → module contract legible.
  2. **Space for non-graph run-local state.** A future step counter,
     inter-module tally, or mid-run observation can go in the
     scratchpad without needing a graph node. The graph stays a
     record-of-attempts; the scratchpad is the "working notes" layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scratchpad:
    """Short-term per-run memory rendered into every module's user
    message. A plain ``dict[str, str]`` of named slots.

    Conventions:

      * Keys are typically module names (``"target-profiler"``,
        ``"attack-planner"``, ``"delimiter-injection"`` …) — the
        framework auto-writes each module's ``conclude()`` text under
        its name after every delegation.
      * Non-module keys are allowed (``"plan_step"``, ``"round"``) for
        run-local state modules want to share. No schema enforcement —
        scratchpad is intentionally a loose KV.
      * Empty values are skipped on render so an overwrite-with-empty
        doesn't leave a stale header.
    """

    entries: dict[str, str] = field(default_factory=dict)

    # --- Accessors ---

    def set(self, key: str, value: str) -> None:
        """Write a slot. Empty values are still stored (they skip
        rendering but stay queryable via :meth:`get`)."""
        self.entries[key] = value

    def get(self, key: str, default: str = "") -> str:
        return self.entries.get(key, default)

    def clear(self) -> None:
        self.entries.clear()

    def is_empty(self) -> bool:
        """True when no slot has any non-empty value. Drives engine's
        "render block only when non-empty" branching."""
        return not any((v or "").strip() for v in self.entries.values())

    # --- Rendering ---

    def render_for_prompt(self, *, max_chars_per_entry: int = 2400) -> str:
        """Compact markdown rendering for injection into a module's
        user message.

        One ``### <key>`` header per non-empty slot, contents below.
        Slots are emitted in sorted-key order for deterministic output
        (useful for diffing prompts across runs / tests).

        Entries exceeding ``max_chars_per_entry`` are truncated to the
        cap with an explicit ``[+N chars — see graph.json]`` suffix —
        **never silently**. Consumers who need the full text know
        where to look.
        """
        if self.is_empty():
            return ""
        parts: list[str] = []
        for key in sorted(self.entries.keys()):
            value = (self.entries[key] or "").rstrip()
            if not value:
                continue
            if len(value) > max_chars_per_entry:
                head = value[:max_chars_per_entry].rstrip()
                remaining = len(value) - max_chars_per_entry
                value = (
                    f"{head}\n\n[+{remaining} chars — "
                    "see graph.json for the full text]"
                )
            parts.append(f"### {key}\n{value}")
        return "\n\n".join(parts)


__all__ = ["Scratchpad"]
