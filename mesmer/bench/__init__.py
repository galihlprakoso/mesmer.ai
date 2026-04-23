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

Everything the CLI and tests import is re-exported here.
"""

from mesmer.bench.canary import (
    CanaryJudgeResult,
    find_canary_in_turns,
    scan_canary,
)
from mesmer.bench.orchestrator import (
    BenchAttackerSpec,
    BenchBudget,
    BenchCellSummary,
    BenchDatasetSpec,
    BenchSpec,
    BenchSummary,
    BenchTargetSpec,
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

__all__ = [
    # Orchestrator
    "BenchAttackerSpec",
    "BenchBudget",
    "BenchCellSummary",
    "BenchDatasetSpec",
    "BenchSpec",
    "BenchSummary",
    "BenchTargetSpec",
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
    # Canary judge
    "CanaryJudgeResult",
    "find_canary_in_turns",
    "scan_canary",
]
