"""Belief Attack Graph — typed planner state for mesmer's red-team agent.

The legacy :class:`mesmer.core.graph.AttackGraph` is a flat execution log:
every module run becomes one node with a score and a status. That answers
"what did we try?" but not "what do we believe about this target?" — the
question that should drive next-move selection.

This module is the typed alternative the planner runs on. Nodes are
discriminated by kind (target, hypothesis, evidence, attempt, strategy,
frontier-experiment), edges carry a typed relationship, and every
mutation flows through a :class:`GraphDelta` reducer. Replaying the
deltas in order reconstructs the current graph — the JSONL delta log
doubles as the audit trail and the recovery log.

The agent's question shifts from:

    "Which module should I run next?"

to:

    "Which suspected weakness should I test next, with which strategy,
     given what I already believe about this target?"

Persistence sidecar — distinct from ``graph.json``::

    ~/.mesmer/targets/{hash}/
    ├── belief_graph.json     # current snapshot
    ├── belief_deltas.jsonl   # append-only delta log
    └── graph.json            # legacy AttackGraph (untouched in Session 1)

Session 1 ships this module + extractor + updater + context compiler as
a parallel system. Session 2 replaces the planner's read of
:class:`AttackGraph` with the belief-graph-derived brief from
:mod:`mesmer.core.agent.graph_compiler`.

References:
    - TAP (Tree of Attacks with Pruning), arXiv 2312.02119 — tree search
      and pruning shape for the frontier ranker.
    - PAIR (Prompt Automatic Iterative Refinement), arXiv 2310.08419 —
      single-branch iterative refinement when a hypothesis is promising.
    - GPTFuzzer, arXiv 2309.10253 — strategy mutation / corpus pressure.
    - AutoDAN-Turbo, arXiv 2410.05295 — lifelong cross-target strategy
      memory (the global ``Strategy`` slice; scoped to per-target in
      Session 1).
    - POMCP / UCT — informs the future shallow-MCTS scheduler that runs
      on top of this graph (Session 4, not Session 1).
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator

from mesmer.core.constants import (
    DeltaKind,
    EdgeKind,
    EvidenceType,
    ExperimentState,
    HypothesisStatus,
    NodeSource,
    Polarity,
)
from mesmer.core.errors import InvalidDelta


# ---------------------------------------------------------------------------
# Node kind discriminator
# ---------------------------------------------------------------------------


class NodeKind(str, Enum):
    """Discriminator on a :class:`BeliefNode` subclass.

    Lives on the node itself (``node.kind``) so edge-endpoint validation
    and JSON dispatch don't have to ``isinstance`` against every
    subclass.
    """

    TARGET = "target"
    HYPOTHESIS = "hypothesis"
    EVIDENCE = "evidence"
    ATTEMPT = "attempt"
    STRATEGY = "strategy"
    FRONTIER = "frontier"


# Per-kind id prefix — readable in logs ("wh_a1b2c3d4 confidence 0.62").
_ID_PREFIXES: dict[NodeKind, str] = {
    NodeKind.TARGET: "tg",
    NodeKind.HYPOTHESIS: "wh",
    NodeKind.EVIDENCE: "ev",
    NodeKind.ATTEMPT: "at",
    NodeKind.STRATEGY: "st",
    NodeKind.FRONTIER: "fx",
}


def _new_id(kind: NodeKind) -> str:
    """Generate a kind-prefixed short id. Logs read as `wh_a1b2c3d4`."""
    return f"{_ID_PREFIXES[kind]}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@dataclass
class BeliefNode:
    """Common shape for every typed node in the belief graph.

    Subclasses set ``kind`` via class-level default and add their own
    fields. The base carries ``id``, ``created_at``, and ``run_id`` —
    the trio every audit / cross-run query joins on.
    """

    kind: ClassVar[NodeKind]

    id: str
    created_at: float = field(default_factory=time.time)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        # Subclasses extend; base dumps the shared trio + kind tag so
        # deserialisation can dispatch.
        return {
            "kind": self.kind.value,
            "id": self.id,
            "created_at": self.created_at,
            "run_id": self.run_id,
        }


@dataclass
class TargetNode(BeliefNode):
    """Singleton root — one per :class:`BeliefGraph`.

    Holds free-form ``traits`` extracted by the target-profiler module
    or other recon attempts. Traits are unstructured strings keyed by
    operator-chosen names ("system_prompt_hint", "tool_catalog",
    "refusal_phrases", …). The schema is intentionally loose — the
    extractor and the planner both read traits as opaque text. Adding a
    typed ``TargetProfile`` dataclass here is the explicit hallucination
    trap from the project's CLAUDE.md ("Things that don't exist — don't
    invent them"); we resist.
    """

    kind: ClassVar[NodeKind] = NodeKind.TARGET

    target_hash: str = ""
    traits: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(target_hash=self.target_hash, traits=dict(self.traits))
        return d


@dataclass
class WeaknessHypothesis(BeliefNode):
    """A typed claim about how the target might be exploited.

    The unit of planning. The agent's next move should be motivated by
    "test or exploit hypothesis X", not "run module Y" — modules and
    strategies are *means* the planner picks once a hypothesis is
    selected.

    ``confidence`` is in [0.0, 1.0]. Updates are linear and clamped (see
    :func:`mesmer.core.agent.beliefs.update_confidence`); we explicitly
    reject full Bayesian inference for now — calibrating priors and
    likelihoods against an LLM target is research-grade work and would
    overengineer Session 1.

    ``family`` groups hypotheses by attack class ("format-shift",
    "authority-bias", "instruction-recital") so the strategy library
    can target the cluster rather than each hypothesis individually.
    """

    kind: ClassVar[NodeKind] = NodeKind.HYPOTHESIS

    claim: str = ""
    description: str = ""
    family: str = ""
    confidence: float = 0.5
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    last_tested_at: float | None = None
    last_evidence_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            claim=self.claim,
            description=self.description,
            family=self.family,
            confidence=self.confidence,
            status=self.status.value,
            last_tested_at=self.last_tested_at,
            last_evidence_at=self.last_evidence_at,
        )
        return d


@dataclass
class Evidence(BeliefNode):
    """Structured signal extracted from a target response.

    Polarity is signed against ``hypothesis_id`` — supports raises that
    hypothesis's confidence, refutes lowers it. Evidence with no
    hypothesis_id (NEUTRAL polarity) is recorded for audit and may
    later attach to a newly generated hypothesis.

    ``confidence_delta`` is the magnitude (always positive); polarity
    decides the sign at apply time. Set by the extractor based on
    :data:`mesmer.core.constants.EVIDENCE_TYPE_WEIGHTS` plus a
    confidence-in-the-extraction multiplier.

    The ``verbatim_fragment`` is the smallest target snippet that
    motivated the label — kept short (under ~200 chars) so the planner
    brief stays readable. Long evidence becomes new traits on the
    target instead.
    """

    kind: ClassVar[NodeKind] = NodeKind.EVIDENCE

    signal_type: EvidenceType = EvidenceType.UNKNOWN
    polarity: Polarity = Polarity.NEUTRAL
    verbatim_fragment: str = ""
    rationale: str = ""
    from_attempt: str = ""
    hypothesis_id: str | None = None
    confidence_delta: float = 0.0
    extractor_confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            signal_type=self.signal_type.value,
            polarity=self.polarity.value,
            verbatim_fragment=self.verbatim_fragment,
            rationale=self.rationale,
            from_attempt=self.from_attempt,
            hypothesis_id=self.hypothesis_id,
            confidence_delta=self.confidence_delta,
            extractor_confidence=self.extractor_confidence,
        )
        return d


@dataclass
class Attempt(BeliefNode):
    """One module execution against the target.

    Replaces the role of the legacy ``AttackNode`` in attempt-recording,
    but with explicit links to the hypotheses tested, the strategy
    used, and the evidence observed. The judge_score and the verbatim
    transcript are kept here so the audit story is identical to the
    legacy graph; what changes is what the planner reads downstream
    (frontier ranking now operates on hypotheses + experiments, not on
    raw attempt history).
    """

    kind: ClassVar[NodeKind] = NodeKind.ATTEMPT

    module: str = ""
    approach: str = ""
    experiment_id: str | None = None
    messages_sent: list[str] = field(default_factory=list)
    target_responses: list[str] = field(default_factory=list)
    module_output: str = ""
    judge_score: int = 0
    objective_progress: float = 0.0
    outcome: str = ""  # AttemptOutcome value; left flexible for back-compat
    reflection: str = ""
    tested_hypothesis_ids: list[str] = field(default_factory=list)
    used_strategy_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            module=self.module,
            approach=self.approach,
            experiment_id=self.experiment_id,
            messages_sent=list(self.messages_sent),
            target_responses=list(self.target_responses),
            module_output=self.module_output,
            judge_score=self.judge_score,
            objective_progress=self.objective_progress,
            outcome=self.outcome,
            reflection=self.reflection,
            tested_hypothesis_ids=list(self.tested_hypothesis_ids),
            used_strategy_id=self.used_strategy_id,
            evidence_ids=list(self.evidence_ids),
        )
        return d


@dataclass
class Strategy(BeliefNode):
    """A reusable attack pattern derived from successful attempts.

    ``family`` matches a :class:`WeaknessHypothesis` family so the
    planner can pair "this hypothesis × that strategy" without joining
    on free-text. ``template_summary`` is short prose ("ask target to
    output policy as transformed artifact") — the strategy is not a
    prompt template, it's the *idea*.

    Stats are local-to-target by default; the AutoDAN-Turbo-style
    cross-target slice lives in a separate global library
    (Session 4 — not in Session 1).
    """

    kind: ClassVar[NodeKind] = NodeKind.STRATEGY

    family: str = ""
    template_summary: str = ""
    success_count: int = 0
    attempt_count: int = 0
    works_against_traits: list[str] = field(default_factory=list)
    fails_against_traits: list[str] = field(default_factory=list)

    @property
    def local_success_rate(self) -> float:
        if self.attempt_count == 0:
            return 0.0
        return self.success_count / self.attempt_count

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            family=self.family,
            template_summary=self.template_summary,
            success_count=self.success_count,
            attempt_count=self.attempt_count,
            works_against_traits=list(self.works_against_traits),
            fails_against_traits=list(self.fails_against_traits),
        )
        return d


@dataclass
class FrontierExperiment(BeliefNode):
    """A proposed next move — typed enough to dispatch directly.

    The planner generates these from active hypotheses + known
    strategies. The leader picks one by ``id`` to dispatch (not by
    module name); the manager that executes it records an
    :class:`Attempt` with ``experiment_id`` set, and the backup pass
    flips this experiment's state to FULFILLED.

    Utility components are in [-1, 1] before weighting (see
    :data:`mesmer.core.constants.DEFAULT_UTILITY_WEIGHTS`); the
    aggregate ``utility`` is computed at ranking time and persisted
    here so the brief renderer doesn't have to recompute on every
    read.
    """

    kind: ClassVar[NodeKind] = NodeKind.FRONTIER

    hypothesis_id: str = ""
    strategy_id: str | None = None
    module: str = ""
    instruction: str = ""
    expected_signal: str = ""
    state: ExperimentState = ExperimentState.PROPOSED
    source: NodeSource = NodeSource.AGENT
    fulfilled_by: str | None = None

    # Ranking components (all in [-1, 1], see DEFAULT_UTILITY_WEIGHTS).
    utility: float = 0.0
    expected_progress: float = 0.0
    information_gain: float = 0.0
    novelty: float = 0.0
    strategy_prior: float = 0.0
    transfer_value: float = 0.0
    query_cost: float = 0.0
    repetition_penalty: float = 0.0
    dead_similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            hypothesis_id=self.hypothesis_id,
            strategy_id=self.strategy_id,
            module=self.module,
            instruction=self.instruction,
            expected_signal=self.expected_signal,
            state=self.state.value,
            source=self.source.value,
            fulfilled_by=self.fulfilled_by,
            utility=self.utility,
            expected_progress=self.expected_progress,
            information_gain=self.information_gain,
            novelty=self.novelty,
            strategy_prior=self.strategy_prior,
            transfer_value=self.transfer_value,
            query_cost=self.query_cost,
            repetition_penalty=self.repetition_penalty,
            dead_similarity=self.dead_similarity,
        )
        return d


# Dispatch table for JSON deserialisation. Adding a new node kind requires
# adding the class here too.
_NODE_CLASS_BY_KIND: dict[NodeKind, type[BeliefNode]] = {
    NodeKind.TARGET: TargetNode,
    NodeKind.HYPOTHESIS: WeaknessHypothesis,
    NodeKind.EVIDENCE: Evidence,
    NodeKind.ATTEMPT: Attempt,
    NodeKind.STRATEGY: Strategy,
    NodeKind.FRONTIER: FrontierExperiment,
}


def _node_from_dict(d: dict[str, Any]) -> BeliefNode:
    """Reconstruct a typed node from its serialised dict."""
    kind = NodeKind(d["kind"])
    cls = _NODE_CLASS_BY_KIND[kind]
    # Re-coerce enum-typed fields manually — dataclass init doesn't
    # know to wrap a str into the right Enum subclass.
    payload = {k: v for k, v in d.items() if k != "kind"}
    if cls is WeaknessHypothesis:
        payload["status"] = HypothesisStatus(payload.get("status", "active"))
    elif cls is Evidence:
        payload["signal_type"] = EvidenceType(payload.get("signal_type", "unknown"))
        payload["polarity"] = Polarity(payload.get("polarity", "neutral"))
    elif cls is FrontierExperiment:
        payload["state"] = ExperimentState(payload.get("state", "proposed"))
        payload["source"] = NodeSource(payload.get("source", "agent"))
    return cls(**payload)


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

# Endpoint contract per edge kind: (source NodeKind, destination NodeKind).
# Validated in :meth:`BeliefGraph._apply_edge_create`.
_EDGE_END_TYPES: dict[EdgeKind, tuple[NodeKind, NodeKind]] = {
    EdgeKind.HYPOTHESIS_SUPPORTED_BY_EVIDENCE: (NodeKind.HYPOTHESIS, NodeKind.EVIDENCE),
    EdgeKind.HYPOTHESIS_REFUTED_BY_EVIDENCE: (NodeKind.HYPOTHESIS, NodeKind.EVIDENCE),
    EdgeKind.ATTEMPT_TESTS_HYPOTHESIS: (NodeKind.ATTEMPT, NodeKind.HYPOTHESIS),
    EdgeKind.ATTEMPT_USED_STRATEGY: (NodeKind.ATTEMPT, NodeKind.STRATEGY),
    EdgeKind.ATTEMPT_OBSERVED_EVIDENCE: (NodeKind.ATTEMPT, NodeKind.EVIDENCE),
    EdgeKind.ATTEMPT_CONFIRMED_HYPOTHESIS: (NodeKind.ATTEMPT, NodeKind.HYPOTHESIS),
    EdgeKind.ATTEMPT_REFUTED_HYPOTHESIS: (NodeKind.ATTEMPT, NodeKind.HYPOTHESIS),
    EdgeKind.FRONTIER_EXPANDS_HYPOTHESIS: (NodeKind.FRONTIER, NodeKind.HYPOTHESIS),
    EdgeKind.FRONTIER_USES_STRATEGY: (NodeKind.FRONTIER, NodeKind.STRATEGY),
    EdgeKind.STRATEGY_GENERALIZES_FROM_ATTEMPT: (NodeKind.STRATEGY, NodeKind.ATTEMPT),
    EdgeKind.HYPOTHESIS_GENERALIZES_TO: (NodeKind.HYPOTHESIS, NodeKind.HYPOTHESIS),
}


@dataclass
class Edge:
    """A typed relationship between two :class:`BeliefNode` instances.

    Edges are stored on the graph as a flat list (no adjacency map yet —
    cardinality is low enough that linear scans for "edges from X" stay
    cheap). The endpoint contract in :data:`_EDGE_END_TYPES` is enforced
    at apply time, NOT at dataclass init, so edges can round-trip through
    JSON without the dispatch table being available.
    """

    src_id: str
    dst_id: str
    kind: EdgeKind
    weight: float = 1.0
    created_at: float = field(default_factory=time.time)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "src_id": self.src_id,
            "dst_id": self.dst_id,
            "kind": self.kind.value,
            "weight": self.weight,
            "created_at": self.created_at,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Edge:
        return cls(
            src_id=d["src_id"],
            dst_id=d["dst_id"],
            kind=EdgeKind(d["kind"]),
            weight=float(d.get("weight", 1.0)),
            created_at=float(d.get("created_at", time.time())),
            run_id=d.get("run_id", ""),
        )


# ---------------------------------------------------------------------------
# Deltas — the only legal way to mutate the graph
# ---------------------------------------------------------------------------


@dataclass
class GraphDelta:
    """Base class for typed graph mutations.

    Subclasses pair with a :class:`DeltaKind` value. The graph's
    :meth:`BeliefGraph.apply` dispatches on ``kind`` to the matching
    private ``_apply_*`` method, then appends the delta to the JSONL
    log. Subclasses should NOT mutate state directly — replay safety
    requires the apply path to be the single source of truth.
    """

    kind: ClassVar[DeltaKind]

    created_at: float = field(default_factory=time.time)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "created_at": self.created_at,
            "run_id": self.run_id,
        }


@dataclass
class TargetTraitsUpdateDelta(GraphDelta):
    """Merge new traits into the singleton target node.

    Latest-write-wins per key. Not a full replacement — partial updates
    from successive recon passes accumulate.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.TARGET_TRAITS_UPDATE
    traits: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["traits"] = dict(self.traits)
        return d


@dataclass
class HypothesisCreateDelta(GraphDelta):
    """Insert a new :class:`WeaknessHypothesis`.

    The hypothesis object is passed in fully-formed; the apply path
    only validates that ``id`` is fresh and the family is non-empty.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.HYPOTHESIS_CREATE
    hypothesis: WeaknessHypothesis | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["hypothesis"] = self.hypothesis.to_dict() if self.hypothesis else None
        return d


@dataclass
class HypothesisUpdateConfidenceDelta(GraphDelta):
    """Apply a signed delta to a hypothesis's confidence.

    ``evidence_id`` ties the update back to its motivating evidence
    for replay / audit. Magnitude is the absolute shift; direction is
    encoded in the sign of ``delta_value`` (positive = supports,
    negative = refutes). Confidence is clamped to [0, 1] inside the
    apply path.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.HYPOTHESIS_UPDATE_CONFIDENCE
    hypothesis_id: str = ""
    delta_value: float = 0.0
    evidence_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            hypothesis_id=self.hypothesis_id,
            delta_value=self.delta_value,
            evidence_id=self.evidence_id,
        )
        return d


