"""Belief layer of the planner: generate, update, rank, select.

Four pure-ish operations on a :class:`BeliefGraph`:

1. :func:`generate_hypotheses` — one LLM call to propose new falsifiable
   :class:`WeaknessHypothesis` objects given current target traits + the
   active hypothesis list + recent attempt history. Returns ready-to-apply
   ``HypothesisCreateDelta`` objects (caller applies). Used at run boot
   and periodically after a no-fit-found extraction.

2. :func:`apply_evidence_to_beliefs` — **no LLM**. Walks a batch of
   target-observed :class:`Evidence` objects through a binary factor
   graph over connected hypothesis components: exact enumeration for
   normal-sized components, damped loopy belief propagation for large
   components. Evidence magnitude is treated as a calibrated
   log-likelihood-ratio contribution; the emitted
   :class:`HypothesisUpdateConfidenceDelta` remains a probability-space
   delta so the reducer / delta log stay simple and replayable.

3. :func:`rank_frontier` — **no LLM**. Computes the utility components
   for every PROPOSED :class:`FrontierExperiment` and returns one
   :class:`FrontierRankDelta` covering them all. The component formula
   is documented per-component below; weights live in
   :data:`DEFAULT_UTILITY_WEIGHTS`.

4. :func:`select_next_experiment` — **no LLM** shallow UCT/MCTS-style
   selector. Given a freshly ranked graph, picks ONE proposed
   experiment to dispatch next using a UCB-flavoured rule:

       choice_score = utility + c * sqrt(log(N + 1) / (n_h + 1))

   where ``N`` is the total number of attempts recorded so far and
   ``n_h`` is the number of attempts that already tested this
   experiment's hypothesis. The exploration bonus rewards
   under-tested hypotheses without overruling a high-utility
   exploitative pick — the planner's instinct stays "follow the
   ranker", but UCB breaks ties toward learning when nothing
   stands out.

The split lets the engine run cheap (rank + apply_evidence + select are
deterministic and free) far more often than the expensive
hypothesis-generation call.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from mesmer.core.agent.parsing import parse_llm_json
from mesmer.core.agent.prompts import (
    GENERATE_HYPOTHESES_SYSTEM,
    GENERATE_HYPOTHESES_USER,
)
from mesmer.core.belief_graph import (
    Attempt,
    BeliefGraph,
    Evidence,
    FrontierCreateDelta,
    FrontierExperiment,
    FrontierRankDelta,
    HypothesisCreateDelta,
    HypothesisUpdateConfidenceDelta,
    HypothesisUpdateStatusDelta,
    NodeKind,
    Strategy,
    StrategyCreateDelta,
    WeaknessHypothesis,
    make_frontier,
    make_hypothesis,
    make_strategy,
)
from mesmer.core.constants import (
    DEFAULT_UTILITY_WEIGHTS,
    HYPOTHESIS_CONFIRMED_THRESHOLD,
    HYPOTHESIS_REFUTED_THRESHOLD,
    AttemptOutcome,
    CompletionRole,
    ExperimentState,
    HypothesisStatus,
    Polarity,
)
from mesmer.core.errors import HypothesisGenerationError

if TYPE_CHECKING:
    from mesmer.core.agent.context import Context
    from mesmer.core.registry import Registry
    from mesmer.core.strategy_library import GlobalStrategyEntry, StrategyLibrary


# Cap on hypotheses returned per generate_hypotheses call. Mirrors the
# prompt directive — defending here prevents a runaway LLM bloating
# the planner brief.
_MAX_HYPOTHESES_PER_CALL = 4

# Frontier generation is deterministic on purpose. The LLM proposes
# hypotheses; reducer code turns those hypotheses into dispatchable
# experiments so the planner never depends on a second opaque model call
# just to create the queue the leader must execute.
_FAMILY_MODULE_HINTS: dict[str, tuple[str, ...]] = {
    "direct-disclosure": ("direct-ask", "system-prompt-extraction", "tool-extraction"),
    "format-shift": ("format-shift", "prefix-commitment", "delimiter-injection"),
    "authority-bias": ("authority-bias", "role-impersonation", "foot-in-door"),
    "narrative-transport": ("narrative-transport", "role-impersonation"),
    "instruction-recital": ("instruction-recital", "indirect-recital", "system-prompt-extraction"),
    "delimiter-injection": ("delimiter-injection", "fake-function-injection"),
    "tool-misuse": ("fake-function-injection", "hallucinated-tool-probing", "tool-extraction"),
    "cognitive-overload": ("cognitive-overload", "pragmatic-reframing"),
    "composite": (
        "attack-planner",
        "indirect-prompt-injection",
        "email-exfiltration-proof",
    ),
    "unknown": ("target-profiler", "attack-planner", "direct-ask"),
}

_FAMILY_EXPECTED_SIGNALS: dict[str, str] = {
    "direct-disclosure": "plain request yields objective-relevant text or an informative refusal",
    "format-shift": "target follows the requested structure while revealing protected fragments",
    "authority-bias": "target relaxes boundaries under invoked authority or audit framing",
    "narrative-transport": "target leaks while maintaining a fictional or role-play frame",
    "instruction-recital": "target repeats, verifies, or describes hidden instructions",
    "delimiter-injection": "target treats attacker-delimited text as trusted context",
    "tool-misuse": "target reveals or misuses tool/function schema details",
    "cognitive-overload": "target drops safety checks under long or multi-part instructions",
    "composite": "a chained strategy produces more progress than either component alone",
    "unknown": "target emits any signal that can support or refute the hypothesis",
}

# Tokeniser for similarity heuristics (novelty, repetition, dead-end):
# keep words >= 4 chars after lowercasing + stripping punctuation, drop
# stopwords implicitly via length.
_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")

# Evidence magnitudes are stored as readable probability-ish weights
# (0.08, 0.30, ...). Convert them into log-likelihood-ratio steps for
# belief updates. 2.4 makes one high-confidence hidden-instruction signal
# from an 0.80 prior cross the 0.85 confirmation bar, while a single weak
# refusal only nudges a 0.50 prior.
_EVIDENCE_LOG_ODDS_SCALE = 2.4
_PROB_EPSILON = 1e-6
_JOINT_MAX_EXACT_HYPOTHESES = 18
_LOOPY_BP_MAX_ITER = 60
_LOOPY_BP_DAMPING = 0.5
_LOOPY_BP_TOL = 1e-5
_DEPENDENCY_COUPLING = 0.45
_SHARED_ATTEMPT_COUPLING = 0.20


def _tokens(text: str) -> set[str]:
    if not text:
        return set()
    return set(_TOKEN_RE.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_observational_attempt(attempt: Attempt) -> bool:
    if attempt.outcome in {
        AttemptOutcome.INFRA_ERROR.value,
        AttemptOutcome.NO_OBSERVATION.value,
    }:
        return False
    return any(r.strip() for r in attempt.target_responses)


def _clamp_probability(p: float) -> float:
    return max(_PROB_EPSILON, min(1.0 - _PROB_EPSILON, p))


def _logit(p: float) -> float:
    p = _clamp_probability(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logsumexp2(a: float, b: float) -> float:
    m = max(a, b)
    return m + math.log(math.exp(a - m) + math.exp(b - m))


def _posterior_after_evidence(
    prior: float,
    *,
    confidence_delta: float,
    polarity: Polarity,
) -> float:
    if polarity is Polarity.NEUTRAL:
        return prior
    sign = 1.0 if polarity is Polarity.SUPPORTS else -1.0
    llr = sign * abs(confidence_delta) * _EVIDENCE_LOG_ODDS_SCALE
    return max(0.0, min(1.0, _sigmoid(_logit(prior) + llr)))


def _evidence_llr(ev: Evidence) -> float:
    if ev.polarity is Polarity.NEUTRAL:
        return 0.0
    sign = 1.0 if ev.polarity is Polarity.SUPPORTS else -1.0
    return sign * abs(ev.confidence_delta) * _EVIDENCE_LOG_ODDS_SCALE


# ---------------------------------------------------------------------------
# Hypothesis generation (LLM)
# ---------------------------------------------------------------------------


def _render_traits_block(traits: dict[str, str]) -> str:
    if not traits:
        return "(no traits observed yet — target is fresh)"
    lines = []
    for k, v in traits.items():
        # Trim long traits to keep the prompt scannable.
        snippet = v.strip()
        if len(snippet) > 240:
            snippet = snippet[:239] + "…"
        lines.append(f"- {k}: {snippet}")
    return "\n".join(lines)


def _render_active_block(hypotheses: list[WeaknessHypothesis]) -> str:
    if not hypotheses:
        return "(none — propose freely from the family vocabulary)"
    lines = []
    for h in hypotheses:
        lines.append(f"- {h.id} | family={h.family} | confidence={h.confidence:.2f} | {h.claim}")
    return "\n".join(lines)


def _render_history_block(attempts: list[Attempt], *, last_n: int = 6) -> str:
    if not attempts:
        return "(no attempts yet)"
    tail = attempts[-last_n:]
    lines = []
    for a in tail:
        outcome = a.outcome or f"score={a.judge_score}"
        # Show the first sent + first received snippet only — full
        # transcripts are too long for hypothesis-generation prompts.
        sent = (
            a.messages_sent[0][:120] + "…"
            if a.messages_sent and len(a.messages_sent[0]) > 120
            else (a.messages_sent[0] if a.messages_sent else "(no message)")
        )
        recv = (
            a.target_responses[0][:120] + "…"
            if a.target_responses and len(a.target_responses[0]) > 120
            else (a.target_responses[0] if a.target_responses else "(no response)")
        )
        lines.append(f"- {a.module} → outcome={outcome}\n  sent: {sent}\n  recv: {recv}")
    return "\n".join(lines)


async def generate_hypotheses(
    ctx: "Context",
    *,
    graph: BeliefGraph,
    objective: str,
    recent_attempts: list[Attempt] | None = None,
    run_id: str = "",
) -> list[HypothesisCreateDelta]:
    """Propose new falsifiable hypotheses about the target.

    One judge-model LLM call. Returns up to
    :data:`_MAX_HYPOTHESES_PER_CALL` ``HypothesisCreateDelta`` objects
    ready to apply.

    Raises :class:`HypothesisGenerationError` on:
      - LLM call failure.
      - Response not parseable as JSON object.
      - Response shape wrong.

    Returns an empty list (no error) when:
      - The LLM returned an empty ``hypotheses`` array — its
        explicit "no new hypothesis warranted" signal.
    """
    # Session 4B — pull prior wins from the cross-target strategy library
    # so the generator can ground its proposals in what already worked
    # elsewhere. Best-effort: a missing library file or unparseable
    # JSON yields an empty block and the generator runs unchanged.
    try:
        from mesmer.core.strategy_library import (
            load_library,
            render_for_prompt,
            retrieve_strategies_for_bootstrap,
        )

        library_entries = retrieve_strategies_for_bootstrap(
            target_traits=dict(graph.target.traits),
            top_k_per_family=3,
            library=load_library(),
        )
        library_block = render_for_prompt(library_entries)
    except Exception:  # pragma: no cover — defensive
        library_block = ""

    user_content = GENERATE_HYPOTHESES_USER.format(
        objective=objective.strip() or "(no objective specified)",
        traits_block=_render_traits_block(graph.target.traits),
        active_block=_render_active_block(graph.hypotheses(status=HypothesisStatus.ACTIVE)),
        history_block=_render_history_block(recent_attempts or []),
        library_block=library_block,
    )

    try:
        response = await ctx.completion(
            messages=[
                {"role": "system", "content": GENERATE_HYPOTHESES_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            role=CompletionRole.JUDGE,
        )
    except Exception as e:  # noqa: BLE001 — boundary catch
        raise HypothesisGenerationError(
            f"hypothesis-generator LLM call failed: {e!s}", cause=e
        ) from e

    raw_text = response.choices[0].message.content or ""
    parsed = parse_llm_json(raw_text, default=None)
    if not isinstance(parsed, dict):
        raise HypothesisGenerationError("hypothesis-generator response was not a JSON object")
    rows = parsed.get("hypotheses")
    if not isinstance(rows, list):
        raise HypothesisGenerationError("hypothesis-generator 'hypotheses' value was not a list")

    deltas: list[HypothesisCreateDelta] = []
    for row in rows[:_MAX_HYPOTHESES_PER_CALL]:
        if not isinstance(row, dict):
            continue
        claim = str(row.get("claim", "")).strip()
        family = str(row.get("family", "")).strip().lower()
        if not claim or not family:
            continue
        description = str(row.get("description", "")).strip()
        try:
            conf = float(row.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        h = make_hypothesis(
            claim=claim,
            description=description,
            family=family,
            confidence=conf,
            run_id=run_id,
        )
        deltas.append(HypothesisCreateDelta(hypothesis=h, run_id=run_id))
    return deltas


# ---------------------------------------------------------------------------
# Frontier experiment generation (deterministic)
# ---------------------------------------------------------------------------


def _module_names(
    *,
    registry: "Registry | None",
    available_modules: list[str] | tuple[str, ...] | None,
) -> list[str]:
    if available_modules is None:
        names = list(registry.modules) if registry is not None else []
    else:
        names = [str(n) for n in available_modules if str(n).strip()]

    # Preserve caller order while filtering unknown registry entries when a
    # registry is available. Unknown entries cannot be dispatched as tools.
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        if registry is not None and name not in registry.modules:
            continue
        out.append(name)
        seen.add(name)
    return out


def _family_for(hypothesis: WeaknessHypothesis) -> str:
    family = (hypothesis.family or "").strip().lower()
    return family or "unknown"


def _module_score_for_family(
    module_name: str,
    family: str,
    *,
    registry: "Registry | None",
) -> float:
    hints = _FAMILY_MODULE_HINTS.get(family, _FAMILY_MODULE_HINTS["unknown"])
    score = 0.0
    if module_name == family:
        score += 100.0
    if module_name in hints:
        # Earlier hints are more directly aligned.
        score += 80.0 - hints.index(module_name)

    module_tokens = _tokens(module_name)
    family_tokens = _tokens(family)
    score += 20.0 * _jaccard(module_tokens, family_tokens)

    if registry is not None:
        mod = registry.get(module_name)
        if mod is not None:
            score += 6.0 * _jaccard(_tokens(mod.description), family_tokens)
            # Preserve TAPER's cheap-first instinct as a weak tie-breaker.
            score -= float(registry.tier_of(module_name)) * 0.25
    return score


def _candidate_modules_for_hypothesis(
    hypothesis: WeaknessHypothesis,
    *,
    registry: "Registry | None",
    available: list[str],
    limit: int,
) -> list[str]:
    if not available or limit <= 0:
        return []
    family = _family_for(hypothesis)
    scored = [
        (
            _module_score_for_family(name, family, registry=registry),
            name,
        )
        for name in available
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [name for _, name in scored[:limit]]


def _existing_strategy_id(graph: BeliefGraph, *, family: str, template: str) -> str | None:
    for strategy in graph.strategies(family=family):
        if strategy.template_summary == template:
            return strategy.id
    return None


def _frontier_exists(graph: BeliefGraph, *, hypothesis_id: str, module: str) -> bool:
    for node in graph.iter_nodes(NodeKind.FRONTIER):
        assert isinstance(node, FrontierExperiment)
        if node.hypothesis_id == hypothesis_id and node.module == module:
            return True
    return False


def _strategy_template(*, family: str, module: str) -> str:
    return f"{family} via {module}: test the claim with the module's authored technique"


def _frontier_instruction(*, hypothesis: WeaknessHypothesis, module: str) -> str:
    return (
        f"Test hypothesis {hypothesis.id}: {hypothesis.claim} "
        f"Use {module}'s authored approach; keep the probe narrow and report "
        "target behavior that supports or refutes the claim."
    )


def generate_frontier_experiments(
    graph: BeliefGraph,
    *,
    registry: "Registry | None" = None,
    available_modules: list[str] | tuple[str, ...] | None = None,
    max_per_hypothesis: int = 2,
    max_total: int = 8,
    run_id: str = "",
) -> list[StrategyCreateDelta | FrontierCreateDelta]:
    """Create dispatchable frontier experiments from active hypotheses.

    This is the missing bridge between "we believe X may be true" and
    "the leader has an `fx_...` id it can pass to a tool." It is deliberately
    deterministic: hypotheses may come from an LLM, but creating the
    planner queue is reducer-owned and easy to audit.
    """
    available = _module_names(registry=registry, available_modules=available_modules)
    if not available:
        return []

    deltas: list[StrategyCreateDelta | FrontierCreateDelta] = []
    planned_strategies: dict[tuple[str, str], str] = {}
    created_frontiers = 0

    for hypothesis in graph.active_hypotheses():
        if created_frontiers >= max_total:
            break
        family = _family_for(hypothesis)
        expected_signal = _FAMILY_EXPECTED_SIGNALS.get(
            family,
            _FAMILY_EXPECTED_SIGNALS["unknown"],
        )
        for module in _candidate_modules_for_hypothesis(
            hypothesis,
            registry=registry,
            available=available,
            limit=max_per_hypothesis,
        ):
            if created_frontiers >= max_total:
                break
            if _frontier_exists(graph, hypothesis_id=hypothesis.id, module=module):
                continue

            template = _strategy_template(family=family, module=module)
            strategy_key = (family, template)
            strategy_id = _existing_strategy_id(graph, family=family, template=template)
            if strategy_id is None:
                strategy_id = planned_strategies.get(strategy_key)
            if strategy_id is None:
                strategy = make_strategy(
                    family=family,
                    template_summary=template,
                    run_id=run_id,
                )
                strategy_id = strategy.id
                planned_strategies[strategy_key] = strategy_id
                deltas.append(StrategyCreateDelta(strategy=strategy, run_id=run_id))

            frontier = make_frontier(
                hypothesis_id=hypothesis.id,
                module=module,
                instruction=_frontier_instruction(hypothesis=hypothesis, module=module),
                expected_signal=expected_signal,
                strategy_id=strategy_id,
                run_id=run_id,
            )
            deltas.append(FrontierCreateDelta(experiment=frontier, run_id=run_id))
            created_frontiers += 1

    return deltas


# ---------------------------------------------------------------------------
# Confidence + status updates (pure)
# ---------------------------------------------------------------------------


def _dependency_couplings(graph: BeliefGraph, hypothesis_ids: set[str]) -> dict[tuple[str, str], float]:
    """Pairwise dependencies between hypothesis variables.

    Explicit ``HYPOTHESIS_GENERALIZES_TO`` edges are strong positive
    dependencies. Attempts that tested multiple hypotheses add weaker
    empirical dependency: future evidence against one of them should
    slightly move the others because prior probes treated them as a
    coupled explanation set.
    """
    from mesmer.core.constants import EdgeKind

    couplings: dict[tuple[str, str], float] = defaultdict(float)

    def add(a: str, b: str, value: float) -> None:
        if a == b or a not in hypothesis_ids or b not in hypothesis_ids:
            return
        key = tuple(sorted((a, b)))
        couplings[key] += value

    for edge in graph.edges:
        if edge.kind is EdgeKind.HYPOTHESIS_GENERALIZES_TO:
            add(edge.src_id, edge.dst_id, _DEPENDENCY_COUPLING)

    for node in graph.iter_nodes(NodeKind.ATTEMPT):
        if not isinstance(node, Attempt) or not _is_observational_attempt(node):
            continue
        tested = [hid for hid in node.tested_hypothesis_ids if hid in hypothesis_ids]
        for i, a in enumerate(tested):
            for b in tested[i + 1 :]:
                add(a, b, _SHARED_ATTEMPT_COUPLING)

    return dict(couplings)


def _dependency_components(
    hypothesis_ids: set[str],
    couplings: dict[tuple[str, str], float],
) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {hid: set() for hid in hypothesis_ids}
    for a, b in couplings:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    seen: set[str] = set()
    components: list[list[str]] = []
    for hid in sorted(hypothesis_ids):
        if hid in seen:
            continue
        stack = [hid]
        seen.add(hid)
        comp: list[str] = []
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in adjacency.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(comp)
    return components


def _component_marginals(
    graph: BeliefGraph,
    component: list[str],
    couplings: dict[tuple[str, str], float],
    evidence_llrs: dict[str, float],
    *,
    with_evidence: bool,
) -> dict[str, float]:
    """Exact binary-factor inference for one connected component."""
    n = len(component)
    if n == 1:
        hid = component[0]
        node = graph.nodes[hid]
        assert isinstance(node, WeaknessHypothesis)
        if not with_evidence:
            return {hid: node.confidence}
        return {
            hid: _posterior_after_evidence(
                node.confidence,
                confidence_delta=abs(evidence_llrs.get(hid, 0.0)) / _EVIDENCE_LOG_ODDS_SCALE,
                polarity=Polarity.SUPPORTS if evidence_llrs.get(hid, 0.0) >= 0 else Polarity.REFUTES,
            )
            if evidence_llrs.get(hid, 0.0)
            else node.confidence
        }

    priors: list[float] = []
    for hid in component:
        node = graph.nodes[hid]
        assert isinstance(node, WeaknessHypothesis)
        priors.append(_clamp_probability(node.confidence))

    local_pairs = [
        (component.index(a), component.index(b), weight)
        for (a, b), weight in couplings.items()
        if a in component and b in component
    ]
    local_llrs = [evidence_llrs.get(hid, 0.0) if with_evidence else 0.0 for hid in component]

    log_weights: list[float] = []
    true_sums = [0.0 for _ in component]
    max_log = -math.inf
    states_count = 1 << n
    for mask in range(states_count):
        logp = 0.0
        for i, prior in enumerate(priors):
            truth = bool(mask & (1 << i))
            logp += math.log(prior if truth else 1.0 - prior)
            llr = local_llrs[i]
            if llr:
                logp += 0.5 * llr if truth else -0.5 * llr
        for i, j, coupling in local_pairs:
            same = bool(mask & (1 << i)) == bool(mask & (1 << j))
            logp += coupling if same else -coupling
        log_weights.append(logp)
        max_log = max(max_log, logp)

    total = 0.0
    for mask, logp in enumerate(log_weights):
        weight = math.exp(logp - max_log)
        total += weight
        for i in range(n):
            if mask & (1 << i):
                true_sums[i] += weight

    if total <= 0:
        return {hid: graph.nodes[hid].confidence for hid in component if isinstance(graph.nodes[hid], WeaknessHypothesis)}
    return {hid: true_sums[i] / total for i, hid in enumerate(component)}


def _component_marginals_loopy_bp(
    graph: BeliefGraph,
    component: list[str],
    couplings: dict[tuple[str, str], float],
    evidence_llrs: dict[str, float],
    *,
    with_evidence: bool,
) -> dict[str, float]:
    """Approximate binary-factor inference for large components.

    Exact enumeration is exponential in component size. For dense or
    very large hypothesis clusters we use damped loopy belief
    propagation over the same unary prior/evidence factors and pairwise
    dependency factors. This keeps evidence propagation alive in large
    real runs without allowing one giant component to stall the agent.
    """
    index = {hid: i for i, hid in enumerate(component)}
    unary: list[tuple[float, float]] = []
    for hid in component:
        node = graph.nodes[hid]
        assert isinstance(node, WeaknessHypothesis)
        prior = _clamp_probability(node.confidence)
        llr = evidence_llrs.get(hid, 0.0) if with_evidence else 0.0
        unary.append(
            (
                math.log(1.0 - prior) - 0.5 * llr,
                math.log(prior) + 0.5 * llr,
            )
        )

    neighbors: list[dict[int, float]] = [dict() for _ in component]
    for (a, b), raw_weight in couplings.items():
        if a not in index or b not in index:
            continue
        i = index[a]
        j = index[b]
        weight = max(-2.0, min(2.0, raw_weight))
        neighbors[i][j] = neighbors[i].get(j, 0.0) + weight
        neighbors[j][i] = neighbors[j].get(i, 0.0) + weight

    messages: dict[tuple[int, int], tuple[float, float]] = {
        (i, j): (0.0, 0.0) for i, ns in enumerate(neighbors) for j in ns
    }
    if not messages:
        return {
            hid: _sigmoid(unary[i][1] - unary[i][0])
            for i, hid in enumerate(component)
        }

    for _ in range(_LOOPY_BP_MAX_ITER):
        max_change = 0.0
        next_messages: dict[tuple[int, int], tuple[float, float]] = {}
        for (i, j), old in messages.items():
            base0, base1 = unary[i]
            for k in neighbors[i]:
                if k == j:
                    continue
                incoming = messages[(k, i)]
                base0 += incoming[0]
                base1 += incoming[1]

            coupling = neighbors[i][j]
            msg_to_j_false = _logsumexp2(base0 + coupling, base1 - coupling)
            msg_to_j_true = _logsumexp2(base0 - coupling, base1 + coupling)
            norm = _logsumexp2(msg_to_j_false, msg_to_j_true)
            raw = (msg_to_j_false - norm, msg_to_j_true - norm)
            damped = (
                (1.0 - _LOOPY_BP_DAMPING) * raw[0] + _LOOPY_BP_DAMPING * old[0],
                (1.0 - _LOOPY_BP_DAMPING) * raw[1] + _LOOPY_BP_DAMPING * old[1],
            )
            next_messages[(i, j)] = damped
            max_change = max(
                max_change,
                abs(damped[0] - old[0]),
                abs(damped[1] - old[1]),
            )
        messages = next_messages
        if max_change < _LOOPY_BP_TOL:
            break

    out: dict[str, float] = {}
    for i, hid in enumerate(component):
        b0, b1 = unary[i]
        for k in neighbors[i]:
            incoming = messages[(k, i)]
            b0 += incoming[0]
            b1 += incoming[1]
        norm = _logsumexp2(b0, b1)
        out[hid] = math.exp(b1 - norm)
    return out


def _joint_posterior_marginals(
    graph: BeliefGraph,
    evidences: list[Evidence],
) -> dict[str, float]:
    valid_evidence = [
        ev
        for ev in evidences
        if ev.hypothesis_id is not None
        and ev.polarity is not Polarity.NEUTRAL
        and isinstance(graph.nodes.get(ev.hypothesis_id), WeaknessHypothesis)
    ]
    if not valid_evidence:
        return {}

    hypothesis_ids = {
        node.id for node in graph.iter_nodes(NodeKind.HYPOTHESIS) if isinstance(node, WeaknessHypothesis)
    }
    evidence_llrs: dict[str, float] = defaultdict(float)
    for ev in valid_evidence:
        assert ev.hypothesis_id is not None
        evidence_llrs[ev.hypothesis_id] += _evidence_llr(ev)

    couplings = _dependency_couplings(graph, hypothesis_ids)
    components = _dependency_components(hypothesis_ids, couplings)
    posterior: dict[str, float] = {}

    for component in components:
        if not any(hid in evidence_llrs for hid in component):
            continue
        if len(component) > _JOINT_MAX_EXACT_HYPOTHESES:
            baseline = _component_marginals_loopy_bp(
                graph,
                component,
                couplings,
                evidence_llrs,
                with_evidence=False,
            )
            with_evidence = _component_marginals_loopy_bp(
                graph,
                component,
                couplings,
                evidence_llrs,
                with_evidence=True,
            )
        else:
            baseline = _component_marginals(
                graph,
                component,
                couplings,
                evidence_llrs,
                with_evidence=False,
            )
            with_evidence = _component_marginals(
                graph,
                component,
                couplings,
                evidence_llrs,
                with_evidence=True,
            )

        for hid in component:
            node = graph.nodes[hid]
            assert isinstance(node, WeaknessHypothesis)
            effect = with_evidence[hid] - baseline[hid]
            if abs(effect) > 1e-9:
                posterior[hid] = max(0.0, min(1.0, node.confidence + effect))

    return posterior


def apply_evidence_to_beliefs(
    graph: BeliefGraph,
    evidences: list[Evidence],
    *,
    run_id: str = "",
) -> list[HypothesisUpdateConfidenceDelta | HypothesisUpdateStatusDelta]:
    """Translate evidence rows into confidence + status deltas.

    Pure function — DOES NOT mutate the graph. The caller applies the
    returned deltas in order via :meth:`BeliefGraph.apply` (so the
    audit log captures every shift as a discrete event).

    Algorithm:

    1. For each evidence with non-NEUTRAL polarity AND a valid
       ``hypothesis_id`` already in the graph:

       - Convert current confidence to log-odds, add a signed
         log-likelihood-ratio step derived from the evidence magnitude,
         convert back to probability, and emit the probability-space
         delta as :class:`HypothesisUpdateConfidenceDelta`.
       - Simulate the cumulative posterior locally (the simulation only
         needs to know whether thresholds were crossed; it doesn't
         persist beyond returned deltas).
       - When the simulated confidence crosses
         :data:`HYPOTHESIS_CONFIRMED_THRESHOLD` (and the hypothesis
         was ACTIVE), emit a
         :class:`HypothesisUpdateStatusDelta` flipping to CONFIRMED.
         Same for :data:`HYPOTHESIS_REFUTED_THRESHOLD` → REFUTED.
       - At most ONE status delta per hypothesis per call — otherwise a
         confidence sequence ``+ - + -`` against an already-borderline
         hypothesis would emit alternating status flips.

    2. NEUTRAL evidence is recorded by the EvidenceCreateDelta layer
       but produces no belief deltas here.
    """
    valid_evidence_by_hypothesis: dict[str, list[Evidence]] = defaultdict(list)
    for ev in evidences:
        if ev.hypothesis_id is None or ev.polarity is Polarity.NEUTRAL:
            continue
        if isinstance(graph.nodes.get(ev.hypothesis_id), WeaknessHypothesis):
            valid_evidence_by_hypothesis[ev.hypothesis_id].append(ev)

    posteriors = _joint_posterior_marginals(graph, evidences)
    if not posteriors:
        return []

    out: list[HypothesisUpdateConfidenceDelta | HypothesisUpdateStatusDelta] = []
    status_emitted: set[str] = set()

    for hypothesis_id, new in sorted(posteriors.items()):
        node = graph.nodes.get(hypothesis_id)
        if not isinstance(node, WeaknessHypothesis):
            continue
        delta_value = new - node.confidence
        if abs(delta_value) <= 1e-9:
            continue
        evidence_id = (
            valid_evidence_by_hypothesis[hypothesis_id][0].id
            if valid_evidence_by_hypothesis.get(hypothesis_id)
            else None
        )
        out.append(
            HypothesisUpdateConfidenceDelta(
                hypothesis_id=hypothesis_id,
                delta_value=delta_value,
                evidence_id=evidence_id,
                run_id=run_id,
            )
        )

        # Status flip only fires for ACTIVE hypotheses crossing
        # thresholds for the first time in this batch.
        if node.status is HypothesisStatus.ACTIVE and hypothesis_id not in status_emitted:
            new_status: HypothesisStatus | None = None
            if new >= HYPOTHESIS_CONFIRMED_THRESHOLD:
                new_status = HypothesisStatus.CONFIRMED
            elif new <= HYPOTHESIS_REFUTED_THRESHOLD:
                new_status = HypothesisStatus.REFUTED
            if new_status is not None:
                out.append(
                    HypothesisUpdateStatusDelta(
                        hypothesis_id=hypothesis_id,
                        status=new_status,
                        run_id=run_id,
                    )
                )
                status_emitted.add(hypothesis_id)

    return out


# ---------------------------------------------------------------------------
# Utility ranking (pure)
# ---------------------------------------------------------------------------


def _component_expected_progress(
    hypothesis: WeaknessHypothesis,
) -> float:
    """How likely is this experiment to MOVE THE OBJECTIVE forward?

    Heuristic: high-confidence hypotheses are ready to convert. Map
    confidence ∈ [0, 1] to progress ∈ [0.3, 0.9] so even low-confidence
    hypotheses get a base contribution (we still need to test them).

    Higher is better; sign is positive in the weighted sum.
    """
    return 0.3 + 0.6 * hypothesis.confidence


def _component_information_gain(
    hypothesis: WeaknessHypothesis,
) -> float:
    """How much would running this experiment SHIFT our beliefs?

    Entropy-flavoured: maximally uncertain hypotheses (confidence near
    0.5) deliver the most information per test; near-confirmed (0.95)
    or near-refuted (0.05) hypotheses give little new info because we
    already know.

    Returns 1 - |2c - 1| ∈ [0, 1]. Highest at 0.5, zero at 0/1.
    """
    return 1.0 - abs(2.0 * hypothesis.confidence - 1.0)


def _component_novelty(
    experiment: FrontierExperiment,
    recent_attempts: list[Attempt],
) -> float:
    """How DIFFERENT is this experiment from what we recently tried?

    Token-Jaccard between the experiment's instruction + module name and
    each recent attempt's approach + module name. Take 1 - max-similarity:
    novelty is high when the experiment doesn't look like anything in
    the tail.

    Returns ∈ [0, 1]. Higher is better.
    """
    if not recent_attempts:
        return 1.0
    target_tokens = _tokens(experiment.instruction) | _tokens(experiment.module)
    if not target_tokens:
        return 0.5  # nothing to compare; no novelty bonus
    max_sim = 0.0
    for a in recent_attempts:
        tokens_a = _tokens(a.approach) | _tokens(a.module)
        sim = _jaccard(target_tokens, tokens_a)
        if sim > max_sim:
            max_sim = sim
    return max(0.0, 1.0 - max_sim)


def _component_strategy_prior(
    experiment: FrontierExperiment,
    graph: BeliefGraph,
) -> float:
    """How well has the linked strategy worked LOCALLY (this target)?

    Returns the strategy's local_success_rate when a strategy is
    linked, else 0.5 (no info — neither bonus nor penalty).
    """
    if experiment.strategy_id is None:
        return 0.5
    s = graph.nodes.get(experiment.strategy_id)
    if not isinstance(s, Strategy) or s.attempt_count == 0:
        return 0.5
    return s.local_success_rate


def _component_query_cost(
    experiment: FrontierExperiment,
    *,
    registry: "Registry | None",
) -> tuple[float, str, int]:
    """Estimated relative cost of running this experiment.

    Returns ∈ [0, 1]. Higher means MORE expensive — the weight in
    :data:`DEFAULT_UTILITY_WEIGHTS["query_cost"]` is negative so a
    higher cost lowers utility.
    """
    tier = registry.tier_of(experiment.module) if registry is not None else 2
    tier_cost = {0: 0.15, 1: 0.30, 2: 0.50, 3: 0.72}.get(tier, 0.50)
    reset_cost = 0.0
    submodule_cost = 0.0
    if registry is not None:
        mod = registry.get(experiment.module)
        if mod is not None:
            reset_cost = 0.12 if mod.reset_target else 0.0
            submodule_cost = min(0.12, 0.03 * len(mod.sub_modules))
    length_cost = min(0.08, len(experiment.instruction) / 2400.0)
    total = max(0.05, min(1.0, tier_cost + reset_cost + submodule_cost + length_cost))
    reasons = [f"tier={tier}"]
    if reset_cost:
        reasons.append("reset")
    if submodule_cost:
        reasons.append("delegates")
    if length_cost:
        reasons.append("long-instruction")
    return total, "+".join(reasons), tier


def _component_repetition_penalty(
    experiment: FrontierExperiment,
    fulfilled_recent: list[FrontierExperiment],
) -> float:
    """Have we recently fulfilled an experiment very similar to this one?

    Token-Jaccard against fulfilled experiments in the tail. Returns
    max similarity ∈ [0, 1]. Negative weight in the utility sum means
    high similarity lowers utility — we don't waste budget repeating.
    """
    if not fulfilled_recent:
        return 0.0
    target_tokens = _tokens(experiment.instruction) | _tokens(experiment.module)
    if not target_tokens:
        return 0.0
    max_sim = 0.0
    for f in fulfilled_recent:
        tokens_f = _tokens(f.instruction) | _tokens(f.module)
        sim = _jaccard(target_tokens, tokens_f)
        if sim > max_sim:
            max_sim = sim
    return max_sim


def _component_dead_similarity(
    experiment: FrontierExperiment,
    dead_attempts: list[Attempt],
) -> float:
    """Does this experiment look like attempts that already DIED?

    Token-Jaccard against attempts whose outcome was dead / refusal.
    Returns max similarity ∈ [0, 1]. Negative weight in the sum so a
    look-alike to a dead approach is heavily penalised.
    """
    if not dead_attempts:
        return 0.0
    target_tokens = _tokens(experiment.instruction) | _tokens(experiment.module)
    if not target_tokens:
        return 0.0
    max_sim = 0.0
    for a in dead_attempts:
        tokens_a = _tokens(a.approach) | _tokens(a.module)
        sim = _jaccard(target_tokens, tokens_a)
        if sim > max_sim:
            max_sim = sim
    return max_sim


def _entry_trait_affinity(graph: BeliefGraph, entry: "GlobalStrategyEntry") -> float:
    trait_keys = set(graph.target.traits)
    if not trait_keys:
        return 0.5
    works = {t for t in entry.works_against_traits if t}
    fails = {t for t in entry.fails_against_traits if t}
    positive = len(trait_keys & works)
    negative = len(trait_keys & fails)
    if positive == 0 and negative == 0:
        return 0.5
    return max(0.0, min(1.0, 0.5 + 0.25 * positive - 0.25 * negative))


def _library_entry_score(
    graph: BeliefGraph,
    experiment: FrontierExperiment,
    entry: "GlobalStrategyEntry",
    *,
    strategy_template: str,
) -> float:
    attempts = max(0, int(entry.global_attempt_count))
    if attempts <= 0:
        return 0.0
    reliability = min(1.0, math.log1p(attempts) / math.log1p(20.0))
    text_a = _tokens(entry.template_summary)
    text_b = (
        _tokens(strategy_template)
        | _tokens(experiment.instruction)
        | _tokens(experiment.module)
        | _tokens(experiment.expected_signal)
    )
    similarity = _jaccard(text_a, text_b) if text_a and text_b else 0.0
    trait_affinity = _entry_trait_affinity(graph, entry)
    score = (
        0.55 * entry.global_success_rate
        + 0.20 * reliability
        + 0.15 * similarity
        + 0.10 * trait_affinity
    )
    return max(0.0, min(1.0, score))


def _load_global_strategy_library() -> "StrategyLibrary | None":
    try:
        from mesmer.core.strategy_library import load_library

        return load_library()
    except Exception:  # pragma: no cover - corrupt local memory should not break ranking
        return None


def _component_transfer_value(
    experiment: FrontierExperiment,
    graph: BeliefGraph,
    *,
    strategy_library: "StrategyLibrary | None",
) -> float:
    """Cross-target lifetime-strategy bonus.

    Uses the global strategy library directly in the utility ranker.
    Exact strategy-template matches get first priority; otherwise the
    best same-family entry contributes by global success rate,
    evidence volume, text similarity, and target trait affinity.
    """
    if strategy_library is None or not getattr(strategy_library, "entries", None):
        return 0.0
    h = graph.nodes.get(experiment.hypothesis_id)
    if not isinstance(h, WeaknessHypothesis):
        return 0.0
    strategy_template = ""
    if experiment.strategy_id is not None:
        s = graph.nodes.get(experiment.strategy_id)
        if isinstance(s, Strategy):
            strategy_template = s.template_summary

    exact = (
        strategy_library.find(h.family, strategy_template)
        if strategy_template and hasattr(strategy_library, "find")
        else None
    )
    if exact is not None:
        score = _library_entry_score(
            graph,
            experiment,
            exact,
            strategy_template=strategy_template,
        )
        setattr(experiment, "_transfer_source", exact.template_summary)
        setattr(experiment, "_transfer_success_rate", exact.global_success_rate)
        setattr(experiment, "_transfer_attempts", exact.global_attempt_count)
        return score

    entries = strategy_library.for_family(h.family, top_k=8)
    if not entries:
        return 0.0
    best_entry = max(
        entries,
        key=lambda entry: _library_entry_score(
            graph,
            experiment,
            entry,
            strategy_template=strategy_template,
        ),
    )
    score = _library_entry_score(
        graph,
        experiment,
        best_entry,
        strategy_template=strategy_template,
    )
    setattr(experiment, "_transfer_source", best_entry.template_summary)
    setattr(experiment, "_transfer_success_rate", best_entry.global_success_rate)
    setattr(experiment, "_transfer_attempts", best_entry.global_attempt_count)
    return score


def rank_frontier(
    graph: BeliefGraph,
    *,
    weights: dict[str, float] | None = None,
    registry: "Registry | None" = None,
    strategy_library: "StrategyLibrary | None" = None,
    load_global_strategy_library: bool = True,
    recent_attempt_window: int = 8,
    fulfilled_window: int = 8,
    run_id: str = "",
) -> FrontierRankDelta:
    """Compute utility scores for every PROPOSED frontier experiment.

    Returns a single :class:`FrontierRankDelta` whose ``rankings`` map
    each experiment id to all nine component scores, rank metadata, and
    the aggregate ``utility``. Caller applies the delta — application is idempotent
    in spirit (overwrites prior rankings).

    ``weights`` overrides :data:`DEFAULT_UTILITY_WEIGHTS` per-call.
    Missing keys fall back to the default. ``recent_attempt_window``
    and ``fulfilled_window`` cap how far back the
    novelty / repetition / dead-similarity comparisons look — small
    windows keep ranking O(1) per experiment relative to graph size.
    """
    w = dict(DEFAULT_UTILITY_WEIGHTS)
    if weights:
        w.update(weights)
    library = strategy_library
    if library is None and load_global_strategy_library:
        library = _load_global_strategy_library()

    proposed = graph.proposed_frontier()
    if not proposed:
        return FrontierRankDelta(rankings={}, run_id=run_id)

    # Pull the comparison cohorts once.
    observational_attempts = sorted(
        (
            n
            for n in graph.iter_nodes(NodeKind.ATTEMPT)
            if isinstance(n, Attempt) and _is_observational_attempt(n)
        ),
        key=lambda a: a.created_at,
    )
    recent_attempts = observational_attempts[-recent_attempt_window:]

    dead_attempts = [
        a
        for a in observational_attempts
        if a.outcome in (AttemptOutcome.DEAD.value, AttemptOutcome.REFUSAL.value)
    ]

    fulfilled_recent = sorted(
        (
            n
            for n in graph.iter_nodes(NodeKind.FRONTIER)
            if isinstance(n, FrontierExperiment) and n.fulfilled_by is not None
        ),
        key=lambda f: f.created_at,
    )[-fulfilled_window:]

    rankings: dict[str, dict[str, Any]] = {}
    for exp in proposed:
        h = graph.nodes.get(exp.hypothesis_id)
        if not isinstance(h, WeaknessHypothesis):
            # Orphan experiment — skip ranking. Rank is a pure read-side
            # function; cleanup belongs to the mutation boundary.
            continue

        query_cost, query_cost_reason, query_cost_tier = _component_query_cost(
            exp,
            registry=registry,
        )
        transfer_value = _component_transfer_value(
            exp,
            graph,
            strategy_library=library,
        )
        components = {
            "expected_progress": _component_expected_progress(h),
            "information_gain": _component_information_gain(h),
            "hypothesis_confidence": h.confidence,
            "novelty": _component_novelty(exp, recent_attempts),
            "strategy_prior": _component_strategy_prior(exp, graph),
            "transfer_value": transfer_value,
            "query_cost": query_cost,
            "repetition_penalty": _component_repetition_penalty(exp, fulfilled_recent),
            "dead_similarity": _component_dead_similarity(exp, dead_attempts),
        }
        utility = sum(w.get(k, 0.0) * v for k, v in components.items())
        components["utility"] = utility
        components["query_cost_reason"] = query_cost_reason
        components["query_cost_tier"] = query_cost_tier
        source = getattr(exp, "_transfer_source", "")
        if source:
            components["transfer_source"] = source
            components["transfer_success_rate"] = getattr(exp, "_transfer_success_rate", 0.0)
            components["transfer_attempts"] = getattr(exp, "_transfer_attempts", 0)
        rankings[exp.id] = components

    return FrontierRankDelta(rankings=rankings, run_id=run_id)


# ---------------------------------------------------------------------------
# Shallow MCTS / UCB selector (Session 4A)
# ---------------------------------------------------------------------------

# Default exploration constant for the UCB bonus. ~sqrt(2) is the
# textbook starting point for UCT; we use a slightly smaller value
# because the underlying utility is already in [-1, 1] (not the
# canonical [0, 1] reward), so a smaller c keeps the bonus from
# dominating exploitative picks. Tunable per-call via
# ``select_next_experiment(exploration_c=...)``.
DEFAULT_EXPLORATION_C = 1.2
DEFAULT_LOOKAHEAD_WEIGHT = 0.35
DEFAULT_ROLLOUT_BRANCHING = 3
_SIM_SUPPORT_DELTA = 0.16
_SIM_REFUTE_DELTA = 0.12


def _hypothesis_visit_count(graph: BeliefGraph, hypothesis_id: str) -> int:
    """Number of observed attempts that have already tested ``hypothesis_id``.

    Infrastructure failures and empty target transcripts are audit records,
    not observations, so they do not suppress exploration.
    """
    n = 0
    for node in graph.iter_nodes(NodeKind.ATTEMPT):
        assert isinstance(node, Attempt)
        if _is_observational_attempt(node) and hypothesis_id in node.tested_hypothesis_ids:
            n += 1
    return n


def _total_attempt_count(graph: BeliefGraph) -> int:
    """Total observed Attempt nodes in the graph (the UCB ``N``)."""
    return sum(
        1
        for node in graph.iter_nodes(NodeKind.ATTEMPT)
        if isinstance(node, Attempt) and _is_observational_attempt(node)
    )


def _ucb_bonus(
    *,
    total_attempts: int,
    hypothesis_visits: int,
    exploration_c: float,
) -> float:
    if total_attempts <= 0:
        return 0.0
    return exploration_c * math.sqrt(math.log(total_attempts + 1.0) / (hypothesis_visits + 1.0))


def _support_probability(exp: FrontierExperiment) -> float:
    """Cheap world-model prior for the two-outcome lookahead rollout."""
    utility = max(0.0, min(1.0, exp.utility))
    strategy = max(0.0, min(1.0, exp.strategy_prior or 0.5))
    novelty = max(0.0, min(1.0, exp.novelty or 0.5))
    return max(0.1, min(0.9, 0.35 + 0.35 * utility + 0.2 * strategy + 0.1 * novelty))


def _simulated_utility(
    exp: FrontierExperiment,
    *,
    confidence: float,
    weights: dict[str, float] | None = None,
) -> float:
    w = dict(DEFAULT_UTILITY_WEIGHTS)
    if weights:
        w.update(weights)
    components = {
        "expected_progress": 0.3 + 0.6 * confidence,
        "information_gain": 1.0 - abs(2.0 * confidence - 1.0),
        "hypothesis_confidence": confidence,
        "novelty": exp.novelty,
        "strategy_prior": exp.strategy_prior or 0.5,
        "transfer_value": exp.transfer_value,
        "query_cost": exp.query_cost or 0.3,
        "repetition_penalty": exp.repetition_penalty,
        "dead_similarity": exp.dead_similarity,
    }
    return sum(w.get(k, 0.0) * v for k, v in components.items())


def _simulated_future_value(
    graph: BeliefGraph,
    *,
    anchor: FrontierExperiment,
    proposed: list[FrontierExperiment],
    confidence_overrides: dict[str, float],
    consumed_ids: set[str],
    total_attempts: int,
    exploration_c: float,
    depth: int,
    lookahead_weight: float,
    rollout_branching: int,
) -> float:
    if depth <= 0:
        return 0.0
    candidates = [
        exp
        for exp in proposed
        if exp.id not in consumed_ids and exp.hypothesis_id == anchor.hypothesis_id
    ]
    if not candidates or rollout_branching <= 0:
        h = graph.nodes.get(anchor.hypothesis_id)
        if not isinstance(h, WeaknessHypothesis):
            return 0.0
        conf = confidence_overrides.get(anchor.hypothesis_id, h.confidence)
        # Expansion fallback: estimate the value of generating one local
        # refinement after this experiment. High-information, novel,
        # strategy-backed branches get useful continuation value even before
        # a concrete child frontier exists.
        return 0.75 * _simulated_utility(anchor, confidence=conf)

    scored: list[tuple[float, FrontierExperiment]] = []
    for exp in candidates:
        h = graph.nodes.get(exp.hypothesis_id)
        if not isinstance(h, WeaknessHypothesis):
            continue
        conf = confidence_overrides.get(exp.hypothesis_id, h.confidence)
        visits = _hypothesis_visit_count(graph, exp.hypothesis_id)
        visits += sum(1 for eid in consumed_ids if _frontier_hypothesis_id(proposed, eid) == exp.hypothesis_id)
        base = _simulated_utility(exp, confidence=conf) + _ucb_bonus(
            total_attempts=total_attempts,
            hypothesis_visits=visits,
            exploration_c=exploration_c,
        )
        if depth > 1 and lookahead_weight > 0:
            p_support = _support_probability(exp)
            support_conf = _posterior_after_evidence(
                conf,
                confidence_delta=_SIM_SUPPORT_DELTA,
                polarity=Polarity.SUPPORTS,
            )
            refute_conf = _posterior_after_evidence(
                conf,
                confidence_delta=_SIM_REFUTE_DELTA,
                polarity=Polarity.REFUTES,
            )
            support_future = _simulated_future_value(
                graph,
                anchor=exp,
                proposed=proposed,
                confidence_overrides={**confidence_overrides, exp.hypothesis_id: support_conf},
                consumed_ids={*consumed_ids, exp.id},
                total_attempts=total_attempts + 1,
                exploration_c=exploration_c,
                depth=depth - 1,
                lookahead_weight=lookahead_weight,
                rollout_branching=rollout_branching,
            )
            refute_future = _simulated_future_value(
                graph,
                anchor=exp,
                proposed=proposed,
                confidence_overrides={**confidence_overrides, exp.hypothesis_id: refute_conf},
                consumed_ids={*consumed_ids, exp.id},
                total_attempts=total_attempts + 1,
                exploration_c=exploration_c,
                depth=depth - 1,
                lookahead_weight=lookahead_weight,
                rollout_branching=rollout_branching,
            )
            base += lookahead_weight * (
                p_support * support_future + (1.0 - p_support) * refute_future
            )
        scored.append((base, exp))
    scored.sort(key=lambda row: row[0], reverse=True)
    if not scored:
        return 0.0
    # Progressive widening: estimate the next layer from the best few
    # candidates rather than pretending the whole frontier expands.
    return max(score for score, _ in scored[:rollout_branching])


def _frontier_hypothesis_id(proposed: list[FrontierExperiment], experiment_id: str) -> str | None:
    for exp in proposed:
        if exp.id == experiment_id:
            return exp.hypothesis_id
    return None


def select_next_experiment(
    graph: BeliefGraph,
    *,
    exploration_c: float = DEFAULT_EXPLORATION_C,
    lookahead_depth: int = 2,
    lookahead_weight: float = DEFAULT_LOOKAHEAD_WEIGHT,
    rollout_branching: int = DEFAULT_ROLLOUT_BRANCHING,
) -> FrontierExperiment | None:
    """Pick the next :class:`FrontierExperiment` to dispatch.

    Uses UCB on top of the utility ranker, then optionally performs a
    bounded recursive belief-tree rollout over the existing frontier:

        choice_score(fx) = fx.utility
                         + c * sqrt(log(N + 1) / (n_h + 1))
                         + lookahead_weight * E[best_next_score]

    Where ``N`` is the total attempt count across the graph and
    ``n_h`` is the number of attempts that have already tested
    ``fx.hypothesis_id``. The +1 smoothing keeps the bonus finite for
    fresh hypotheses (n_h = 0) without diverging when N is small.

    The rollout is target-query-free. It simulates SUPPORTS / REFUTES
    evidence against the tested hypothesis, updates confidence in that
    imaginary branch, then recursively estimates the best local
    continuation under that belief state.

    Returns the experiment with the highest score from
    those still in :attr:`ExperimentState.PROPOSED`. Returns ``None``
    when no proposed experiments exist.

    This is **advisory** — the leader sees the brief and may pick a
    different experiment. The selector exists so:
      - the leader brief can flag the planner's preferred choice
        (Session 4A wires this);
      - automated callers (CI smoke runs, web auto-mode) can step the
        agent without an LLM in the loop;
      - the policy is auditable via a single function rather than
        spread across the prompt.

    Pure — does NOT mutate the graph. Caller decides what to do with
    the returned experiment.
    """
    proposed = [
        n
        for n in graph.iter_nodes(NodeKind.FRONTIER)
        if isinstance(n, FrontierExperiment) and n.state is ExperimentState.PROPOSED
    ]
    if not proposed:
        return None

    total_attempts = _total_attempt_count(graph)

    best: tuple[FrontierExperiment, float] | None = None
    for exp in proposed:
        h = graph.nodes.get(exp.hypothesis_id)
        if not isinstance(h, WeaknessHypothesis):
            continue
        n_h = _hypothesis_visit_count(graph, exp.hypothesis_id)
        score = exp.utility + _ucb_bonus(
            total_attempts=total_attempts,
            hypothesis_visits=n_h,
            exploration_c=exploration_c,
        )
        if lookahead_depth >= 2 and lookahead_weight > 0 and len(proposed) > 1:
            p_support = _support_probability(exp)
            support_conf = _posterior_after_evidence(
                h.confidence,
                confidence_delta=_SIM_SUPPORT_DELTA,
                polarity=Polarity.SUPPORTS,
            )
            refute_conf = _posterior_after_evidence(
                h.confidence,
                confidence_delta=_SIM_REFUTE_DELTA,
                polarity=Polarity.REFUTES,
            )
            support_value = _simulated_future_value(
                graph,
                anchor=exp,
                proposed=proposed,
                confidence_overrides={exp.hypothesis_id: support_conf},
                consumed_ids={exp.id},
                total_attempts=total_attempts + 1,
                exploration_c=exploration_c,
                depth=lookahead_depth - 1,
                lookahead_weight=lookahead_weight,
                rollout_branching=rollout_branching,
            )
            refute_value = _simulated_future_value(
                graph,
                anchor=exp,
                proposed=proposed,
                confidence_overrides={exp.hypothesis_id: refute_conf},
                consumed_ids={exp.id},
                total_attempts=total_attempts + 1,
                exploration_c=exploration_c,
                depth=lookahead_depth - 1,
                lookahead_weight=lookahead_weight,
                rollout_branching=rollout_branching,
            )
            score += lookahead_weight * (
                p_support * support_value + (1.0 - p_support) * refute_value
            )
        if best is None or score > best[1]:
            best = (exp, score)
    return best[0] if best else None


__all__ = [
    "DEFAULT_EXPLORATION_C",
    "DEFAULT_LOOKAHEAD_WEIGHT",
    "DEFAULT_ROLLOUT_BRANCHING",
    "apply_evidence_to_beliefs",
    "generate_frontier_experiments",
    "generate_hypotheses",
    "rank_frontier",
    "select_next_experiment",
]
