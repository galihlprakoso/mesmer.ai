"""Post-delegation pipeline: judge → graph update → reflect+expand.

Called from :func:`mesmer.core.agent.tools.sub_module.handle` after each
sub-module returns. Three phases:

1. :func:`_judge_module_result` — one LLM call scores the exchanges 1-10 and
   extracts insights via :func:`mesmer.core.agent.judge.evaluate_attempt`.
2. :func:`_update_graph` — writes the attempt into the persistent AttackGraph
   using TAP-aligned parent semantics (fulfill existing frontier or attach
   under root / latest-explored leaf depending on scenario mode).
3. :func:`_reflect_and_expand` — graph proposes next-move candidates
   deterministically (:meth:`AttackGraph.propose_frontier`), then LLM writes
   approach one-liners via :func:`mesmer.core.agent.judge.refine_approach`.

The deep imports of ``evaluate_attempt``, ``refine_approach`` and
``maybe_compress`` are intentionally lazy (inside functions) so that tests
can ``mock.patch`` them at module load time and have the patch take effect
when this pipeline runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mesmer.core.agent.context import Turn
from mesmer.core.constants import LogEvent, NodeStatus, ScenarioMode

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.agent.engine import LogFn
    from mesmer.core.graph import AttackNode


def _format_prior_turns_for_judge(prior_turns: list[Turn], last_n: int = 6) -> str:
    """Render a short baseline transcript for the CONTINUOUS-mode judge.

    Returns the last ``last_n`` turns as compact ``[role] text`` lines so
    the judge can tell what was already visible to the target before the
    move under evaluation. Empty input yields empty string (TRIALS mode
    callers simply skip the section).
    """
    if not prior_turns:
        return ""
    lines: list[str] = []
    for t in prior_turns[-last_n:]:
        prefix = f"[{t.module}] " if t.module else ""
        sent = t.sent.strip()
        received = t.received.strip()
        if sent:
            lines.append(f"{prefix}Attacker: {sent}")
        if received:
            lines.append(f"Target: {received}")
    return "\n".join(lines)


async def _judge_module_result(
    ctx: Context,
    module_name: str,
    approach: str,
    log: LogFn,
    *,
    exchanges: list[Turn] | None = None,
    module_result: str = "",
):
    """Run the judge on the exchanges produced during a sub-module.

    ``exchanges`` is the slice of ``ctx.turns`` added by the delegated
    sub-module. Passing Turn objects directly preserves the ``is_error``
    flag (P4) without a parallel side-list.

    ``module_result`` is the sub-module's ``conclude()`` text. For modules
    whose artifact lives in the conclude (e.g. safety-profiler's defense
    profile), the probe messages alone are insufficient to score — the
    judge also needs the summary the module produced.

    In CONTINUOUS mode the judge additionally receives a compact
    ``prior_transcript_summary`` so it can score this move on DELTA leaks
    (new evidence) instead of absolute visible information.
    """
    turns = exchanges or []

    if not turns:
        log(LogEvent.JUDGE.value, f"Skipping judge — no messages exchanged in {module_name}")
        return None

    # Lazy import so ``patch("mesmer.core.agent.judge.evaluate_attempt", ...)``
    # in tests takes effect when we call it below.
    from mesmer.core.agent.judge import evaluate_attempt

    # Look up the judged module's own rubric (if any) so the judge scores
    # the attempt with technique-aware criteria, not just extraction floor.
    module = ctx.registry.get(module_name) if ctx.registry else None
    module_rubric = module.judge_rubric if module else ""

    # CONTINUOUS: build a compact baseline transcript of what was visible
    # BEFORE this move started. ctx.turns currently ends with ``turns``
    # (the sub-module's exchanges), so prior = everything before that tail.
    prior_transcript_summary = ""
    if ctx.scenario_mode == ScenarioMode.CONTINUOUS and len(ctx.turns) >= len(turns):
        prior = ctx.turns[: len(ctx.turns) - len(turns)]
        prior_transcript_summary = _format_prior_turns_for_judge(prior, last_n=6)

    err_count = sum(1 for t in turns if t.is_error)
    log(
        LogEvent.JUDGE.value,
        f"Evaluating {module_name} ({len(turns)} messages"
        + (f", {err_count} pipeline errors" if err_count else "")
        + ")..."
    )

    # C9 — compress before the judge call too. Judge prompts carry the full
    # prior_transcript_summary + exchanges + module_rubric; in a long arc
    # those can overshoot the judge model's window just like the attacker's.
    # Uses the judge model for the cap lookup so the threshold matches the
    # model that's actually going to receive the prompt.
    if ctx.scenario_mode == ScenarioMode.CONTINUOUS:
        from mesmer.core.agent.compressor import maybe_compress
        await maybe_compress(ctx, ctx.agent_config.effective_judge_model, log=log)

    try:
        result = await evaluate_attempt(
            ctx,
            module_name=module_name,
            approach=approach,
            exchanges=turns,
            module_rubric=module_rubric,
            module_result=module_result,
            prior_transcript_summary=prior_transcript_summary,
        )
        log(LogEvent.JUDGE_SCORE.value, f"Score: {result.score}/10 — {result.leaked_info}")
        return result
    except Exception as e:
        log(LogEvent.JUDGE_ERROR.value, f"Judge failed: {e}")
        return None


def _update_graph(
    ctx: Context,
    module_name: str,
    approach: str,
    judge_result,
    log: LogFn,
    *,
    messages_sent: list[str] | None = None,
    target_responses: list[str] | None = None,
    frontier_id: str | None = None,
) -> AttackNode | None:
    """Record an explored attempt in the attack graph.

    TAP-aligned parent semantics ([Mehrotra et al. 2023](
    https://arxiv.org/abs/2312.02119)):

      - If `frontier_id` names a real frontier node, fulfill it — the edge
        parent→node literally means "child was proposed by reflecting on
        parent's result."
      - Otherwise (fresh attempt, no frontier_id):
        * ``TRIALS``: new node attaches as a direct child of root — each
          trial is an independent rollout sibling.
        * ``CONTINUOUS``: new node attaches under the latest explored node
          (the live chain's leaf) — the graph is a path of moves in one
          conversation, not a fan of trials. Falls back to root when no
          explored node exists yet.

    No more "best-same-module" heuristic — that fabricated edges that had no
    causal relationship to the data.
    """
    graph = ctx.graph
    if not graph:
        return None

    msgs = messages_sent or []
    resps = target_responses or []

    score = judge_result.score if judge_result else 3
    leaked = judge_result.leaked_info if judge_result else ""
    reflection = ""
    if judge_result:
        reflection = (
            f"Score {score}/10. "
            f"Promising: {judge_result.promising_angle}. "
            f"Dead end: {judge_result.dead_end}."
        )

    node = None

    # Case 1: leader is executing a specific frontier suggestion → fulfill it
    if frontier_id:
        existing = graph.get(frontier_id)
        if existing and existing.is_frontier:
            # Pass module=module_name so the fulfilled node reflects the
            # sub-module the leader actually called, not whatever was stored
            # on the frontier when it was generated.
            node = graph.fulfill_frontier(
                frontier_id,
                approach=approach,
                messages_sent=msgs,
                target_responses=resps,
                score=score,
                leaked_info=leaked,
                reflection=reflection,
                run_id=ctx.run_id,
                module=module_name,
            )
        else:
            log(
                LogEvent.GRAPH_UPDATE.value,
                f"frontier_id={frontier_id} unknown or already explored — "
                f"falling back to fresh attempt"
            )

    # Case 2: fresh attempt. TRIALS → child of root; CONTINUOUS → child of
    # the live chain's leaf so sibling moves don't fan out from root.
    if node is None:
        root = graph.ensure_root()
        attach_id = root.id
        if ctx.scenario_mode == ScenarioMode.CONTINUOUS:
            leaf = graph.latest_explored_node()
            if leaf is not None:
                attach_id = leaf.id
        node = graph.add_node(
            parent_id=attach_id,
            module=module_name,
            approach=approach,
            messages_sent=msgs,
            target_responses=resps,
            score=score,
            leaked_info=leaked,
            reflection=reflection,
            run_id=ctx.run_id,
        )

    status_icon = {
        NodeStatus.DEAD.value: "✗",
        NodeStatus.PROMISING.value: "★",
        NodeStatus.ALIVE.value: "·",
    }.get(node.status, "·")
    log(
        LogEvent.GRAPH_UPDATE.value,
        f"{status_icon} [{node.module}→{node.approach[:60]}] "
        f"score:{node.score} status:{node.status}"
    )
    return node


async def _reflect_and_expand(
    ctx: Context,
    module_name: str,
    approach: str,
    judge_result,
    current_node: AttackNode,
    log: LogFn,
    *,
    available_modules: list[str] | None = None,
) -> None:
    """Expand the frontier using the graph-first (P2) flow.

    Phase 1 — :meth:`AttackGraph.propose_frontier` deterministically ranks the
    available modules and hands back the ``top_k`` slots to expand. Untried
    arms come first; modules whose every prior attempt is dead are filtered
    out. The LLM is not involved.

    Phase 2 — :func:`mesmer.core.agent.judge.refine_approach` writes a one-line
    approach for each already-selected module, grounded in the latest judge
    result. The LLM never sees a menu of modules, so it can no longer
    re-suggest techniques the graph already excluded.

    ``available_modules`` defaults to the leader's direct sub-modules if not
    given. A leader that advertised no sub-modules cannot expand the frontier.
    """
    graph = ctx.graph
    if not graph:
        return

    # Only generate frontier for non-dead nodes
    if current_node.is_dead:
        log(LogEvent.GRAPH_UPDATE.value, "Node is dead — no frontier expansion")
        return

    # Fall back to "everything in the registry" if the caller didn't scope.
    # This matches the pre-P2 behaviour for callers that haven't been updated.
    if available_modules is None:
        available_modules = list(ctx.registry.modules.keys()) if ctx.registry else []
    if not available_modules:
        return

    try:
        candidates = graph.propose_frontier(
            available_modules,
            parent_id=current_node.id,
            top_k=3,
        )
    except Exception as e:
        log(LogEvent.REFLECT_ERROR.value, f"Frontier proposal failed: {e}")
        return

    if not candidates:
        log(LogEvent.FRONTIER.value, "No candidates — every module is dead or excluded")
        return

    # Lazy import so ``patch("mesmer.core.agent.judge.refine_approach", ...)``
    # in tests takes effect when we call refine_approach below.
    from mesmer.core.agent import judge as _judge_mod

    # CONTINUOUS: compress before building the refinement prompts too —
    # each candidate re-invokes the judge model, so a huge transcript tail
    # would bloat N sequential LLM calls. Compression happens once here,
    # before the tail is captured, so all candidates see the same (compressed)
    # view of the live state.
    if ctx.scenario_mode == ScenarioMode.CONTINUOUS:
        from mesmer.core.agent.compressor import maybe_compress
        await maybe_compress(ctx, ctx.agent_config.effective_judge_model, log=log)

    # CONTINUOUS: refinement LLM sees the live tail so the opener is grounded
    # in the current dialogue state, not just the judge verdict. TRIALS mode
    # passes an empty tail — the refinement prompt then hides that section.
    transcript_tail = ""
    if ctx.scenario_mode == ScenarioMode.CONTINUOUS:
        transcript_tail = ctx.format_session_turns(last_n=8)
        if transcript_tail.startswith("(no conversation"):
            transcript_tail = ""

    for c in candidates:
        mod = c["module"]
        rationale = c["rationale"]
        try:
            approach_text = await _judge_mod.refine_approach(
                ctx,
                module=mod,
                rationale=rationale,
                judge_result=judge_result,
                transcript_tail=transcript_tail,
            )
        except Exception as e:
            log(LogEvent.REFLECT_ERROR.value, f"refine_approach({mod}) failed: {e}")
            continue

        if not approach_text:
            # LLM failed or returned empty — fall back to the rationale as a
            # readable placeholder rather than dropping the slot entirely.
            approach_text = f"{mod}: {rationale}"

        frontier = graph.add_frontier_node(
            parent_id=c["parent_id"],
            module=mod,
            approach=approach_text,
            run_id=ctx.run_id,
        )
        log(
            LogEvent.FRONTIER.value,
            f"🌿 New frontier: {frontier.module}→{frontier.approach} "
            f"[{rationale}]",
        )


__all__ = [
    "_format_prior_turns_for_judge",
    "_judge_module_result",
    "_update_graph",
    "_reflect_and_expand",
]