@dataclass
class HypothesisUpdateStatusDelta(GraphDelta):
    """Flip a hypothesis status (ACTIVE / CONFIRMED / REFUTED / STALE).

    Usually applied by the belief updater after a confidence delta
    crosses a threshold. Operators can also issue this directly via
    the web UI to manually retire a hypothesis.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.HYPOTHESIS_UPDATE_STATUS
    hypothesis_id: str = ""
    status: HypothesisStatus = HypothesisStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(hypothesis_id=self.hypothesis_id, status=self.status.value)
        return d


@dataclass
class EvidenceCreateDelta(GraphDelta):
    """Insert a new :class:`Evidence` and its supports/refutes edge.

    The apply path also creates the corresponding
    HYPOTHESIS_SUPPORTED_BY_EVIDENCE / HYPOTHESIS_REFUTED_BY_EVIDENCE
    edge from ``evidence.hypothesis_id`` (when set) — saves the caller
    from emitting a separate :class:`EdgeCreateDelta` for the common
    case. Neutral evidence (``hypothesis_id is None``) lands without
    an edge.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.EVIDENCE_CREATE
    evidence: Evidence | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["evidence"] = self.evidence.to_dict() if self.evidence else None
        return d


@dataclass
class AttemptCreateDelta(GraphDelta):
    """Insert a new :class:`Attempt` plus its derived edges.

    The apply path emits ATTEMPT_TESTS_HYPOTHESIS edges for every id in
    ``attempt.tested_hypothesis_ids``, ATTEMPT_USED_STRATEGY for the
    strategy (if set), and ATTEMPT_OBSERVED_EVIDENCE for every id in
    ``attempt.evidence_ids``. The frontier link is implicit through
    ``attempt.experiment_id``; we don't need a dedicated edge because
    the FrontierExperiment's ``fulfilled_by`` field already points to
    the attempt id.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.ATTEMPT_CREATE
    attempt: Attempt | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["attempt"] = self.attempt.to_dict() if self.attempt else None
        return d


@dataclass
class StrategyCreateDelta(GraphDelta):
    """Insert a new :class:`Strategy`."""

    kind: ClassVar[DeltaKind] = DeltaKind.STRATEGY_CREATE
    strategy: Strategy | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["strategy"] = self.strategy.to_dict() if self.strategy else None
        return d


@dataclass
class StrategyUpdateStatsDelta(GraphDelta):
    """Bump a strategy's success/attempt counters.

    Applied by the backup pass after each fulfilled experiment. We
    keep this as a dedicated delta (rather than rewriting the strategy
    object) so the audit log shows every win/loss as a discrete event.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.STRATEGY_UPDATE_STATS
    strategy_id: str = ""
    success_inc: int = 0
    attempt_inc: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            strategy_id=self.strategy_id,
            success_inc=self.success_inc,
            attempt_inc=self.attempt_inc,
        )
        return d


