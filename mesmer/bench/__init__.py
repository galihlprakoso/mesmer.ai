"""Benchmark infrastructure — measures the attacker runtime from outside.

``mesmer.core`` is the attacker agent at runtime. ``mesmer.bench`` is the
harness that drives many runs of that agent over a dataset of defenses and
reports aggregate numbers. Bench *consumes* core (scenario, runner,
execute_run, Turn); core has no reverse dependency on bench.

Submodules:

  * :mod:`mesmer.bench.orchestrator` — spec loader, trial dispatch,
    aggregation, artifact writing (the bulk of the package).
  * :mod:`mesmer.bench.canary` — deterministic substring judge. Pure
    function over a run's assistant output; reproducible, no LLM.
  * :mod:`mesmer.bench.trace` — per-trial event capture +
    post-run telemetry extraction (tier/module/score/win attribution).
  * :mod:`mesmer.bench.belief_eval` — pure BeliefGraph planner-quality
    metrics for calibration, frontier regret, duplicate probes, and
    no-observation rates.

Everything the CLI and tests import is re-exported here.
"""

from mesmer.bench.canary import (
    CanaryJudgeResult,
    find_canary_in_turns,
    scan_canary,
)
from mesmer.bench.belief_eval import (
    BeliefPlannerMetrics,
    aggregate_belief_planner_metrics,
    evaluate_belief_planner,
)
from mesmer.bench.orchestrator import (
    BenchAgentSpec,
    BenchBudget,
    BenchCellSummary,
    BenchDatasetSpec,
    BenchJudgeSpec,
    BenchSpec,
    BenchSummary,
    BenchTargetSpec,
    BenchTargetPromptSpec,
    ContaminationPosture,
    DatasetRow,
    TrialResult,
    aggregate,
    build_scenario_for_row,
    ensure_dataset_cached,
    load_dataset,
    load_spec,
    render_markdown_table,
    run_benchmark,
)
from mesmer.bench.trace import (
    BenchEventRecorder,
    TrialTelemetry,
    extract_trial_telemetry,
    write_trial_graph_snapshot,
)
from mesmer.bench.viz import (
    VIZ_INLINE_BYTES_LIMIT,
    VizResult,
    build_viz_html,
)

__all__ = [
    # Orchestrator
    "BenchAgentSpec",
    "BenchBudget",
    "BenchCellSummary",
    "BenchDatasetSpec",
    "BenchJudgeSpec",
    "BenchSpec",
    "BenchSummary",
    "BenchTargetSpec",
    "BenchTargetPromptSpec",
    "ContaminationPosture",
    "DatasetRow",
    "TrialResult",
    "aggregate",
    "build_scenario_for_row",
    "ensure_dataset_cached",
    "load_dataset",
    "load_spec",
    "render_markdown_table",
    "run_benchmark",
    # Belief planner eval
    "BeliefPlannerMetrics",
    "aggregate_belief_planner_metrics",
    "evaluate_belief_planner",
    # Canary judge
    "CanaryJudgeResult",
    "find_canary_in_turns",
    "scan_canary",
    # Trace
    "BenchEventRecorder",
    "TrialTelemetry",
    "extract_trial_telemetry",
    "write_trial_graph_snapshot",
    # Viz
    "VIZ_INLINE_BYTES_LIMIT",
    "VizResult",
    "build_viz_html",
]
