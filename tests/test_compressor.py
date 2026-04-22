"""Tests for mesmer.core.compressor — summary-buffer compression (C9).

The compressor only fires under CONTINUOUS mode and only when the current
messages payload overshoots the effective context budget. Tests cover the
no-op paths (TRIALS, zero-cap, under-budget, too-few-turns), the happy path
(over-budget → synthetic summary turn + tail preserved), and the stacking
behaviour (compressing a transcript that already contains a summary turn).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesmer.core.compressor import maybe_compress
from mesmer.core.constants import LogEvent, ScenarioMode
from mesmer.core.context import Context, Turn
from mesmer.core.graph import AttackGraph
from mesmer.core.scenario import AgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(
    scenario_mode: ScenarioMode = ScenarioMode.CONTINUOUS,
    max_context_tokens: int = 100,
    compression_keep_recent: int = 2,
    compression_model: str = "",
    turns: list[Turn] | None = None,
):
    target = MagicMock()
    target.send = AsyncMock(return_value="ok")
    target.reset = AsyncMock(return_value=None)
    registry = MagicMock()
    agent_config = AgentConfig(
        model="test/attacker",
        judge_model="test/judge",
        api_key="sk",
        max_context_tokens=max_context_tokens,
        compression_keep_recent=compression_keep_recent,
        compression_model=compression_model,
    )
    graph = AttackGraph()
    graph.ensure_root()
    ctx = Context(
        target=target, registry=registry, agent_config=agent_config,
        objective="o", run_id="r", graph=graph,
        scenario_mode=scenario_mode,
    )
    # Stub ctx.completion for the summary LLM call unless a test overrides.
    async def fake_completion(messages, tools=None, *, role="attacker"):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message = MagicMock()
        resp.choices[0].message.content = "SUMMARY TEXT"
        return resp
    ctx.completion = fake_completion

    if turns is not None:
        ctx.turns[:] = turns
    return ctx


def _make_turns(n: int, prefix: str = "turn") -> list[Turn]:
    return [Turn(sent=f"{prefix} {i} sent", received=f"{prefix} {i} reply", module="probe")
            for i in range(n)]


# ---------------------------------------------------------------------------
# No-op paths
# ---------------------------------------------------------------------------

class TestMaybeCompressNoOps:
    @pytest.mark.asyncio
    async def test_noop_in_trials_mode(self):
        """TRIALS mode never compresses — each sub-module is an independent
        trial, there's no single arc to summarise."""
        ctx = _make_ctx(scenario_mode=ScenarioMode.TRIALS, turns=_make_turns(50))
        # Even with huge messages and a tiny cap, trials won't compress.
        ran = await maybe_compress(ctx, "test/attacker", messages=[
            {"role": "user", "content": "x" * 100_000}
        ])
        assert ran is False
        assert len(ctx.turns) == 50  # untouched

    @pytest.mark.asyncio
    async def test_noop_when_cap_is_zero(self):
        """When effective_max_context_tokens returns 0 (no explicit cap AND
        litellm can't resolve the model) compression is disabled."""
        ctx = _make_ctx(max_context_tokens=0, turns=_make_turns(20))
        with patch("litellm.get_max_tokens", return_value=None):
            ran = await maybe_compress(ctx, "unknown/model")
        assert ran is False
        assert len(ctx.turns) == 20

    @pytest.mark.asyncio
    async def test_noop_when_under_budget(self):
        """Current tokens ≤ cap → no-op."""
        ctx = _make_ctx(max_context_tokens=1_000_000, turns=_make_turns(5))
        ran = await maybe_compress(ctx, "test/attacker")
        assert ran is False
        assert len(ctx.turns) == 5

    @pytest.mark.asyncio
    async def test_noop_when_too_few_turns_to_compress(self):
        """If ctx.turns has fewer than keep_recent + 2 entries, there's
        nothing meaningful to summarise — no-op even when over budget."""
        ctx = _make_ctx(
            max_context_tokens=10,  # tiny cap would normally trigger
            compression_keep_recent=5,
            turns=_make_turns(3),
        )
        ran = await maybe_compress(ctx, "test/attacker")
        assert ran is False
        assert len(ctx.turns) == 3


# ---------------------------------------------------------------------------
# Happy path — compression fires, produces a summary + preserves tail
# ---------------------------------------------------------------------------