@dataclass
class FrontierCreateDelta(GraphDelta):
    """Insert a proposed :class:`FrontierExperiment`.

    The apply path emits FRONTIER_EXPANDS_HYPOTHESIS automatically and
    FRONTIER_USES_STRATEGY when ``strategy_id`` is set.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.FRONTIER_CREATE
    experiment: FrontierExperiment | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["experiment"] = self.experiment.to_dict() if self.experiment else None
        return d


@dataclass
class FrontierUpdateStateDelta(GraphDelta):
    """Move a frontier experiment between PROPOSED → EXECUTING →
    FULFILLED, or DROPPED.

    ``fulfilled_by`` is set when transitioning to FULFILLED — the
    attempt id that closed the experiment.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.FRONTIER_UPDATE_STATE
    experiment_id: str = ""
    state: ExperimentState = ExperimentState.PROPOSED
    fulfilled_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            experiment_id=self.experiment_id,
            state=self.state.value,
            fulfilled_by=self.fulfilled_by,
        )
        return d


@dataclass
class FrontierRankDelta(GraphDelta):
    """Bulk-update utility scores for a set of frontier experiments.

    ``rankings`` maps experiment_id → component dict (must include all
    keys of :data:`mesmer.core.constants.DEFAULT_UTILITY_WEIGHTS` plus
    the aggregate ``utility``). The apply path overwrites — frontier
    rankings are recomputed every iteration in current design.
    """

    kind: ClassVar[DeltaKind] = DeltaKind.FRONTIER_RANK
    rankings: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["rankings"] = {eid: dict(scores) for eid, scores in self.rankings.items()}
        return d


