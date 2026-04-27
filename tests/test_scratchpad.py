"""Tests for Scratchpad — shared whiteboard plus module output cache.

Scratchpad + AttackGraph.conversation_history are the two inter-module
state abstractions:

  * Scratchpad content = shared markdown whiteboard.
  * module_outputs = current-state cache (latest output per module).
  * Conversation history = timeline view (ordered sequence of turns).

These tests cover whiteboard rendering plus the separate module-output cache.
"""

from __future__ import annotations

from mesmer.core.scratchpad import Scratchpad


class TestAccessors:
    def test_default_is_empty(self):
        sp = Scratchpad()
        assert sp.is_empty()
        assert sp.module_output("missing") == ""
        assert sp.module_output("missing", default="dflt") == "dflt"

    def test_set_and_get_round_trip(self):
        sp = Scratchpad()
        sp.set_module_output("target-profiler", "DOSSIER TEXT")
        assert sp.module_output("target-profiler") == "DOSSIER TEXT"
        assert sp.is_empty()

    def test_update_sets_shared_whiteboard(self):
        sp = Scratchpad()
        sp.update("## Target\nresearch-l1")
        assert sp.content == "## Target\nresearch-l1"
        assert not sp.is_empty()

    def test_empty_string_set_keeps_slot_but_is_still_empty(self):
        """An explicit empty-string set must not crash or render, but
        can be queried (intentional overwrite signal)."""
        sp = Scratchpad()
        sp.set_module_output("key", "some")
        sp.set_module_output("key", "")
        assert sp.module_output("key") == ""
        assert sp.is_empty()

    def test_clear_wipes_all(self):
        sp = Scratchpad()
        sp.set_module_output("a", "1")
        sp.set_module_output("b", "2")
        sp.update("notes")
        sp.clear()
        assert sp.is_empty()
        assert sp.module_output("a") == ""
        assert sp.content == ""


class TestRenderForPrompt:
    def test_empty_renders_nothing(self):
        assert Scratchpad().render_for_prompt() == ""

    def test_whiteboard_renders_without_synthetic_slot_headers(self):
        sp = Scratchpad()
        sp.update("## Target\nresearch-l1")
        out = sp.render_for_prompt()
        assert out == "## Target\nresearch-l1"
        assert "### target-profiler" not in out

    def test_long_value_truncates_with_explicit_suffix(self):
        """No silent truncation — the render tells readers where to
        look for the full text."""
        sp = Scratchpad()
        sp.update("X" * 5000)
        out = sp.render_for_prompt(max_chars_per_entry=1000)
        assert "see scratchpad.md" in out
        assert "chars" in out