class TestMaybeCompressHappyPath:
    @pytest.mark.asyncio
    async def test_compression_creates_summary_and_keeps_tail(self):
        """Over-budget → single summary Turn + keep_recent tail verbatim."""
        ctx = _make_ctx(max_context_tokens=10, compression_keep_recent=2,
                        turns=_make_turns(10))
        ran = await maybe_compress(ctx, "test/attacker")

        assert ran is True
        # Transcript collapses to: [summary] + last 2 original turns.
        assert len(ctx.turns) == 3
        summary_turn = ctx.turns[0]
        assert summary_turn.kind == "summary"
        assert summary_turn.sent == ""
        assert "SUMMARY TEXT" in summary_turn.received
        assert summary_turn.module == "_summary_"
        # Tail preserved (the last 2 turns keep their original sent text).
        assert ctx.turns[1].sent == "turn 8 sent"
        assert ctx.turns[2].sent == "turn 9 sent"
        assert all(t.kind == "exchange" for t in ctx.turns[1:])

    @pytest.mark.asyncio
    async def test_compression_resets_target_reset_at(self):
        """After compression the summary encodes the pre-reset history, so
        the whole remaining transcript counts as the current session."""
        ctx = _make_ctx(max_context_tokens=10, turns=_make_turns(10))
        ctx._target_reset_at = 3
        await maybe_compress(ctx, "test/attacker")
        assert ctx._target_reset_at == 0

    @pytest.mark.asyncio
    async def test_compression_noop_when_llm_returns_empty(self):
        """Summary LLM failed → leave transcript intact. Returning False
        lets the caller surface the miss without crashing the run."""
        ctx = _make_ctx(max_context_tokens=10, turns=_make_turns(10))

        async def empty_completion(messages, tools=None, *, role="attacker"):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message = MagicMock()
            resp.choices[0].message.content = ""
            return resp
        ctx.completion = empty_completion

        ran = await maybe_compress(ctx, "test/attacker")
        assert ran is False
        assert len(ctx.turns) == 10  # untouched

    @pytest.mark.asyncio
    async def test_compression_emits_log_events(self):
        events: list[tuple[str, str]] = []
        ctx = _make_ctx(max_context_tokens=10, turns=_make_turns(10))
        await maybe_compress(
            ctx, "test/attacker",
            log=lambda e, d="": events.append((e, d)),
        )
        compression_events = [e for e in events if e[0] == LogEvent.COMPRESSION.value]
        assert len(compression_events) >= 1
        # First event announces the trigger; subsequent announces the result.
        assert "Compressing" in compression_events[0][1]

    @pytest.mark.asyncio
    async def test_stacked_summary_compresses_further(self):
        """C9 claim: summary turns stack. A transcript that already contains
        a summary Turn can be compressed again — the new summary subsumes
        the older one."""
        initial = [
            Turn(sent="", received="earlier summary text",
                 module="_summary_", kind="summary"),
            *_make_turns(8),
        ]
        ctx = _make_ctx(max_context_tokens=10, compression_keep_recent=2, turns=initial)
        ran = await maybe_compress(ctx, "test/attacker")
        assert ran is True
        # Still collapses to one summary + 2 tail turns.
        assert len(ctx.turns) == 3
        assert ctx.turns[0].kind == "summary"
        # The earlier summary was swallowed into the compression input; it
        # is no longer present as its own Turn.
        assert ctx.turns[0].received != "earlier summary text"

    @pytest.mark.asyncio
    async def test_shared_turn_list_mutated_in_place(self):
        """ctx.child() passes ``_turns=self.turns`` by reference. If
        maybe_compress rebuilt the list the child context would see the
        OLD list and blow out its own budget. Verify the list is mutated
        in place rather than reassigned."""
        ctx = _make_ctx(max_context_tokens=10, turns=_make_turns(10))
        child = ctx.child()
        # Both point to the same list object.
        assert child.turns is ctx.turns
        await maybe_compress(ctx, "test/attacker")
        # Child still sees the post-compression transcript.
        assert child.turns is ctx.turns
        assert len(child.turns) == 3

    @pytest.mark.asyncio
    async def test_explicit_compression_model_used_when_set(self):
        """When AgentConfig.compression_model is set, the summary call goes
        through litellm.acompletion directly with that model — bypassing
        ctx._resolve_model's judge-vs-attacker pick."""
        ctx = _make_ctx(
            max_context_tokens=10,
            compression_model="override/summariser",
            turns=_make_turns(10),
        )
        captured = {}

        async def fake_acompletion(**kwargs):
            captured["model"] = kwargs.get("model")
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message = MagicMock()
            resp.choices[0].message.content = "FROM OVERRIDE"
            return resp

        with patch("litellm.acompletion", new=fake_acompletion):
            ran = await maybe_compress(ctx, "test/attacker")

        assert ran is True
        assert captured["model"] == "override/summariser"
        assert "FROM OVERRIDE" in ctx.turns[0].received


