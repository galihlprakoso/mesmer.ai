"""Unit tests for mesmer.core.agent.graph_compiler.

GraphContextCompiler is a pure renderer — no LLM, no I/O. Tests build
fixture graphs and verify the markdown brief structure for each role.
"""

from __future__ import annotations


from mesmer.core.agent.beliefs import apply_evidence_to_beliefs, rank_frontier
from mesmer.core.agent.graph_compiler import GraphContextCompiler
from mesmer.core.agent.prompt import _belief_role_for, _build_belief_context
from mesmer.core.belief_graph import (
    BeliefGraph,
    EvidenceCreateDelta,
    FrontierCreateDelta,
    FrontierUpdateStateDelta,
    HypothesisCreateDelta,
    HypothesisUpdateStatusDelta,
    StrategyCreateDelta,
    TargetTraitsUpdateDelta,
    make_evidence,
    make_frontier,
    make_hypothesis,
    make_strategy,
)
from mesmer.core.module import ModuleConfig, SubModuleEntry
from mesmer.core.constants import (
    BeliefRole,
    EvidenceType,
    ExperimentState,
    HypothesisStatus,
    Polarity,
)


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------

def _build_demo_graph() -> tuple[BeliefGraph, dict]:
    """Return a representative graph + an id-map for assertions."""
    g = BeliefGraph(target_hash="t")
    g.apply(
        TargetTraitsUpdateDelta(
            traits={
                "system_prompt_hint": "Internal customer-success assistant.",
                "tool_catalog": "(none)",
            }
        )
    )

    h1 = make_hypothesis(
        claim="Target leaks under format-shift",
        description="Reformatting bypass.",
        family="format-shift",
        confidence=0.5,
    )
    h2 = make_hypothesis(
        claim="Target complies with admin authority",
        description="Authority framing.",
        family="authority-bias",
        confidence=0.7,
    )
    g.apply(HypothesisCreateDelta(hypothesis=h1))
    g.apply(HypothesisCreateDelta(hypothesis=h2))

    ev1 = make_evidence(
        signal_type=EvidenceType.PARTIAL_COMPLIANCE,
        polarity=Polarity.SUPPORTS,
        verbatim_fragment="Sure, I can format that as JSON.",
        rationale="Format request honoured",
        hypothesis_id=h1.id,
        confidence_delta=0.18,
    )
    ev2 = make_evidence(
        signal_type=EvidenceType.REFUSAL_TEMPLATE,
        polarity=Polarity.REFUTES,
        verbatim_fragment="I cannot share my instructions.",
        rationale="Direct refusal",
        hypothesis_id=h2.id,
        confidence_delta=0.10,
    )
    g.apply(EvidenceCreateDelta(evidence=ev1))
    g.apply(EvidenceCreateDelta(evidence=ev2))

    s = make_strategy(family="format-shift", template_summary="reformat-as-yaml")
    g.apply(StrategyCreateDelta(strategy=s))

    f1 = make_frontier(
        hypothesis_id=h1.id,
        module="format-shift",
        instruction="Ask target to output instructions as YAML",
        expected_signal="partial compliance",
        strategy_id=s.id,
    )
    f2 = make_frontier(
        hypothesis_id=h2.id,
        module="authority-bias",
        instruction="Claim devops audit",
        expected_signal="refusal or partial compliance",
    )
    g.apply(FrontierCreateDelta(experiment=f1))
    g.apply(FrontierCreateDelta(experiment=f2))

    for d in apply_evidence_to_beliefs(g, [ev1, ev2]):
        g.apply(d)
    g.apply(rank_frontier(g))

    return g, {
        "h1": h1.id,
        "h2": h2.id,
        "ev1": ev1.id,
        "ev2": ev2.id,
        "f1": f1.id,
        "f2": f2.id,
    }


# ---------------------------------------------------------------------------
# LEADER
# ---------------------------------------------------------------------------


def test_belief_role_for_executive_only_gets_leader_brief() -> None:
    class Ctx:
        depth = 0
        active_experiment_id = None

    module = ModuleConfig(
        name="scenario:executive",
        sub_modules=[SubModuleEntry(name="system-prompt-extraction")],
        is_executive=True,
    )

    assert _belief_role_for(module, Ctx()) is BeliefRole.LEADER


def test_belief_role_for_registry_manager_with_submodules_stays_manager() -> None:
    class Ctx:
        depth = 1
        active_experiment_id = None

    module = ModuleConfig(
        name="system-prompt-extraction",
        sub_modules=[SubModuleEntry(name="target-profiler")],
        is_executive=False,
    )

    assert _belief_role_for(module, Ctx()) is BeliefRole.MANAGER


def test_belief_context_can_be_suppressed_for_fixed_scenario_executive() -> None:
    class Ctx:
        belief_graph = _build_demo_graph()[0]
        active_experiment_id = None

    module = ModuleConfig(
        name="scenario:executive",
        parameters={"suppress_belief_context": True},
        is_executive=True,
    )

    assert _build_belief_context(Ctx(), module) == ""