@dataclass
class EdgeCreateDelta(GraphDelta):
    """Insert a typed edge.

    Most edges are emitted automatically by the EvidenceCreateDelta /
    AttemptCreateDelta / FrontierCreateDelta apply paths. Direct
    EdgeCreateDelta is reserved for cross-cutting links the helper
    paths don't cover (e.g. STRATEGY_GENERALIZES_FROM_ATTEMPT, which
    fires from the strategy synthesiser).
    """

    kind: ClassVar[DeltaKind] = DeltaKind.EDGE_CREATE
    edge: Edge | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["edge"] = self.edge.to_dict() if self.edge else None
        return d


# Dispatch table for delta JSON deserialisation. Adding a new delta kind
# requires adding it here too.
_DELTA_CLASS_BY_KIND: dict[DeltaKind, type[GraphDelta]] = {
    DeltaKind.TARGET_TRAITS_UPDATE: TargetTraitsUpdateDelta,
    DeltaKind.HYPOTHESIS_CREATE: HypothesisCreateDelta,
    DeltaKind.HYPOTHESIS_UPDATE_CONFIDENCE: HypothesisUpdateConfidenceDelta,
    DeltaKind.HYPOTHESIS_UPDATE_STATUS: HypothesisUpdateStatusDelta,
    DeltaKind.EVIDENCE_CREATE: EvidenceCreateDelta,
    DeltaKind.ATTEMPT_CREATE: AttemptCreateDelta,
    DeltaKind.STRATEGY_CREATE: StrategyCreateDelta,
    DeltaKind.STRATEGY_UPDATE_STATS: StrategyUpdateStatsDelta,
    DeltaKind.FRONTIER_CREATE: FrontierCreateDelta,
    DeltaKind.FRONTIER_UPDATE_STATE: FrontierUpdateStateDelta,
    DeltaKind.FRONTIER_RANK: FrontierRankDelta,
    DeltaKind.EDGE_CREATE: EdgeCreateDelta,
}