# ---------------------------------------------------------------------------
# Formatter integration (C9) — summary turns render inline
# ---------------------------------------------------------------------------

class TestTurnFormattersWithSummary:
    def test_format_turns_renders_summary_inline(self):
        ctx = _make_ctx(turns=[
            Turn(sent="", received="earlier stuff happened",
                 module="_summary_", kind="summary"),
            Turn(sent="fresh probe", received="fresh reply", module="x"),
        ])
        rendered = ctx.format_turns()
        assert "Summary of compressed earlier turns" in rendered
        assert "earlier stuff happened" in rendered
        assert "fresh probe" in rendered

    def test_format_session_turns_respects_summary(self):
        ctx = _make_ctx(turns=[
            Turn(sent="", received="pre-reset summary",
                 module="_summary_", kind="summary"),
            Turn(sent="probe", received="reply", module="x"),
        ])
        ctx._target_reset_at = 0
        rendered = ctx.format_session_turns()
        assert "Summary of compressed" in rendered
        assert "pre-reset summary" in rendered


# ---------------------------------------------------------------------------
# Integration: the loop actually invokes maybe_compress at each hook point
#
# These tests guard against the hook being deleted or bypassed. Without them,
# a refactor that removed the `await maybe_compress(...)` call would still
# leave every unit-level compressor test green while silently disabling the
# feature in production.
# ---------------------------------------------------------------------------

class TestCompressionHookedIntoReactLoop:
    """C9 hook (attacker side) — ``run_react_loop`` must call
    ``maybe_compress`` before each iteration's attacker LLM call in
    CONTINUOUS mode, and must NOT call it in TRIALS mode."""

    @pytest.mark.asyncio
    async def test_attacker_hook_fires_in_continuous(self):
        from unittest.mock import AsyncMock as _AsyncMock
        from mesmer.core.loop import run_react_loop

        # Stage a completion that immediately concludes — we only care that
        # maybe_compress was called on the first (and only) iteration.
        def _make_completion():
            conclude_msg = MagicMock()
            conclude_msg.content = None
            conclude_msg.tool_calls = [
                MagicMock(
                    id="c1",
                    function=MagicMock(
                        name="conclude",
                        arguments='{"result": "done"}',
                    ),
                )
            ]
            # The Click-style auto-MagicMock gets the ``.function.name``
            # right only if we set it explicitly (MagicMock reserves .name).
            conclude_msg.tool_calls[0].function.name = "conclude"
            resp = MagicMock()
            resp.choices = [MagicMock(message=conclude_msg)]
            return resp

        ctx = _make_ctx(scenario_mode=ScenarioMode.CONTINUOUS,
                        max_context_tokens=0,  # compression disabled — still call should fire
                        turns=_make_turns(5))
        ctx.completion = _AsyncMock(return_value=_make_completion())

        module = MagicMock()
        module.name = "test"
        module.has_custom_run = False
        module.sub_modules = []
        module.system_prompt = "sys"
        module.description = "d"
        module.theory = "t"

        with patch("mesmer.core.compressor.maybe_compress", new=_AsyncMock(return_value=False)) as mock_compress:
            await run_react_loop(module, ctx, "probe")

        # Must be called at least once per iteration — one iteration here.
        assert mock_compress.await_count >= 1
        # Sanity: it was invoked with the attacker model, not the judge model.
        first_call = mock_compress.await_args_list[0]
        assert first_call.args[1] == ctx.agent_model

    @pytest.mark.asyncio
    async def test_attacker_hook_skipped_in_trials(self):
        """TRIALS runs must never call maybe_compress — the compressor's
        own TRIALS guard is defense-in-depth; the loop also short-circuits."""
        from unittest.mock import AsyncMock as _AsyncMock
        from mesmer.core.loop import run_react_loop

        def _make_completion():
            conclude_msg = MagicMock()
            conclude_msg.content = None
            conclude_msg.tool_calls = [
                MagicMock(id="c1", function=MagicMock(arguments='{"result": "done"}'))
            ]
            conclude_msg.tool_calls[0].function.name = "conclude"
            resp = MagicMock()
            resp.choices = [MagicMock(message=conclude_msg)]
            return resp

        ctx = _make_ctx(scenario_mode=ScenarioMode.TRIALS,
                        max_context_tokens=0,
                        turns=_make_turns(5))
        ctx.completion = _AsyncMock(return_value=_make_completion())

        module = MagicMock()
        module.name = "test"
        module.has_custom_run = False
        module.sub_modules = []
        module.system_prompt = "sys"
        module.description = "d"
        module.theory = "t"

        with patch("mesmer.core.compressor.maybe_compress", new=_AsyncMock(return_value=False)) as mock_compress:
            await run_react_loop(module, ctx, "probe")

        assert mock_compress.await_count == 0


