"""Tests for Scratchpad — short-term per-run memory.

Scratchpad + AttackGraph.conversation_history are the two inter-module
state abstractions:

  * Scratchpad = current-state KV snapshot (latest output per module).
  * Conversation history = timeline view (ordered sequence of turns).

These tests cover the scratchpad mechanics: set/get/clear, empty
detection, render formatting, and truncation.
"""

from __future__ import annotations

from mesmer.core.scratchpad import Scratchpad


class TestAccessors:
    def test_default_is_empty(self):
        sp = Scratchpad()
        assert sp.is_empty()
        assert sp.get("missing") == ""
        assert sp.get("missing", default="dflt") == "dflt"

    def test_set_and_get_round_trip(self):
        sp = Scratchpad()
        sp.set("target-profiler", "DOSSIER TEXT")
        assert sp.get("target-profiler") == "DOSSIER TEXT"
        assert not sp.is_empty()

    def test_empty_string_set_keeps_slot_but_is_still_empty(self):
        """An explicit empty-string set must not crash or render, but
        can be queried (intentional overwrite signal)."""
        sp = Scratchpad()
        sp.set("key", "some")
        sp.set("key", "")
        assert sp.get("key") == ""
        assert sp.is_empty()

    def test_clear_wipes_all(self):
        sp = Scratchpad()
        sp.set("a", "1")
        sp.set("b", "2")
        sp.clear()
        assert sp.is_empty()
        assert sp.get("a") == ""


class TestRenderForPrompt:
    def test_empty_renders_nothing(self):
        assert Scratchpad().render_for_prompt() == ""

    def test_single_slot_renders_header_and_body(self):
        sp = Scratchpad()
        sp.set("target-profiler", "DOSSIER")
        out = sp.render_for_prompt()
        assert "### target-profiler" in out
        assert "DOSSIER" in out

    def test_multiple_slots_sorted_by_key(self):
        """Deterministic sort matters for diff-friendly prompt output."""
        sp = Scratchpad()
        sp.set("zzz", "last")
        sp.set("aaa", "first")
        sp.set("mmm", "middle")
        out = sp.render_for_prompt()
        assert out.index("### aaa") < out.index("### mmm") < out.index("### zzz")

    def test_empty_values_skip_in_render(self):
        """An overwrite-with-empty leaves the slot queryable but doesn't
        clutter the rendered prompt."""
        sp = Scratchpad()
        sp.set("kept", "value")
        sp.set("blanked", "")
        out = sp.render_for_prompt()
        assert "### kept" in out
        assert "### blanked" not in out

    def test_long_value_truncates_with_explicit_suffix(self):
        """No silent truncation — the render tells readers where to
        look for the full text."""
        sp = Scratchpad()
        sp.set("verbose", "X" * 5000)
        out = sp.render_for_prompt(max_chars_per_entry=1000)
        assert "see graph.json" in out
        assert "chars" in out