def _delta_from_dict(d: dict[str, Any]) -> GraphDelta:
    """Reconstruct a typed delta from its serialised dict."""
    kind = DeltaKind(d["kind"])
    cls = _DELTA_CLASS_BY_KIND[kind]
    common = {
        "created_at": float(d.get("created_at", time.time())),
        "run_id": d.get("run_id", ""),
    }
    if cls is TargetTraitsUpdateDelta:
        return cls(**common, traits=dict(d.get("traits", {})))
    if cls is HypothesisCreateDelta:
        h = d.get("hypothesis")
        return cls(**common, hypothesis=_node_from_dict(h) if h else None)  # type: ignore[arg-type]
    if cls is HypothesisUpdateConfidenceDelta:
        return cls(
            **common,
            hypothesis_id=d.get("hypothesis_id", ""),
            delta_value=float(d.get("delta_value", 0.0)),
            evidence_id=d.get("evidence_id"),
        )
    if cls is HypothesisUpdateStatusDelta:
        return cls(
            **common,
            hypothesis_id=d.get("hypothesis_id", ""),
            status=HypothesisStatus(d.get("status", "active")),
        )
    if cls is EvidenceCreateDelta:
        e = d.get("evidence")
        return cls(**common, evidence=_node_from_dict(e) if e else None)  # type: ignore[arg-type]
    if cls is AttemptCreateDelta:
        a = d.get("attempt")
        return cls(**common, attempt=_node_from_dict(a) if a else None)  # type: ignore[arg-type]
    if cls is StrategyCreateDelta:
        s = d.get("strategy")
        return cls(**common, strategy=_node_from_dict(s) if s else None)  # type: ignore[arg-type]
    if cls is StrategyUpdateStatsDelta:
        return cls(
            **common,
            strategy_id=d.get("strategy_id", ""),
            success_inc=int(d.get("success_inc", 0)),
            attempt_inc=int(d.get("attempt_inc", 0)),
        )
    if cls is FrontierCreateDelta:
        ex = d.get("experiment")
        return cls(**common, experiment=_node_from_dict(ex) if ex else None)  # type: ignore[arg-type]
    if cls is FrontierUpdateStateDelta:
        return cls(
            **common,
            experiment_id=d.get("experiment_id", ""),
            state=ExperimentState(d.get("state", "proposed")),
            fulfilled_by=d.get("fulfilled_by"),
        )
    if cls is FrontierRankDelta:
        rankings = {
            eid: {k: float(v) for k, v in scores.items()}
            for eid, scores in d.get("rankings", {}).items()
        }
        return cls(**common, rankings=rankings)
    if cls is EdgeCreateDelta:
        ed = d.get("edge")
        return cls(**common, edge=Edge.from_dict(ed) if ed else None)
    raise InvalidDelta(kind.value, "no deserialiser registered")


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@dataclass
class BeliefGraph:
    """Typed planner state for a single target.

    Construct empty (``BeliefGraph(target_hash=...)``) or load from
    snapshot + delta log via :meth:`load`. Mutations always go through
    :meth:`apply` — never reach into ``self.nodes`` directly outside
    this module's apply paths, because the JSONL audit log has to stay
    aligned with in-memory state.
    """

    target_hash: str = ""
    nodes: dict[str, BeliefNode] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    deltas: list[GraphDelta] = field(default_factory=list)

    # ---- construction ----

    def __post_init__(self) -> None:
        if not self.nodes:
            target = TargetNode(
                id=_new_id(NodeKind.TARGET),
                target_hash=self.target_hash,
            )
            self.nodes[target.id] = target

    @property
    def target(self) -> TargetNode:
        for n in self.nodes.values():
            if isinstance(n, TargetNode):
                return n
        raise InvalidDelta("target_missing", "no TargetNode in graph")

    # ---- apply: the only mutation entry point ----

    def apply(self, delta: GraphDelta) -> None:
        """Mutate in place and append the delta to the audit log.

        Raises :class:`InvalidDelta` for malformed deltas (unknown
        node refs, bad enum values, edge endpoint contract violations).
        Callers at the engine boundary should catch and log; deeper
        code lets the exception propagate.
        """
        if isinstance(delta, TargetTraitsUpdateDelta):
            self._apply_target_traits(delta)
        elif isinstance(delta, HypothesisCreateDelta):
            self._apply_hypothesis_create(delta)
        elif isinstance(delta, HypothesisUpdateConfidenceDelta):
            self._apply_hypothesis_update_confidence(delta)
        elif isinstance(delta, HypothesisUpdateStatusDelta):
            self._apply_hypothesis_update_status(delta)
        elif isinstance(delta, EvidenceCreateDelta):
            self._apply_evidence_create(delta)
        elif isinstance(delta, AttemptCreateDelta):
            self._apply_attempt_create(delta)
        elif isinstance(delta, StrategyCreateDelta):
            self._apply_strategy_create(delta)
        elif isinstance(delta, StrategyUpdateStatsDelta):
            self._apply_strategy_update_stats(delta)
        elif isinstance(delta, FrontierCreateDelta):
            self._apply_frontier_create(delta)
        elif isinstance(delta, FrontierUpdateStateDelta):
            self._apply_frontier_update_state(delta)
        elif isinstance(delta, FrontierRankDelta):
            self._apply_frontier_rank(delta)
        elif isinstance(delta, EdgeCreateDelta):
            self._apply_edge_create(delta)
        else:
            raise InvalidDelta(
                getattr(delta, "kind", "?").value if hasattr(delta, "kind") else "unknown",
                f"no apply handler for {type(delta).__name__}",
            )
        self.deltas.append(delta)

    # ---- per-delta apply paths ----

    def _apply_target_traits(self, delta: TargetTraitsUpdateDelta) -> None:
        self.target.traits.update(delta.traits)

    def _apply_hypothesis_create(self, delta: HypothesisCreateDelta) -> None:
        h = delta.hypothesis
        if h is None:
            raise InvalidDelta(delta.kind.value, "hypothesis is None")
        if h.id in self.nodes:
            raise InvalidDelta(delta.kind.value, f"id {h.id!r} already exists")
        if not h.family:
            raise InvalidDelta(delta.kind.value, "family is required")
        # Deep-copy so subsequent mutations of the graph's node don't bleed
        # back into the delta's payload (which the audit log serialises
        # later). The delta is the snapshot at delta-emit time; the graph
        # owns the live mutable copy.
        self.nodes[h.id] = copy.deepcopy(h)

    def _apply_hypothesis_update_confidence(self, delta: HypothesisUpdateConfidenceDelta) -> None:
        node = self._require_node(delta.hypothesis_id, NodeKind.HYPOTHESIS, delta.kind)
        assert isinstance(node, WeaknessHypothesis)  # narrowing for type-checkers
        node.confidence = max(0.0, min(1.0, node.confidence + delta.delta_value))
        node.last_evidence_at = delta.created_at

    def _apply_hypothesis_update_status(self, delta: HypothesisUpdateStatusDelta) -> None:
        node = self._require_node(delta.hypothesis_id, NodeKind.HYPOTHESIS, delta.kind)
        assert isinstance(node, WeaknessHypothesis)
        node.status = delta.status

    def _apply_evidence_create(self, delta: EvidenceCreateDelta) -> None:
        ev = delta.evidence
        if ev is None:
            raise InvalidDelta(delta.kind.value, "evidence is None")
        if ev.id in self.nodes:
            raise InvalidDelta(delta.kind.value, f"id {ev.id!r} already exists")
        if ev.hypothesis_id is not None:
            self._require_node(ev.hypothesis_id, NodeKind.HYPOTHESIS, delta.kind)
        if ev.from_attempt:
            # Allow forward references — attempt may not exist yet during
            # extractor-first pipelines. We don't validate from_attempt's
            # presence (unlike hypothesis_id which gates the edge).
            pass
        self.nodes[ev.id] = copy.deepcopy(ev)

        # Auto-create the support/refute edge (skipped for NEUTRAL).
        if ev.hypothesis_id is not None and ev.polarity is not Polarity.NEUTRAL:
            edge_kind = (
                EdgeKind.HYPOTHESIS_SUPPORTED_BY_EVIDENCE
                if ev.polarity is Polarity.SUPPORTS
                else EdgeKind.HYPOTHESIS_REFUTED_BY_EVIDENCE
            )
            self._add_edge_validated(
                Edge(
                    src_id=ev.hypothesis_id,
                    dst_id=ev.id,
                    kind=edge_kind,
                    weight=ev.confidence_delta,
                    run_id=ev.run_id,
                ),
                delta.kind,
            )

    def _apply_attempt_create(self, delta: AttemptCreateDelta) -> None:
        a = delta.attempt
        if a is None:
            raise InvalidDelta(delta.kind.value, "attempt is None")
        if a.id in self.nodes:
            raise InvalidDelta(delta.kind.value, f"id {a.id!r} already exists")
        # Validate every referenced hypothesis exists (fail-loud — silent
        # drops here would lose the planner's grounding).
        for hid in a.tested_hypothesis_ids:
            self._require_node(hid, NodeKind.HYPOTHESIS, delta.kind)
        if a.used_strategy_id is not None:
            self._require_node(a.used_strategy_id, NodeKind.STRATEGY, delta.kind)
        for eid in a.evidence_ids:
            # Evidence is allowed to NOT exist yet — extractor may emit
            # the attempt before the evidence stream lands. We DO require
            # any present id to be the right kind, though.
            present = self.nodes.get(eid)
            if present is not None and present.kind is not NodeKind.EVIDENCE:
                raise InvalidDelta(
                    delta.kind.value,
                    f"evidence_id {eid!r} resolves to non-evidence node",
                )
        self.nodes[a.id] = copy.deepcopy(a)

        # Mark every tested hypothesis as recently tested.
        for hid in a.tested_hypothesis_ids:
            h = self.nodes[hid]
            assert isinstance(h, WeaknessHypothesis)
            h.last_tested_at = a.created_at

        # Auto-emit edges — keep them all idempotent-friendly (just append).
        for hid in a.tested_hypothesis_ids:
            self._add_edge_validated(
                Edge(
                    src_id=a.id,
                    dst_id=hid,
                    kind=EdgeKind.ATTEMPT_TESTS_HYPOTHESIS,
                    run_id=a.run_id,
                ),
                delta.kind,
            )
        if a.used_strategy_id is not None:
            self._add_edge_validated(
                Edge(
                    src_id=a.id,
                    dst_id=a.used_strategy_id,
                    kind=EdgeKind.ATTEMPT_USED_STRATEGY,
                    run_id=a.run_id,
                ),
                delta.kind,
            )
        for eid in a.evidence_ids:
            if eid in self.nodes:
                self._add_edge_validated(
                    Edge(
                        src_id=a.id,
                        dst_id=eid,
                        kind=EdgeKind.ATTEMPT_OBSERVED_EVIDENCE,
                        run_id=a.run_id,
                    ),
                    delta.kind,
                )

        # Bookkeeping on the originating frontier experiment, when present.
        if a.experiment_id and a.experiment_id in self.nodes:
            fx = self.nodes[a.experiment_id]
            if isinstance(fx, FrontierExperiment):
                fx.fulfilled_by = a.id
                fx.state = ExperimentState.FULFILLED

    def _apply_strategy_create(self, delta: StrategyCreateDelta) -> None:
        s = delta.strategy
        if s is None:
            raise InvalidDelta(delta.kind.value, "strategy is None")
        if s.id in self.nodes:
            raise InvalidDelta(delta.kind.value, f"id {s.id!r} already exists")
        if not s.family:
            raise InvalidDelta(delta.kind.value, "family is required")
        self.nodes[s.id] = copy.deepcopy(s)

    def _apply_strategy_update_stats(self, delta: StrategyUpdateStatsDelta) -> None:
        node = self._require_node(delta.strategy_id, NodeKind.STRATEGY, delta.kind)
        assert isinstance(node, Strategy)
        node.success_count += delta.success_inc
        node.attempt_count += delta.attempt_inc

    def _apply_frontier_create(self, delta: FrontierCreateDelta) -> None:
        fx = delta.experiment
        if fx is None:
            raise InvalidDelta(delta.kind.value, "experiment is None")
        if fx.id in self.nodes:
            raise InvalidDelta(delta.kind.value, f"id {fx.id!r} already exists")
        self._require_node(fx.hypothesis_id, NodeKind.HYPOTHESIS, delta.kind)
        if fx.strategy_id is not None:
            self._require_node(fx.strategy_id, NodeKind.STRATEGY, delta.kind)
        self.nodes[fx.id] = copy.deepcopy(fx)

        # Auto-emit the expansion edge.
        self._add_edge_validated(
            Edge(
                src_id=fx.id,
                dst_id=fx.hypothesis_id,
                kind=EdgeKind.FRONTIER_EXPANDS_HYPOTHESIS,
                run_id=fx.run_id,
            ),
            delta.kind,
        )
        if fx.strategy_id is not None:
            self._add_edge_validated(
                Edge(
                    src_id=fx.id,
                    dst_id=fx.strategy_id,
                    kind=EdgeKind.FRONTIER_USES_STRATEGY,
                    run_id=fx.run_id,
                ),
                delta.kind,
            )

    def _apply_frontier_update_state(self, delta: FrontierUpdateStateDelta) -> None:
        node = self._require_node(delta.experiment_id, NodeKind.FRONTIER, delta.kind)
        assert isinstance(node, FrontierExperiment)
        node.state = delta.state
        if delta.fulfilled_by is not None:
            node.fulfilled_by = delta.fulfilled_by

    def _apply_frontier_rank(self, delta: FrontierRankDelta) -> None:
        for eid, scores in delta.rankings.items():
            node = self._require_node(eid, NodeKind.FRONTIER, delta.kind)
            assert isinstance(node, FrontierExperiment)
            for component in (
                "expected_progress",
                "information_gain",
                "novelty",
                "strategy_prior",
                "transfer_value",
                "query_cost",
                "repetition_penalty",
                "dead_similarity",
                "utility",
            ):
                if component in scores:
                    setattr(node, component, float(scores[component]))

    def _apply_edge_create(self, delta: EdgeCreateDelta) -> None:
        if delta.edge is None:
            raise InvalidDelta(delta.kind.value, "edge is None")
        self._add_edge_validated(delta.edge, delta.kind)

    # ---- edge validation helper ----

    def _add_edge_validated(self, edge: Edge, delta_kind: DeltaKind) -> None:
        contract = _EDGE_END_TYPES.get(edge.kind)
        if contract is None:
            raise InvalidDelta(
                delta_kind.value,
                f"unknown edge kind {edge.kind.value!r}",
            )
        src_kind, dst_kind = contract
        src = self.nodes.get(edge.src_id)
        dst = self.nodes.get(edge.dst_id)
        if src is None:
            raise InvalidDelta(
                delta_kind.value,
                f"edge src {edge.src_id!r} not in graph",
            )
        if dst is None:
            raise InvalidDelta(
                delta_kind.value,
                f"edge dst {edge.dst_id!r} not in graph",
            )
        if src.kind is not src_kind:
            raise InvalidDelta(
                delta_kind.value,
                f"edge {edge.kind.value} expected src kind {src_kind.value}, got {src.kind.value}",
            )
        if dst.kind is not dst_kind:
            raise InvalidDelta(
                delta_kind.value,
                f"edge {edge.kind.value} expected dst kind {dst_kind.value}, got {dst.kind.value}",
            )
        self.edges.append(edge)

    def _require_node(
        self, node_id: str, expected_kind: NodeKind, delta_kind: DeltaKind
    ) -> BeliefNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise InvalidDelta(
                delta_kind.value,
                f"node {node_id!r} not in graph",
            )
        if node.kind is not expected_kind:
            raise InvalidDelta(
                delta_kind.value,
                f"node {node_id!r} expected kind {expected_kind.value}, got {node.kind.value}",
            )
        return node

    # ---- queries ----

    def iter_nodes(self, kind: NodeKind | None = None) -> Iterator[BeliefNode]:
        if kind is None:
            yield from self.nodes.values()
            return
        for n in self.nodes.values():
            if n.kind is kind:
                yield n

    def hypotheses(
        self,
        *,
        status: HypothesisStatus | None = None,
        family: str | None = None,
    ) -> list[WeaknessHypothesis]:
        """Return hypotheses optionally filtered by status / family."""
        out: list[WeaknessHypothesis] = []
        for n in self.iter_nodes(NodeKind.HYPOTHESIS):
            assert isinstance(n, WeaknessHypothesis)
            if status is not None and n.status is not status:
                continue
            if family is not None and n.family != family:
                continue
            out.append(n)
        return out

    def active_hypotheses(self) -> list[WeaknessHypothesis]:
        """Active (non-CONFIRMED, non-REFUTED, non-STALE) hypotheses sorted
        confidence-descending — the ranking the planner cares about."""
        out = self.hypotheses(status=HypothesisStatus.ACTIVE)
        out.sort(key=lambda h: h.confidence, reverse=True)
        return out

    def evidence_for(
        self,
        hypothesis_id: str,
        *,
        polarity: Polarity | None = None,
    ) -> list[Evidence]:
        out: list[Evidence] = []
        for n in self.iter_nodes(NodeKind.EVIDENCE):
            assert isinstance(n, Evidence)
            if n.hypothesis_id != hypothesis_id:
                continue
            if polarity is not None and n.polarity is not polarity:
                continue
            out.append(n)
        out.sort(key=lambda e: e.created_at)
        return out

    def attempts_for(self, hypothesis_id: str) -> list[Attempt]:
        out: list[Attempt] = []
        for n in self.iter_nodes(NodeKind.ATTEMPT):
            assert isinstance(n, Attempt)
            if hypothesis_id in n.tested_hypothesis_ids:
                out.append(n)
        out.sort(key=lambda a: a.created_at)
        return out

    def proposed_frontier(self) -> list[FrontierExperiment]:
        """Frontier experiments still in PROPOSED state, sorted by
        utility descending. The planner's ready-queue."""
        out: list[FrontierExperiment] = []
        for n in self.iter_nodes(NodeKind.FRONTIER):
            assert isinstance(n, FrontierExperiment)
            if n.state is ExperimentState.PROPOSED:
                out.append(n)
        out.sort(key=lambda f: f.utility, reverse=True)
        return out

    def strategies(self, *, family: str | None = None) -> list[Strategy]:
        out: list[Strategy] = []
        for n in self.iter_nodes(NodeKind.STRATEGY):
            assert isinstance(n, Strategy)
            if family is not None and n.family != family:
                continue
            out.append(n)
        out.sort(key=lambda s: s.local_success_rate, reverse=True)
        return out

    def edges_from(self, src_id: str, *, kind: EdgeKind | None = None) -> list[Edge]:
        out: list[Edge] = []
        for e in self.edges:
            if e.src_id != src_id:
                continue
            if kind is not None and e.kind is not kind:
                continue
            out.append(e)
        return out

    def edges_to(self, dst_id: str, *, kind: EdgeKind | None = None) -> list[Edge]:
        out: list[Edge] = []
        for e in self.edges:
            if e.dst_id != dst_id:
                continue
            if kind is not None and e.kind is not kind:
                continue
            out.append(e)
        return out

    # ---- persistence ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "target_hash": self.target_hash,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BeliefGraph:
        graph = cls.__new__(cls)
        graph.target_hash = d.get("target_hash", "")
        graph.nodes = {}
        graph.edges = []
        graph.deltas = []
        for nd in d.get("nodes", []):
            node = _node_from_dict(nd)
            graph.nodes[node.id] = node
        for ed in d.get("edges", []):
            graph.edges.append(Edge.from_dict(ed))
        # Materialise the singleton if the snapshot somehow lacks one (old
        # corrupt file): better to reconstruct than to raise on read.
        if not any(isinstance(n, TargetNode) for n in graph.nodes.values()):
            target = TargetNode(
                id=_new_id(NodeKind.TARGET),
                target_hash=graph.target_hash,
            )
            graph.nodes[target.id] = target
        return graph

    @classmethod
    def from_json(cls, data: str | bytes) -> BeliefGraph:
        return cls.from_dict(json.loads(data))

    def save(self, snapshot_path: Path, *, delta_log_path: Path | None = None) -> None:
        """Write snapshot to ``snapshot_path``; append unsaved deltas to
        ``delta_log_path`` (one JSON object per line) when provided.

        Snapshot is the authoritative current state. Delta log is an
        audit trail; if it diverges from the snapshot, the snapshot
        wins on the next load. Callers who want delta-replay recovery
        should keep the delta log AND verify on boot.
        """
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(self.to_json(), encoding="utf-8")
        if delta_log_path is not None and self.deltas:
            with delta_log_path.open("a", encoding="utf-8") as fh:
                for delta in self.deltas:
                    fh.write(json.dumps(delta.to_dict(), sort_keys=True, default=str))
                    fh.write("\n")
            # Clear the in-memory queue; the JSONL file is now the
            # persistent record. Re-populating happens on apply().
            self.deltas = []

    @classmethod
    def load(cls, snapshot_path: Path) -> BeliefGraph:
        return cls.from_json(snapshot_path.read_text(encoding="utf-8"))

    @classmethod
    def replay(cls, delta_log_path: Path, *, target_hash: str = "") -> BeliefGraph:
        """Reconstruct a graph by replaying its delta log.

        Recovery path for when ``belief_graph.json`` is missing or
        corrupt. Slower than ``load`` (proportional to log length) but
        complete — every delta that was applied in-order rebuilds the
        same state.
        """
        graph = cls(target_hash=target_hash)
        for line in delta_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            graph.apply(_delta_from_dict(json.loads(line)))
        return graph

    # ---- summary ----

    def stats(self) -> dict[str, int]:
        """Counts by node kind plus edge total — surface for the web UI."""
        out: dict[str, int] = {k.value: 0 for k in NodeKind}
        for n in self.nodes.values():
            out[n.kind.value] += 1
        out["edges"] = len(self.edges)
        out["active_hypotheses"] = sum(
            1
            for n in self.nodes.values()
            if isinstance(n, WeaknessHypothesis) and n.status is HypothesisStatus.ACTIVE
        )
        out["proposed_frontier"] = sum(
            1
            for n in self.nodes.values()
            if isinstance(n, FrontierExperiment) and n.state is ExperimentState.PROPOSED
        )
        return out


