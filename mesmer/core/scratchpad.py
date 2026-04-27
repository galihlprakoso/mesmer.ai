"""Shared scratchpad and module-output cache.

Mesmer keeps three related but distinct state surfaces:

* ``AttackGraph`` is the complete audit timeline.
* ``module_outputs`` is a latest-output cache keyed by module name. It is
  useful for ordered phase gates and precise handoff checks, but it is not the
  human-style scratchpad shown to agents.
* ``scratchpad`` is one shared markdown whiteboard. Agents edit the whole
  whiteboard through ``update_scratchpad``; it is rendered as a single note
  block into prompts and persisted as ``scratchpad.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scratchpad:
    """One shared markdown whiteboard plus latest module outputs.

    ``content`` is the actual scratchpad. ``module_outputs`` is kept separate
    so callers can still ask "what did module X last conclude?" without
    bloating the shared working note.
    """

    content: str = ""
    module_outputs: dict[str, str] = field(default_factory=dict)

    # --- Module-output cache ---

    def set_module_output(self, key: str, value: str) -> None:
        """Record the latest raw output for a module."""
        self.module_outputs[key] = value

    def module_output(self, key: str, default: str = "") -> str:
        """Return the latest raw output recorded for a module."""
        return self.module_outputs.get(key, default)

    def update(self, content: str) -> None:
        """Rewrite the shared whiteboard."""
        self.content = content

    def clear(self) -> None:
        self.content = ""
        self.module_outputs.clear()

    def is_empty(self) -> bool:
        """True when the shared whiteboard has no content."""
        return not self.content.strip()

    # --- Rendering ---

    def render_for_prompt(self, *, max_chars_per_entry: int = 2400) -> str:
        """Compact markdown rendering for injection into a module's
        user message.

        Renders one shared markdown note. Full per-module outputs live in
        graph history and ``module_outputs``; they are intentionally not
        expanded here.
        """
        if self.is_empty():
            return ""
        value = self.content.rstrip()
        if len(value) > max_chars_per_entry:
            head = value[:max_chars_per_entry].rstrip()
            remaining = len(value) - max_chars_per_entry
            value = f"{head}\n\n[+{remaining} chars — see scratchpad.md for the full text]"
        return value


__all__ = ["Scratchpad"]