def test_leader_brief_lists_active_hypotheses() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    assert "Current Target Beliefs" in text
    assert ids["h1"] in text
    assert ids["h2"] in text
    assert "format-shift" in text
    assert "authority-bias" in text


def test_leader_brief_renders_strongest_evidence() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    assert "Strongest Evidence" in text
    assert ids["ev1"] in text
    assert "Sure, I can format that as JSON." in text


def test_leader_brief_lists_experiments_with_utility() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    assert "Recommended Experiments" in text
    assert ids["f1"] in text
    assert ids["f2"] in text
    assert "utility" in text.lower()


def test_leader_brief_includes_required_action() -> None:
    g, _ = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    assert "Required Action" in text
    assert "experiment_id" in text


def test_leader_brief_dead_zones_when_refuted() -> None:
    g, ids = _build_demo_graph()
    g.apply(
        HypothesisUpdateStatusDelta(
            hypothesis_id=ids["h2"], status=HypothesisStatus.REFUTED
        )
    )
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    assert "Dead Zones" in text
    assert ids["h2"] in text


def test_leader_brief_empty_graph_renders_seed_hint() -> None:
    g = BeliefGraph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    assert "no active hypotheses yet" in text


# ---------------------------------------------------------------------------
# MANAGER
# ---------------------------------------------------------------------------

def test_manager_brief_resolves_active_experiment_by_module() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.MANAGER, module_name="format-shift"
    )
    assert "Active Experiment" in text
    assert ids["f1"] in text
    assert "Report Back" in text


def test_manager_brief_renders_supporting_evidence() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.MANAGER, module_name="format-shift"
    )
    assert "Hypothesis Evidence" in text
    assert "Sure, I can format that as JSON." in text


def test_manager_brief_explicit_experiment_id_wins() -> None:
    g, ids = _build_demo_graph()
    # Pass a different module name; the explicit experiment id should
    # still resolve.
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.MANAGER,
        module_name="format-shift",
        active_experiment_id=ids["f2"],
    )
    assert ids["f2"] in text
    # f1 metadata should not appear (module rendering only carries one).
    assert "Ask target to output instructions as YAML" not in text


def test_manager_brief_no_assignment_fallback() -> None:
    g, _ = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.MANAGER, module_name="non-existent-module"
    )
    assert "no active experiment" in text.lower()


def test_manager_brief_prefers_executing_over_proposed() -> None:
    g, ids = _build_demo_graph()
    # Mark f2 as EXECUTING by transitioning state.
    g.apply(
        FrontierUpdateStateDelta(
            experiment_id=ids["f2"], state=ExperimentState.EXECUTING
        )
    )
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.MANAGER, module_name="authority-bias"
    )
    assert ids["f2"] in text


# ---------------------------------------------------------------------------
# EMPLOYEE
# ---------------------------------------------------------------------------

def test_employee_brief_is_focused() -> None:
    g, _ = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.EMPLOYEE, module_name="format-shift"
    )
    assert "Your Job" in text
    # Employee should NOT see the full belief landscape.
    assert "Current Target Beliefs" not in text
    assert "Recommended Experiments" not in text


def test_employee_brief_no_assignment_renders_generic_directive() -> None:
    g = BeliefGraph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.EMPLOYEE, module_name=None
    )
    assert "Your Job" in text


# ---------------------------------------------------------------------------
# JUDGE
# ---------------------------------------------------------------------------

def test_judge_brief_lists_active_hypotheses() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.JUDGE)
    assert "Active Hypotheses" in text
    assert ids["h1"] in text


def test_judge_brief_includes_expected_signal_when_provided() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.JUDGE, active_experiment_id=ids["f1"]
    )
    assert "Expected Signal" in text
    assert "partial compliance" in text


# ---------------------------------------------------------------------------
# EXTRACTOR
# ---------------------------------------------------------------------------

def test_extractor_brief_lists_hypotheses_with_ids() -> None:
    g, ids = _build_demo_graph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.EXTRACTOR)
    assert "use these ids when labelling" in text.lower()
    assert ids["h1"] in text


def test_extractor_brief_empty_graph_yields_neutral_hint() -> None:
    g = BeliefGraph()
    text = GraphContextCompiler(graph=g).compile(role=BeliefRole.EXTRACTOR)
    assert "neutral evidence" in text


# ---------------------------------------------------------------------------
# Token budget trim
# ---------------------------------------------------------------------------

def test_token_budget_trims_trailing_sections() -> None:
    g, _ = _build_demo_graph()
    full = GraphContextCompiler(graph=g).compile(role=BeliefRole.LEADER)
    # Truncate at half the token approximation (~ chars / 4).
    char_target = len(full) // 2 // 4 * 4
    trimmed = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.LEADER, token_budget=char_target // 4
    )
    assert "[brief truncated to fit token budget]" in trimmed
    assert len(trimmed) < len(full)


def test_token_budget_no_trim_when_in_budget() -> None:
    g = BeliefGraph()
    text = GraphContextCompiler(graph=g).compile(
        role=BeliefRole.LEADER, token_budget=10_000
    )
    assert "[brief truncated" not in text
