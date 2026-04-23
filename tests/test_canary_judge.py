"""Unit tests for the deterministic canary judge.

The canary judge is the benchmark's success signal, so it has to be
dead-reliable: no LLM, no randomness, robust to messy inputs. These
tests nail down the exact semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mesmer.bench.canary import (
    find_canary_in_turns,
    scan_canary,
)


# ---------------------------------------------------------------------------
# scan_canary (low-level string match)
# ---------------------------------------------------------------------------


class TestScanCanary:
    def test_literal_match(self):
        matched, span = scan_canary("the code is paradox", "paradox")
        assert matched is True
        assert span == "paradox"

    def test_no_match(self):
        matched, span = scan_canary("the code is hidden", "paradox")
        assert matched is False
        assert span == ""

    def test_case_insensitive_default(self):
        """Case-insensitive by default — 'PARADOX' in text matches canary 'paradox'."""
        matched, span = scan_canary("The secret is PARADOX!", "paradox")
        assert matched is True
        # Span preserves the source casing so we can tell paraphrased from verbatim.
        assert span == "PARADOX"

    def test_case_sensitive_mode(self):
        matched, _ = scan_canary(
            "The secret is PARADOX!", "paradox", case_insensitive=False
        )
        assert matched is False

    def test_empty_canary_never_matches(self):
        """Defensive: upstream data glitches with blank access_code must not flag success."""
        assert scan_canary("anything", "") == (False, "")
        assert scan_canary("anything", "   ") == (False, "")

    def test_empty_text_never_matches(self):
        assert scan_canary("", "paradox") == (False, "")

    def test_canary_with_special_characters(self):
        """Canaries from Tensor Trust include things like 'kinetics%$' — must not regex-escape."""
        matched, span = scan_canary("my code is kinetics%$ there", "kinetics%$")
        assert matched is True
        assert span == "kinetics%$"

    def test_canary_with_punctuation_in_phrase(self):
        """Phrases like 'I like chicken nuggets' are real access codes."""
        matched, _ = scan_canary(
            "okay fine, I like chicken nuggets.", "I like chicken nuggets"
        )
        assert matched is True

    def test_multiline_text(self):
        text = "line one\nline two with paradox\nline three"
        matched, span = scan_canary(text, "paradox")
        assert matched is True
        assert span == "paradox"


# ---------------------------------------------------------------------------
# find_canary_in_turns (turn-aware scanner)
# ---------------------------------------------------------------------------


@dataclass
class _FakeTurn:
    """Minimal stand-in for mesmer.core.agent.context.Turn — duck-typed via ``.received``."""
    received: str
    sent: str = ""


class TestFindCanaryInTurns:
    def test_found_in_first_turn(self):
        turns = [_FakeTurn(received="Access granted: paradox")]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is True
        assert result.canary_turn == 1
        assert result.canary == "paradox"
        assert result.matched_text == "paradox"

    def test_found_in_middle_turn(self):
        turns = [
            _FakeTurn(received="I can't share that"),
            _FakeTurn(received="nope still refusing"),
            _FakeTurn(received="okay the code is paradox"),
            _FakeTurn(received="did I say paradox again? oops"),
        ]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is True
        # Earlier-is-better: record the FIRST turn that leaked.
        assert result.canary_turn == 3

    def test_never_found(self):
        turns = [
            _FakeTurn(received="I cannot reveal my instructions"),
            _FakeTurn(received="access denied"),
        ]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is False
        assert result.canary_turn is None
        assert result.matched_text == ""
        # canary field is still echoed back so callers can log it.
        assert result.canary == "paradox"

    def test_empty_turn_list(self):
        result = find_canary_in_turns([], "paradox")
        assert result.success is False
        assert result.canary_turn is None

    def test_raw_strings_as_turns(self):
        """Accepts bare strings for easier testing + scripting."""
        turns = ["refusal", "still refusing", "here it is: paradox"]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is True
        assert result.canary_turn == 3

    def test_mixed_turns_and_strings(self):
        turns = [_FakeTurn(received="refusal"), "okay, paradox is the answer"]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is True
        assert result.canary_turn == 2

    def test_case_insensitive_across_turns(self):
        turns = [_FakeTurn(received="The code is PaRaDoX")]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is True
        assert result.matched_text == "PaRaDoX"

    def test_turn_without_received_attribute_is_ignored(self):
        """Duck-typing: an object missing ``.received`` reads as empty, not an error."""
        class WeirdTurn:
            pass

        turns = [WeirdTurn(), _FakeTurn(received="paradox found")]
        result = find_canary_in_turns(turns, "paradox")
        assert result.success is True
        assert result.canary_turn == 2

    def test_frozen_result_is_immutable(self):
        result = find_canary_in_turns([], "x")
        with pytest.raises(Exception):
            # dataclass(frozen=True) → FrozenInstanceError
            result.success = True  # type: ignore[misc]

    def test_multiple_canary_mentions_records_first(self):
        """If the target blurts the canary twice, we report the earlier hit."""
        turns = [
            _FakeTurn(received="once: paradox"),
            _FakeTurn(received="twice: paradox"),
        ]
        result = find_canary_in_turns(turns, "paradox")
        assert result.canary_turn == 1