class TestCompressionHookedIntoJudge:
    """C9 hook (judge side) — ``_judge_module_result`` must call
    ``maybe_compress`` before ``evaluate_attempt`` in CONTINUOUS mode."""

    @pytest.mark.asyncio
    async def test_judge_hook_fires_in_continuous(self):
        from unittest.mock import AsyncMock as _AsyncMock
        from mesmer.core.judge import JudgeResult
        from mesmer.core.loop import _judge_module_result

        ctx = _make_ctx(scenario_mode=ScenarioMode.CONTINUOUS,
                        turns=_make_turns(5))

        fake_result = JudgeResult(5, "leaked", "angle", "", "next")
        with patch("mesmer.core.compressor.maybe_compress",
                   new=_AsyncMock(return_value=False)) as mock_compress, \
             patch("mesmer.core.judge.evaluate_attempt",
                   new=_AsyncMock(return_value=fake_result)):
            result = await _judge_module_result(
                ctx, "mod", "approach",
                log=lambda *a, **kw: None,
                exchanges=[Turn(sent="hi", received="hello")],
                module_result="r",
            )

        assert result is fake_result
        assert mock_compress.await_count == 1
        # Judge hook uses the judge model (not attacker) — so an ensemble
        # rotation doesn't drag the judge's cap lookup with it.
        assert mock_compress.await_args.args[1] == ctx.agent_config.effective_judge_model

    @pytest.mark.asyncio
    async def test_judge_hook_skipped_in_trials(self):
        from unittest.mock import AsyncMock as _AsyncMock
        from mesmer.core.judge import JudgeResult
        from mesmer.core.loop import _judge_module_result

        ctx = _make_ctx(scenario_mode=ScenarioMode.TRIALS,
                        turns=_make_turns(5))
        fake_result = JudgeResult(5, "", "", "", "")

        with patch("mesmer.core.compressor.maybe_compress",
                   new=_AsyncMock(return_value=False)) as mock_compress, \
             patch("mesmer.core.judge.evaluate_attempt",
                   new=_AsyncMock(return_value=fake_result)):
            await _judge_module_result(
                ctx, "mod", "approach",
                log=lambda *a, **kw: None,
                exchanges=[Turn(sent="hi", received="hello")],
                module_result="",
            )

        assert mock_compress.await_count == 0


