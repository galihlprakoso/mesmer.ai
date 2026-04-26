"""Unit tests for mesmer.core.belief_graph.

Covers: typed-node construction, delta application for every DeltaKind,
edge endpoint validation, persistence round-trip, and delta-log replay.
The belief graph is the foundation everything else layers on, so the
test density here is intentionally high.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mesmer.core.belief_graph import (
    AttemptCreateDelta,
    BeliefGraph,
    Edge,
    EdgeCreateDelta,
    EvidenceCreateDelta,
    FrontierCreateDelta,
    FrontierRankDelta,
    FrontierUpdateStateDelta,
    HypothesisCreateDelta,
    HypothesisUpdateConfidenceDelta,
    HypothesisUpdateStatusDelta,
    NodeKind,
    StrategyCreateDelta,
    StrategyUpdateStatsDelta,
    TargetTraitsUpdateDelta,
    WeaknessHypothesis,
    make_attempt,
    make_evidence,
    make_frontier,
    make_hypothesis,
    make_strategy,
)
from mesmer.core.constants import (
    EdgeKind,
    EvidenceType,
    ExperimentState,
    HypothesisStatus,
    NodeSource,
    Polarity,
)
from mesmer.core.errors import InvalidDelta


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_construct_creates_singleton_target() -> None:
    g = BeliefGraph(target_hash="abc")
    assert g.target.target_hash == "abc"
    assert g.target.kind is NodeKind.TARGET
    # No other nodes pre-seeded.
    assert len(g.nodes) == 1


def test_target_property_raises_when_singleton_missing() -> None:
    g = BeliefGraph(target_hash="abc")
    # Manually corrupt the graph and verify .target raises.
    g.nodes.clear()
    with pytest.raises(InvalidDelta):
        _ = g.target


# ---------------------------------------------------------------------------
# TargetTraitsUpdate
# ---------------------------------------------------------------------------

def test_target_traits_update_merges() -> None:
    g = BeliefGraph(target_hash="abc")
    g.apply(TargetTraitsUpdateDelta(traits={"system_prompt_hint": "hello"}))
    g.apply(TargetTraitsUpdateDelta(traits={"tool_catalog": "(none)"}))
    assert g.target.traits == {
        "system_prompt_hint": "hello",
        "tool_catalog": "(none)",
    }


def test_target_traits_latest_wins_per_key() -> None:
    g = BeliefGraph(target_hash="abc")
    g.apply(TargetTraitsUpdateDelta(traits={"key": "v1"}))
    g.apply(TargetTraitsUpdateDelta(traits={"key": "v2"}))
    assert g.target.traits == {"key": "v2"}


# ---------------------------------------------------------------------------
# Hypothesis lifecycle
# ---------------------------------------------------------------------------

def test_hypothesis_create_inserts() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.5)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    # Stored under the same id with equal field values; the graph
    # deep-copies on insert so identity (`is`) is intentionally not
    # preserved (the copy is what subsequent updates mutate).
    stored = g.nodes[h.id]
    assert isinstance(stored, WeaknessHypothesis)
    assert stored.id == h.id
    assert stored.claim == h.claim
    assert stored.confidence == h.confidence


def test_hypothesis_create_rejects_duplicate_id() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    with pytest.raises(InvalidDelta):
        g.apply(HypothesisCreateDelta(hypothesis=h))


def test_hypothesis_create_requires_family() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="")
    with pytest.raises(InvalidDelta):
        g.apply(HypothesisCreateDelta(hypothesis=h))


def test_hypothesis_create_rejects_none() -> None:
    g = BeliefGraph()
    with pytest.raises(InvalidDelta):
        g.apply(HypothesisCreateDelta(hypothesis=None))


def test_hypothesis_update_confidence_shifts_and_clamps() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.5)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    g.apply(HypothesisUpdateConfidenceDelta(hypothesis_id=h.id, delta_value=0.3))
    assert pytest.approx(g.nodes[h.id].confidence, abs=1e-6) == 0.8

    # Clamps at 1.0.
    g.apply(HypothesisUpdateConfidenceDelta(hypothesis_id=h.id, delta_value=0.5))
    assert g.nodes[h.id].confidence == 1.0

    # Clamps at 0.0.
    g.apply(HypothesisUpdateConfidenceDelta(hypothesis_id=h.id, delta_value=-2.0))
    assert g.nodes[h.id].confidence == 0.0


def test_hypothesis_update_confidence_unknown_id_raises() -> None:
    g = BeliefGraph()
    with pytest.raises(InvalidDelta):
        g.apply(HypothesisUpdateConfidenceDelta(hypothesis_id="wh_missing", delta_value=0.1))


def test_hypothesis_update_status_flips() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    g.apply(
        HypothesisUpdateStatusDelta(
            hypothesis_id=h.id, status=HypothesisStatus.CONFIRMED
        )
    )
    assert g.nodes[h.id].status is HypothesisStatus.CONFIRMED


def test_hypothesis_update_status_rejects_non_hypothesis_id() -> None:
    g = BeliefGraph()
    # Pass the singleton target id — wrong kind.
    with pytest.raises(InvalidDelta):
        g.apply(
            HypothesisUpdateStatusDelta(
                hypothesis_id=g.target.id, status=HypothesisStatus.CONFIRMED
            )
        )


# ---------------------------------------------------------------------------
# Evidence + auto-edge
# ---------------------------------------------------------------------------

def test_evidence_create_attaches_support_edge() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    ev = make_evidence(
        signal_type=EvidenceType.PARTIAL_COMPLIANCE,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h.id,
        confidence_delta=0.18,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))
    assert ev.id in g.nodes
    edges = g.edges_to(ev.id)
    assert len(edges) == 1
    assert edges[0].kind is EdgeKind.HYPOTHESIS_SUPPORTED_BY_EVIDENCE


def test_evidence_create_attaches_refute_edge() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    ev = make_evidence(
        signal_type=EvidenceType.REFUSAL_TEMPLATE,
        polarity=Polarity.REFUTES,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h.id,
        confidence_delta=0.10,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))
    edges = g.edges_to(ev.id)
    assert len(edges) == 1
    assert edges[0].kind is EdgeKind.HYPOTHESIS_REFUTED_BY_EVIDENCE


def test_evidence_create_neutral_no_edge() -> None:
    g = BeliefGraph()
    ev = make_evidence(
        signal_type=EvidenceType.UNKNOWN,
        polarity=Polarity.NEUTRAL,
        verbatim_fragment="x",
        rationale="ambient signal",
    )
    g.apply(EvidenceCreateDelta(evidence=ev))
    assert g.edges == []


def test_evidence_create_unknown_hypothesis_raises() -> None:
    g = BeliefGraph()
    ev = make_evidence(
        signal_type=EvidenceType.PARTIAL_COMPLIANCE,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id="wh_missing",
        confidence_delta=0.1,
    )
    with pytest.raises(InvalidDelta):
        g.apply(EvidenceCreateDelta(evidence=ev))


# ---------------------------------------------------------------------------
# Strategy + Attempt + Frontier
# ---------------------------------------------------------------------------

def test_strategy_create_and_update_stats() -> None:
    g = BeliefGraph()
    s = make_strategy(family="format-shift", template_summary="t")
    g.apply(StrategyCreateDelta(strategy=s))
    g.apply(StrategyUpdateStatsDelta(strategy_id=s.id, success_inc=1, attempt_inc=2))
    g.apply(StrategyUpdateStatsDelta(strategy_id=s.id, success_inc=2, attempt_inc=3))
    node = g.nodes[s.id]
    assert node.success_count == 3
    assert node.attempt_count == 5
    assert pytest.approx(node.local_success_rate, abs=1e-6) == 0.6


def test_attempt_create_emits_edges_and_marks_hypothesis_tested() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    s = make_strategy(family="format-shift", template_summary="t")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    g.apply(StrategyCreateDelta(strategy=s))

    attempt = make_attempt(
        module="format-shift",
        approach="ask for yaml",
        tested_hypothesis_ids=[h.id],
        used_strategy_id=s.id,
    )
    g.apply(AttemptCreateDelta(attempt=attempt))

    # Graph deep-copies on insert; same id, equal fields, distinct object.
    assert attempt.id in g.nodes
    assert g.nodes[h.id].last_tested_at is not None
    edge_kinds = {e.kind for e in g.edges_from(attempt.id)}
    assert EdgeKind.ATTEMPT_TESTS_HYPOTHESIS in edge_kinds
    assert EdgeKind.ATTEMPT_USED_STRATEGY in edge_kinds


def test_attempt_create_unknown_hypothesis_raises() -> None:
    g = BeliefGraph()
    a = make_attempt(
        module="x",
        approach="y",
        tested_hypothesis_ids=["wh_missing"],
    )
    with pytest.raises(InvalidDelta):
        g.apply(AttemptCreateDelta(attempt=a))


def test_attempt_create_evidence_id_wrong_kind_raises() -> None:
    g = BeliefGraph()
    s = make_strategy(family="format-shift", template_summary="t")
    g.apply(StrategyCreateDelta(strategy=s))
    a = make_attempt(
        module="x",
        approach="y",
        evidence_ids=[s.id],  # strategy id under evidence_ids slot
    )
    with pytest.raises(InvalidDelta):
        g.apply(AttemptCreateDelta(attempt=a))


def test_frontier_create_emits_expand_edge() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(
        hypothesis_id=h.id,
        module="format-shift",
        instruction="ask",
        expected_signal="leak",
    )
    g.apply(FrontierCreateDelta(experiment=f))
    edge = g.edges_from(f.id, kind=EdgeKind.FRONTIER_EXPANDS_HYPOTHESIS)
    assert len(edge) == 1
    assert edge[0].dst_id == h.id


def test_frontier_create_unknown_hypothesis_raises() -> None:
    g = BeliefGraph()
    f = make_frontier(
        hypothesis_id="wh_missing",
        module="x",
        instruction="y",
        expected_signal="z",
    )
    with pytest.raises(InvalidDelta):
        g.apply(FrontierCreateDelta(experiment=f))


def test_frontier_update_state_and_attempt_link() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(
        hypothesis_id=h.id, module="m", instruction="i", expected_signal="e"
    )
    g.apply(FrontierCreateDelta(experiment=f))

    a = make_attempt(
        module="m",
        approach="ap",
        experiment_id=f.id,
        tested_hypothesis_ids=[h.id],
    )
    g.apply(AttemptCreateDelta(attempt=a))

    # Attempt-create should auto-promote frontier to FULFILLED.
    assert g.nodes[f.id].state is ExperimentState.FULFILLED
    assert g.nodes[f.id].fulfilled_by == a.id


def test_frontier_rank_writes_components() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(hypothesis_id=h.id, module="m", instruction="i", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f))

    rank = FrontierRankDelta(
        rankings={
            f.id: {
                "expected_progress": 0.7,
                "information_gain": 0.5,
                "novelty": 0.9,
                "strategy_prior": 0.5,
                "transfer_value": 0.0,
                "query_cost": 0.3,
                "repetition_penalty": 0.0,
                "dead_similarity": 0.0,
                "utility": 0.62,
            }
        }
    )
    g.apply(rank)
    fx = g.nodes[f.id]
    assert pytest.approx(fx.utility, abs=1e-6) == 0.62
    assert pytest.approx(fx.expected_progress, abs=1e-6) == 0.7


# ---------------------------------------------------------------------------
# Edge endpoint validation
# ---------------------------------------------------------------------------

def test_edge_create_requires_correct_endpoint_kinds() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="format-shift")
    s = make_strategy(family="format-shift", template_summary="t")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    g.apply(StrategyCreateDelta(strategy=s))

    # ATTEMPT_TESTS_HYPOTHESIS expects (attempt -> hypothesis) — pass
    # (hypothesis -> strategy) and verify it raises.
    bad = Edge(
        src_id=h.id,
        dst_id=s.id,
        kind=EdgeKind.ATTEMPT_TESTS_HYPOTHESIS,
    )
    with pytest.raises(InvalidDelta):
        g.apply(EdgeCreateDelta(edge=bad))


def test_edge_create_unknown_node_raises() -> None:
    g = BeliefGraph()
    bad = Edge(
        src_id="nope",
        dst_id="nada",
        kind=EdgeKind.HYPOTHESIS_GENERALIZES_TO,
    )
    with pytest.raises(InvalidDelta):
        g.apply(EdgeCreateDelta(edge=bad))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def test_active_hypotheses_sorted_by_confidence_desc() -> None:
    g = BeliefGraph()
    h_low = make_hypothesis(claim="lo", description="d", family="x", confidence=0.3)
    h_hi = make_hypothesis(claim="hi", description="d", family="y", confidence=0.8)
    h_refuted = make_hypothesis(claim="rf", description="d", family="z", confidence=0.05)
    g.apply(HypothesisCreateDelta(hypothesis=h_low))
    g.apply(HypothesisCreateDelta(hypothesis=h_hi))
    g.apply(HypothesisCreateDelta(hypothesis=h_refuted))
    g.apply(
        HypothesisUpdateStatusDelta(
            hypothesis_id=h_refuted.id, status=HypothesisStatus.REFUTED
        )
    )
    out = g.active_hypotheses()
    ids = [h.id for h in out]
    assert ids == [h_hi.id, h_low.id]


def test_proposed_frontier_sorted_by_utility_desc() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="x")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f1 = make_frontier(hypothesis_id=h.id, module="m", instruction="i1", expected_signal="e")
    f2 = make_frontier(hypothesis_id=h.id, module="m", instruction="i2", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f1))
    g.apply(FrontierCreateDelta(experiment=f2))
    g.apply(
        FrontierRankDelta(
            rankings={
                f1.id: {"utility": 0.3},
                f2.id: {"utility": 0.7},
            }
        )
    )
    out = g.proposed_frontier()
    assert [f.id for f in out] == [f2.id, f1.id]


def test_proposed_frontier_excludes_fulfilled() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="x")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f1 = make_frontier(hypothesis_id=h.id, module="m", instruction="i1", expected_signal="e")
    g.apply(FrontierCreateDelta(experiment=f1))
    g.apply(
        FrontierUpdateStateDelta(
            experiment_id=f1.id,
            state=ExperimentState.FULFILLED,
            fulfilled_by="at_test",
        )
    )
    assert g.proposed_frontier() == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_to_json_round_trip(tmp_path: Path) -> None:
    g = BeliefGraph(target_hash="t")
    h = make_hypothesis(claim="c", description="d", family="format-shift", confidence=0.6)
    g.apply(HypothesisCreateDelta(hypothesis=h))
    ev = make_evidence(
        signal_type=EvidenceType.PARTIAL_COMPLIANCE,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="x",
        rationale="r",
        hypothesis_id=h.id,
        confidence_delta=0.18,
    )
    g.apply(EvidenceCreateDelta(evidence=ev))

    snapshot = tmp_path / "belief_graph.json"
    delta_log = tmp_path / "belief_deltas.jsonl"
    g.save(snapshot, delta_log_path=delta_log)

    g2 = BeliefGraph.load(snapshot)
    assert g2.target_hash == "t"
    assert h.id in g2.nodes
    assert ev.id in g2.nodes
    assert len(g2.edges) == 1
    # Saving consumed the delta queue.
    assert g.deltas == []


def test_replay_reconstructs_state(tmp_path: Path) -> None:
    g = BeliefGraph(target_hash="t")
    h = make_hypothesis(
        claim="c", description="d", family="format-shift", confidence=0.5
    )
    g.apply(HypothesisCreateDelta(hypothesis=h))
    g.apply(HypothesisUpdateConfidenceDelta(hypothesis_id=h.id, delta_value=0.2))
    g.apply(
        HypothesisUpdateStatusDelta(
            hypothesis_id=h.id, status=HypothesisStatus.CONFIRMED
        )
    )

    delta_log = tmp_path / "belief_deltas.jsonl"
    snapshot = tmp_path / "belief_graph.json"
    g.save(snapshot, delta_log_path=delta_log)

    g2 = BeliefGraph.replay(delta_log, target_hash="t")
    h2 = next(
        n for n in g2.nodes.values() if isinstance(n, WeaknessHypothesis)
    )
    assert pytest.approx(h2.confidence, abs=1e-6) == 0.7
    assert h2.status is HypothesisStatus.CONFIRMED


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    g = BeliefGraph(target_hash="t")
    deep = tmp_path / "a" / "b" / "belief_graph.json"
    g.save(deep)
    assert deep.exists()


def test_stats_counts_kinds() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="x")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(
        hypothesis_id=h.id, module="m", instruction="i", expected_signal="e"
    )
    g.apply(FrontierCreateDelta(experiment=f))

    stats = g.stats()
    assert stats["target"] == 1
    assert stats["hypothesis"] == 1
    assert stats["frontier"] == 1
    assert stats["active_hypotheses"] == 1
    assert stats["proposed_frontier"] == 1


# ---------------------------------------------------------------------------
# Source preservation on FrontierExperiment
# ---------------------------------------------------------------------------

def test_frontier_human_source_persists() -> None:
    g = BeliefGraph()
    h = make_hypothesis(claim="c", description="d", family="x")
    g.apply(HypothesisCreateDelta(hypothesis=h))
    f = make_frontier(
        hypothesis_id=h.id,
        module="m",
        instruction="i",
        expected_signal="e",
        source=NodeSource.HUMAN,
    )
    g.apply(FrontierCreateDelta(experiment=f))
    assert g.nodes[f.id].source is NodeSource.HUMAN

    snapshot_text = g.to_json()
    g2 = BeliefGraph.from_json(snapshot_text)
    assert g2.nodes[f.id].source is NodeSource.HUMAN
