"""Run orchestration — shared between CLI and Web UI.

Extracts the core "run an attack" logic so both interfaces call the same code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mesmer.core.context import Context, HumanQuestionBroker
from mesmer.core.graph import AttackGraph
from mesmer.core.loop import LogFn, run_react_loop
from mesmer.core.memory import TargetMemory, GlobalMemory, generate_run_id
from mesmer.core.registry import Registry
from mesmer.core.scenario import load_scenario, AgentConfig, Scenario

# Default module paths — relative to project root
BUILTIN_MODULES = Path(__file__).parent.parent.parent / "modules"


@dataclass
class RunConfig:
    """Configuration for an attack run."""

    scenario_path: str
    model_override: str | None = None
    max_turns_override: int | None = None
    hints: list[str] = field(default_factory=list)
    hint_file: str | None = None
    fresh: bool = False
    extra_module_paths: list[str] = field(default_factory=list)
    output_path: str | None = None
    mode: str = "autonomous"  # 'autonomous' | 'co-op' | 'plan'
    human_broker: "HumanQuestionBroker | None" = None


@dataclass
class RunResult:
    """Result of an attack run."""

    run_id: str
    scenario: Scenario
    result: str
    ctx: Context
    graph: AttackGraph
    memory: TargetMemory


async def execute_run(
    config: RunConfig,
    log: LogFn | None = None,
    on_graph_update: Callable[[AttackGraph], None] | None = None,
    on_pool_ready: Callable[[object], None] | None = None,
) -> RunResult:
    """
    Execute an attack run. This is the core orchestration shared by CLI and web.

    Args:
        config: Run configuration
        log: Optional logging callback (event, detail) → None
        on_graph_update: Optional callback when graph changes (for web UI)
        on_pool_ready: Optional callback receiving the agent's KeyPool once
            configured, so the web UI can broadcast key_status events.

    Returns:
        RunResult with all run data
    """
    from mesmer.targets import create_target

    # Load scenario
    scenario = load_scenario(config.scenario_path)
    run_id = generate_run_id()

    # Build registry
    registry = Registry()
    registry.auto_discover(BUILTIN_MODULES)
    for p in scenario.module_paths:
        registry.auto_discover(p)
    for p in config.extra_module_paths:
        registry.auto_discover(p)

    # Agent config — apply overrides
    agent_config = scenario.agent
    if config.model_override:
        agent_config = AgentConfig(
            model=config.model_override,
            api_key=agent_config.api_key,
            api_base=agent_config.api_base,
            temperature=agent_config.temperature,
            max_tokens=agent_config.max_tokens,
            extra=agent_config.extra,
        )

    # Let callers grab the pool now (web UI uses this to push key_status events)
    if on_pool_ready is not None and agent_config.pool is not None:
        on_pool_ready(agent_config.pool)

    # Check entry module exists
    entry = registry.get(scenario.module)
    if entry is None:
        available = ", ".join(sorted(registry.modules.keys()))
        raise ValueError(f"Module '{scenario.module}' not found. Available: {available}")

    # Create target
    target = create_target(scenario.target)

    # Load or create attack graph
    memory = TargetMemory(scenario.target)
    if config.fresh or not memory.exists():
        graph = AttackGraph()
    else:
        graph = memory.load_graph()

    graph.ensure_root()
    graph.run_counter += 1

    # Add human hints to graph
    all_hints = list(config.hints)
    if config.hint_file:
        all_hints.append(Path(config.hint_file).read_text().strip())

    for h in all_hints:
        if h.strip():
            graph.add_human_hint(h.strip(), run_id=run_id)

    # Create context
    max_turns = config.max_turns_override or scenario.objective.max_turns
    plan_md = memory.load_plan()  # None if no plan.md for this target
    ctx = Context(
        target=target,
        registry=registry,
        agent_config=agent_config,
        objective=scenario.objective.goal,
        success_signals=scenario.objective.success_signals,
        max_turns=max_turns,
        graph=graph,
        run_id=run_id,
        mode=config.mode,
        human_broker=config.human_broker,
        plan=plan_md,
        judge_rubric_additions=scenario.judge_rubric_additions,
    )

    # Wrap log_fn to also emit graph snapshots
    # on_graph_update fires BEFORE log so the graph ref is set before broadcast
    actual_log = log
    if log and on_graph_update:
        def _log_with_graph(event: str, detail: str = ""):
            if event == "graph_update":
                on_graph_update(graph)
            log(event, detail)
        actual_log = _log_with_graph
    elif on_graph_update:
        def _graph_only_log(event: str, detail: str = ""):
            if event == "graph_update":
                on_graph_update(graph)
        actual_log = _graph_only_log

    # Run
    try:
        result = await run_react_loop(entry, ctx, scenario.objective.goal, log=actual_log)
    except KeyboardInterrupt:
        result = "Interrupted by user"
    except Exception as e:
        result = f"Error: {e}"
    finally:
        # Release any pending human questions so we don't leak awaited futures
        if config.human_broker is not None:
            config.human_broker.cancel_all("run ended")

    # Save graph + memory
    memory.save_graph(graph)
    memory.save_run_log(run_id, ctx.turns)
    GlobalMemory.update_from_graph(graph)

    # Save report if requested
    if config.output_path:
        report = ctx.to_report()
        report["result"] = result
        report["scenario"] = scenario.name
        report["run_id"] = run_id
        report["graph_stats"] = graph.stats()
        with open(config.output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

    return RunResult(
        run_id=run_id,
        scenario=scenario,
        result=result,
        ctx=ctx,
        graph=graph,
        memory=memory,
    )


def list_scenarios(directory: str | Path) -> list[dict]:
    """List scenario YAML files in a directory.

    Only includes files that look like actual scenarios (must have
    'target', 'objective', and 'module' keys). Skips module YAMLs,
    hidden directories, .venv, and node_modules.
    """
    import yaml

    directory = Path(directory)
    scenarios = []

    skip_dirs = {".venv", "node_modules", ".git", "__pycache__", "dist"}

    for ext in ("*.yaml", "*.yml"):
        for f in sorted(directory.rglob(ext)):
            # Skip hidden dirs and known non-scenario directories
            if any(part.startswith(".") or part in skip_dirs for part in f.parts):
                continue

            try:
                with open(f) as fh:
                    data = yaml.safe_load(fh)

                # A valid scenario must have these keys
                if not isinstance(data, dict):
                    continue
                if not all(k in data for k in ("target", "objective", "module")):
                    continue

                s = load_scenario(str(f))
                scenarios.append({
                    "path": str(f),
                    "name": s.name,
                    "description": s.description,
                    "target_adapter": s.target.adapter,
                    "target_url": s.target.url or s.target.base_url or s.target.model or "",
                    "module": s.module,
                    "max_turns": s.objective.max_turns,
                })
            except Exception:
                pass  # silently skip unparseable files
    return scenarios


def list_modules(extra_paths: list[str] | None = None) -> list[dict]:
    """List all available modules."""
    registry = Registry()
    registry.auto_discover(BUILTIN_MODULES)
    for p in (extra_paths or []):
        registry.auto_discover(p)
    return registry.list_modules()


def list_targets() -> list[dict]:
    """List known targets from ~/.mesmer/targets/."""
    targets_dir = Path.home() / ".mesmer" / "targets"
    if not targets_dir.exists():
        return []

    targets = []
    for d in sorted(targets_dir.iterdir()):
        if not d.is_dir():
            continue
        graph_path = d / "graph.json"
        info = {"hash": d.name, "has_graph": graph_path.exists()}
        if graph_path.exists():
            try:
                g = AttackGraph.from_json(graph_path.read_text())
                info["stats"] = g.stats()
                info["runs"] = g.run_counter
            except Exception:
                pass
        targets.append(info)
    return targets