# ---------------------------------------------------------------------------
# Convenience helpers — short-form builders for the extractor / updater
# ---------------------------------------------------------------------------


def make_hypothesis(
    *,
    claim: str,
    description: str,
    family: str,
    confidence: float = 0.5,
    run_id: str = "",
) -> WeaknessHypothesis:
    """Build a fresh :class:`WeaknessHypothesis` with a generated id."""
    return WeaknessHypothesis(
        id=_new_id(NodeKind.HYPOTHESIS),
        claim=claim,
        description=description,
        family=family,
        confidence=max(0.0, min(1.0, confidence)),
        status=HypothesisStatus.ACTIVE,
        run_id=run_id,
    )


def make_evidence(
    *,
    signal_type: EvidenceType,
    polarity: Polarity,
    verbatim_fragment: str,
    rationale: str,
    from_attempt: str = "",
    hypothesis_id: str | None = None,
    confidence_delta: float = 0.0,
    extractor_confidence: float = 1.0,
    run_id: str = "",
) -> Evidence:
    """Build a fresh :class:`Evidence` with a generated id."""
    return Evidence(
        id=_new_id(NodeKind.EVIDENCE),
        signal_type=signal_type,
        polarity=polarity,
        verbatim_fragment=verbatim_fragment,
        rationale=rationale,
        from_attempt=from_attempt,
        hypothesis_id=hypothesis_id,
        confidence_delta=abs(confidence_delta),
        extractor_confidence=max(0.0, min(1.0, extractor_confidence)),
        run_id=run_id,
    )