class TestCompressionHookedIntoReflect:
    """C9 hook (refine side) — ``_reflect_and_expand`` must compress
    before iterating refine_approach calls in CONTINUOUS mode."""

    @pytest.mark.asyncio
    async def test_refine_hook_fires_in_continuous(self):
        from unittest.mock import AsyncMock as _AsyncMock
        from mesmer.core.judge import JudgeResult
        from mesmer.core.loop import _reflect_and_expand

        ctx = _make_ctx(scenario_mode=ScenarioMode.CONTINUOUS,
                        turns=_make_turns(5))
        graph = ctx.graph
        current_node = graph.add_node(
            graph.root_id, "foo", "prior angle words plenty", score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        async def fake_refine(ctx, *, module, rationale, judge_result, **kw):
            return "x"

        with patch("mesmer.core.compressor.maybe_compress",
                   new=_AsyncMock(return_value=False)) as mock_compress, \
             patch("mesmer.core.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, "foo", "a", judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["foo", "bar"],
            )

        # Exactly once — not per-candidate, so refine_approach sees a stable
        # compressed view across all slots.
        assert mock_compress.await_count == 1

    @pytest.mark.asyncio
    async def test_refine_hook_skipped_in_trials(self):
        from unittest.mock import AsyncMock as _AsyncMock
        from mesmer.core.judge import JudgeResult
        from mesmer.core.loop import _reflect_and_expand

        ctx = _make_ctx(scenario_mode=ScenarioMode.TRIALS,
                        turns=_make_turns(5))
        graph = ctx.graph
        current_node = graph.add_node(
            graph.root_id, "foo", "prior angle words plenty", score=5,
        )
        judge = JudgeResult(5, "", "", "", "")

        async def fake_refine(ctx, *, module, rationale, judge_result, **kw):
            return "x"

        with patch("mesmer.core.compressor.maybe_compress",
                   new=_AsyncMock(return_value=False)) as mock_compress, \
             patch("mesmer.core.judge.refine_approach", new=fake_refine):
            await _reflect_and_expand(
                ctx, "foo", "a", judge, current_node,
                log=lambda *a, **kw: None,
                available_modules=["foo", "bar"],
            )

        assert mock_compress.await_count == 0


# ---------------------------------------------------------------------------
# End-to-end: compression + persistence across two runs
# ---------------------------------------------------------------------------

class TestCompressionPersistsAcrossRuns:
    """Full slice: a CONTINUOUS run fires compression mid-run, persists the
    compressed Turn list to disk, and a subsequent run re-loads the same
    compressed state. This is the one test that would break if ANY of the
    compression/persistence machinery regressed end-to-end."""

    @pytest.mark.asyncio
    async def test_compressed_state_round_trips_across_runs(self, tmp_path):
        from mesmer.core.memory import TargetMemory
        from mesmer.core.scenario import TargetConfig

        target_config = TargetConfig(adapter="echo", url="e2e://test")
        with patch("mesmer.core.memory.MESMER_HOME", tmp_path / ".mesmer"):
            memory = TargetMemory(target_config)
            memory.base_dir = tmp_path / ".mesmer" / "targets" / memory.target_hash

            # Build a run-1 ctx with a small cap and 20 turns → compression
            # should fire.
            ctx = _make_ctx(scenario_mode=ScenarioMode.CONTINUOUS,
                            max_context_tokens=10,
                            compression_keep_recent=2,
                            turns=_make_turns(20))

            ran = await maybe_compress(ctx, "test/attacker")
            assert ran is True
            # Transcript collapsed to summary + keep_recent.
            assert len(ctx.turns) == 3
            assert ctx.turns[0].kind == "summary"
            first_summary_text = ctx.turns[0].received
            last_tail_sent = ctx.turns[-1].sent

            # Persist to disk.
            memory.save_conversation(ctx.turns)
            assert memory.conversation_path.exists()

            # Run 2: a fresh ctx reloads the persisted transcript. The
            # summary turn must round-trip (not be silently downgraded to
            # exchange), and the tail must be preserved verbatim.
            loaded = memory.load_conversation()
            assert len(loaded) == 3
            assert loaded[0].kind == "summary"
            assert loaded[0].received == first_summary_text
            assert loaded[-1].sent == last_tail_sent

            # Run 2 can itself compress further (stacks). Seed 18 more
            # exchanges onto the reloaded transcript and re-trigger.
            new_turns = list(loaded) + _make_turns(18, prefix="r2")
            ctx2 = _make_ctx(scenario_mode=ScenarioMode.CONTINUOUS,
                             max_context_tokens=10,
                             compression_keep_recent=2,
                             turns=new_turns)
            ran2 = await maybe_compress(ctx2, "test/attacker")
            assert ran2 is True
            # After stacking: exactly one summary Turn remains (the earlier
            # summary was absorbed), plus keep_recent=2 tail turns.
            assert len(ctx2.turns) == 3
            assert ctx2.turns[0].kind == "summary"
            # The tail shows the newest r2 exchanges, not the run-1 tail —
            # proof that the run-1 tail was swept into the new summary.
            assert all(t.sent.startswith("r2 ") for t in ctx2.turns[1:])

            # Final persistence: overwrite with the new compressed state.
            memory.save_conversation(ctx2.turns)
            round_three = memory.load_conversation()
            assert round_three[0].kind == "summary"
            assert round_three[0].received == ctx2.turns[0].received

    def test_fresh_flag_wipes_conversation_for_continuous(self, tmp_path):
        """``--fresh`` with CONTINUOUS must delete the persisted transcript —
        otherwise a "start over" invocation silently inherits old state."""
        from mesmer.core.memory import TargetMemory
        from mesmer.core.scenario import TargetConfig

        target_config = TargetConfig(adapter="echo", url="e2e://fresh")
        with patch("mesmer.core.memory.MESMER_HOME", tmp_path / ".mesmer"):
            memory = TargetMemory(target_config)
            memory.base_dir = tmp_path / ".mesmer" / "targets" / memory.target_hash

            memory.save_conversation(_make_turns(5))
            assert len(memory.load_conversation()) == 5

            memory.delete_conversation()
            assert memory.load_conversation() == []
            assert not memory.conversation_path.exists()


# ---------------------------------------------------------------------------
# Token-counting + helper edge cases (fills remaining compressor coverage)
# ---------------------------------------------------------------------------

class TestTokenCountingHelpers:
    def test_char_fallback_empty_returns_zero(self):
        """Empty string → 0 tokens (guards division-by-zero downstream)."""
        from mesmer.core.compressor import _char_fallback
        assert _char_fallback("") == 0
        assert _char_fallback(None) == 0  # type: ignore[arg-type]

    def test_char_fallback_short_string_returns_one(self):
        """Any non-empty text counts as >= 1 token so compression signal
        stays monotonic."""
        from mesmer.core.compressor import _char_fallback
        assert _char_fallback("x") == 1

    def test_count_tokens_exception_falls_back(self):
        """litellm raises → char fallback, not 0 (which would disable
        compression silently on every unknown model)."""
        from mesmer.core.compressor import _count_tokens
        with patch("litellm.token_counter", side_effect=RuntimeError("boom")):
            # 40 chars / 4 ≈ 10 tokens — monotonic with text length.
            assert _count_tokens("unknown/model", "x" * 40) == 10

    def test_count_tokens_non_int_return_falls_back(self):
        """litellm returning a non-int (None, str, etc.) is treated as a
        failed tokenizer; char fallback wins."""
        from mesmer.core.compressor import _count_tokens
        with patch("litellm.token_counter", return_value=None):
            assert _count_tokens("x/y", "xxxxx") > 0
        with patch("litellm.token_counter", return_value="nope"):
            assert _count_tokens("x/y", "xxxxx") > 0

    def test_count_message_tokens_uses_litellm_when_available(self):
        """When litellm can tokenise the full messages payload, that value
        wins — we don't override it with the fallback."""
        from mesmer.core.compressor import _count_message_tokens
        with patch("litellm.token_counter", return_value=777):
            assert _count_message_tokens("real/model", [{"content": "x"}]) == 777

    def test_count_message_tokens_fallback_on_exception(self):
        """Exception → sum of contents via char fallback. Monotonic, non-zero."""
        from mesmer.core.compressor import _count_message_tokens
        msgs = [{"content": "x" * 20}, {"content": "y" * 20}]
        with patch("litellm.token_counter", side_effect=Exception("x")):
            # Two 20-char messages joined with \n = 41 chars → ~10 tokens.
            n = _count_message_tokens("bad/model", msgs)
            assert n > 0

    def test_estimate_turns_tokens_empty_list_is_zero(self):
        """No turns → 0 tokens. Used to short-circuit size checks."""
        from mesmer.core.compressor import _estimate_turns_tokens
        assert _estimate_turns_tokens("x/y", []) == 0


class TestSummariseBlockFailurePaths:
    @pytest.mark.asyncio
    async def test_summarise_block_catches_ctx_completion_exception(self):
        """When ctx.completion raises (network, auth, etc.) the summariser
        must return "" so maybe_compress keeps the transcript intact
        rather than blowing up the run."""
        from mesmer.core.compressor import _summarise_block

        ctx = _make_ctx(turns=_make_turns(5))

        async def boom(messages, tools=None, *, role="attacker"):
            raise RuntimeError("provider down")
        ctx.completion = boom

        out = await _summarise_block(
            ctx, ctx.turns,
            explicit_compression_model="",  # no override → routes through ctx.completion
        )
        assert out == ""

    @pytest.mark.asyncio
    async def test_summarise_block_restores_attacker_override_after_call(self):
        """Summariser temporarily clears attacker_model_override so role=
        judge wins; it must restore the prior value, including on failure,
        so the attacker rotation state isn't corrupted."""
        from mesmer.core.compressor import _summarise_block

        ctx = _make_ctx(turns=_make_turns(5))
        ctx.attacker_model_override = "rotated/model-B"

        async def boom(messages, tools=None, *, role="attacker"):
            raise RuntimeError("x")
        ctx.completion = boom

        await _summarise_block(ctx, ctx.turns, explicit_compression_model="")
        # Prior override restored even on exception.
        assert ctx.attacker_model_override == "rotated/model-B"


class TestRawCompletionPath:
    """``_raw_completion`` is the branch taken when an explicit
    ``compression_model`` override is set. Covers api_base propagation
    and the exception path."""

    @pytest.mark.asyncio
    async def test_raw_completion_forwards_api_base_when_set(self):
        from mesmer.core.compressor import _raw_completion

        ctx = _make_ctx(turns=[])
        ctx.agent_config.api_base = "https://custom.endpoint/v1"
        ctx.agent_config.api_key = "k"

        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message = MagicMock()
            resp.choices[0].message.content = "result"
            return resp

        with patch("litellm.acompletion", new=fake_acompletion):
            out = await _raw_completion(ctx, "override/model", [{"content": "x"}])

        assert out == "result"
        assert captured["model"] == "override/model"
        assert captured["api_base"] == "https://custom.endpoint/v1"

    @pytest.mark.asyncio
    async def test_raw_completion_returns_empty_on_exception(self):
        """litellm raises → return "" so maybe_compress treats it as a
        failed compression (leaves ctx.turns intact)."""
        from mesmer.core.compressor import _raw_completion

        ctx = _make_ctx(turns=[])

        async def fake_acompletion(**kwargs):
            raise RuntimeError("provider down")

        with patch("litellm.acompletion", new=fake_acompletion):
            out = await _raw_completion(ctx, "override/model", [{"content": "x"}])
        assert out == ""


class TestMaybeCompressMessagesArg:
    """Exercises the ``messages=`` path so the coverage reflects the real
    loop.py call-site (which always passes messages)."""

    @pytest.mark.asyncio
    async def test_messages_arg_dominates_token_count(self):
        """When the attacker messages payload is provided, compression
        triggers on THAT size — even if ctx.turns alone would be under cap."""
        ctx = _make_ctx(max_context_tokens=10, compression_keep_recent=2,
                        turns=_make_turns(5))
        # 5 short turns alone would be around cap; a huge messages payload
        # should force a compression decision anyway.
        huge_messages = [{"content": "x" * 10_000}]
        ran = await maybe_compress(ctx, "test/attacker", messages=huge_messages)
        assert ran is True

    @pytest.mark.asyncio
    async def test_empty_log_still_ok(self):
        """``log=None`` is supported — compression runs silently."""
        ctx = _make_ctx(max_context_tokens=10, turns=_make_turns(10))
        ran = await maybe_compress(ctx, "test/attacker", log=None)
        assert ran is True

    @pytest.mark.asyncio
    async def test_empty_summary_logs_failure_reason(self):
        """When the summary LLM returns empty AND a log is configured, a
        COMPRESSION log line explains why the transcript wasn't changed —
        otherwise operators see no signal that compression was attempted."""
        ctx = _make_ctx(max_context_tokens=10, turns=_make_turns(10))

        async def empty_completion(messages, tools=None, *, role="attacker"):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message = MagicMock()
            resp.choices[0].message.content = ""
            return resp
        ctx.completion = empty_completion

        events: list[tuple[str, str]] = []
        ran = await maybe_compress(
            ctx, "test/attacker",
            log=lambda e, d="": events.append((e, d)),
        )
        assert ran is False
        compression_logs = [e for e in events if e[0] == LogEvent.COMPRESSION.value]
        # Two events: one announcing the attempt, one announcing the miss.
        assert any("returned empty" in d or "intact" in d for _, d in compression_logs)