def make_attempt(
    *,
    module: str,
    approach: str,
    experiment_id: str | None = None,
    messages_sent: Iterable[str] = (),
    target_responses: Iterable[str] = (),
    module_output: str = "",
    judge_score: int = 0,
    objective_progress: float = 0.0,
    outcome: str = "",
    reflection: str = "",
    tested_hypothesis_ids: Iterable[str] = (),
    used_strategy_id: str | None = None,
    evidence_ids: Iterable[str] = (),
    run_id: str = "",
) -> Attempt:
    return Attempt(
        id=_new_id(NodeKind.ATTEMPT),
        module=module,
        approach=approach,
        experiment_id=experiment_id,
        messages_sent=list(messages_sent),
        target_responses=list(target_responses),
        module_output=module_output,
        judge_score=judge_score,
        objective_progress=objective_progress,
        outcome=outcome,
        reflection=reflection,
        tested_hypothesis_ids=list(tested_hypothesis_ids),
        used_strategy_id=used_strategy_id,
        evidence_ids=list(evidence_ids),
        run_id=run_id,
    )


def make_strategy(
    *,
    family: str,
    template_summary: str,
    works_against_traits: Iterable[str] = (),
    fails_against_traits: Iterable[str] = (),
    run_id: str = "",
) -> Strategy:
    return Strategy(
        id=_new_id(NodeKind.STRATEGY),
        family=family,
        template_summary=template_summary,
        works_against_traits=list(works_against_traits),
        fails_against_traits=list(fails_against_traits),
        run_id=run_id,
    )


def make_frontier(
    *,
    hypothesis_id: str,
    module: str,
    instruction: str,
    expected_signal: str,
    strategy_id: str | None = None,
    source: NodeSource = NodeSource.AGENT,
    run_id: str = "",
) -> FrontierExperiment:
    return FrontierExperiment(
        id=_new_id(NodeKind.FRONTIER),
        hypothesis_id=hypothesis_id,
        strategy_id=strategy_id,
        module=module,
        instruction=instruction,
        expected_signal=expected_signal,
        state=ExperimentState.PROPOSED,
        source=source,
        run_id=run_id,
    )


__all__ = [
    "Attempt",
    "AttemptCreateDelta",
    "BeliefGraph",
    "BeliefNode",
    "Edge",
    "EdgeCreateDelta",
    "Evidence",
    "EvidenceCreateDelta",
    "FrontierCreateDelta",
    "FrontierExperiment",
    "FrontierRankDelta",
    "FrontierUpdateStateDelta",
    "GraphDelta",
    "HypothesisCreateDelta",
    "HypothesisUpdateConfidenceDelta",
    "HypothesisUpdateStatusDelta",
    "NodeKind",
    "Strategy",
    "StrategyCreateDelta",
    "StrategyUpdateStatsDelta",
    "TargetNode",
    "TargetTraitsUpdateDelta",
    "WeaknessHypothesis",
    "make_attempt",
    "make_evidence",
    "make_frontier",
    "make_hypothesis",
    "make_strategy",
]
