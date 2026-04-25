"""Run orchestration — shared between CLI and Web UI.

Extracts the core "run an attack" logic so both interfaces call the same code.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mesmer.core.constants import ContextMode, NodeSource, NodeStatus, ScenarioMode
from mesmer.core.agent.context import Context, HumanQuestionBroker, RunTelemetry
from mesmer.core.graph import AttackGraph
from mesmer.core.agent import LogFn, run_react_loop
from mesmer.core.agent.memory import TargetMemory, GlobalMemory, generate_run_id
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
    mode: str = ContextMode.AUTONOMOUS.value  # ContextMode or 'plan'
    human_broker: "HumanQuestionBroker | None" = None
    # Per-invocation ScenarioMode override (--mode on the CLI). When None,
    # the scenario YAML's mode field wins. Set to ScenarioMode.TRIALS or
    # ScenarioMode.CONTINUOUS to force a mode regardless of YAML.
    scenario_mode_override: ScenarioMode | None = None
    # When given, pre-built Scenario wins over ``scenario_path`` — used by
    # ``mesmer bench`` which constructs scenarios programmatically per
    # dataset row instead of writing 570 YAML files.
    scenario_override: Scenario | None = None
    # PRNG seed for this run. None = legacy "no reseeding"; an int seeds
    # Python's ``random`` module before execute_run starts so technique
    # tie-breaks and other mesmer-level randomness are reproducible.
    # LLM sampling remains provider-side and is NOT made deterministic.
    seed: int | None = None


@dataclass
class RunResult:
    """Result of an attack run."""

    run_id: str
    scenario: Scenario
    result: str
    ctx: Context
    graph: AttackGraph
    memory: TargetMemory
    # Wall-clock from the moment execute_run started to when it returned.
    # Includes LLM calls + tool dispatch + graph persistence.
    duration_s: float = 0.0
    # Seed that was applied to ``random`` at the start of this run. None
    # when no seed was set (legacy behaviour).
    seed: int | None = None

    @property
    def telemetry(self) -> RunTelemetry:
        """Convenience accessor — the run's per-call token/latency roll-up."""
        return self.ctx.telemetry


async def execute_run(
    config: RunConfig,
    log: LogFn | None = None,
    on_graph_update: Callable[[AttackGraph], None] | None = None,
    on_pool_ready: Callable[[object], None] | None = None,
    on_ctx_ready: Callable[[Context], None] | None = None,
) -> RunResult:
    """
    Execute an attack run. This is the core orchestration shared by CLI and web.

    Args:
        config: Run configuration
        log: Optional logging callback (event, detail) → None
        on_graph_update: Optional callback when graph changes (for web UI)
        on_pool_ready: Optional callback receiving the agent's KeyPool once
            configured, so the web UI can broadcast key_status events.
        on_ctx_ready: Optional callback fired right after the top-level
            ``Context`` is constructed and seeded. The web backend uses this
            to grab a handle on the running ctx so operator chat messages
            can be queued onto ``ctx.operator_messages`` mid-run.

    Returns:
        RunResult with all run data
    """
    from mesmer.targets import create_target

    # Load scenario — override wins over path, letting the bench runner
    # construct synthetic scenarios in-memory per dataset row.
    if config.scenario_override is not None:
        scenario = config.scenario_override
    else:
        scenario = load_scenario(config.scenario_path)
    run_id = generate_run_id()

    # Seed the PRNG if either the CLI or the scenario provided one. Order
    # of precedence: explicit config.seed > scenario.agent.seed. A None
    # seed leaves ``random`` untouched so legacy runs are unaffected.
    effective_seed = (
        config.seed if config.seed is not None else scenario.agent.seed
    )
    if effective_seed is not None:
        random.seed(effective_seed)

    run_started_at = time.monotonic()

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
    # CLI override wins over YAML (mirrors model_override / max_turns_override).
    effective_scenario_mode = (
        config.scenario_mode_override
        if config.scenario_mode_override is not None
        else scenario.mode
    )

    # C8 — cross-run conversation persistence for CONTINUOUS mode. Load the
    # prior transcript so the attacker picks up where it left off. ``--fresh``
    # wipes the file; TRIALS mode never touches it (and new targets have none).
    seeded_turns = None
    if effective_scenario_mode == ScenarioMode.CONTINUOUS:
        if config.fresh:
            # Clear the persisted arc — --fresh must mean genuinely fresh.
            memory.delete_conversation()
        else:
            seeded_turns = memory.load_conversation()

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
        target_memory=memory,
        judge_rubric_additions=scenario.judge_rubric_additions,
        scenario_mode=effective_scenario_mode,
        _turns=seeded_turns,
    )

    # Seed the scratchpad from the graph's conversation history. Walk
    # oldest → newest so the latest-wins semantic falls out naturally:
    # if ``target-profiler`` ran twice, the newer dossier overwrites
    # the older slot. Empty outputs are skipped so a module that only
    # produced target messages (no authored conclude text) doesn't
    # clutter the pad.
    #
    # This is how the scratchpad carries cross-run knowledge forward:
    # a second run against a known target starts with the latest
    # profiler dossier + latest plan already on the pad, so the first
    # sub-module delegation already sees it.
    # ``conversation_history()`` already excludes leader-verdict nodes
    # at the source — this loop sees only real attempt outputs.
    for node in graph.conversation_history():
        output = (node.module_output or "").strip()
        if output:
            ctx.scratchpad.set(node.module, output)

    # Persistent leader-scratchpad slot. The leader's slot is the only one
    # the framework leaves empty after the conversation_history loop above
    # (leader-verdict nodes are excluded at source), so the on-disk
    # scratchpad.md content is the canonical contents — overwrite cleanly.
    # Edited via the leader's update_scratchpad tool and via the operator
    # through the web UI's leader-chat.
    scratchpad_md = memory.load_scratchpad()
    if scratchpad_md is not None:
        ctx.scratchpad.set(scenario.module, scratchpad_md)

    # Fire ctx-ready hook AFTER seeding so the web backend's hold-onto-ctx
    # grab gets a fully populated context (operator_messages queue ready).
    if on_ctx_ready is not None:
        on_ctx_ready(ctx)

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

    # Bind the log onto the Context so every :meth:`Context.completion`
    # call (attacker, judge, compressor) can emit a structured
    # LLM_COMPLETION event without needing the caller to thread ``log``
    # through every signature. child() propagates this onto sub-contexts.
    ctx.log = actual_log

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

    # Record the leader's own execution as a graph node, same as any
    # sub-module: one module run → one node. The leader is just a module
    # whose parent in the tree is root (or the last sub-module it
    # delegated to). Marking source=LEADER lets attempt-centric walks
    # (TAPER trace, frontier ranking, winning-module attribution) skip
    # it without having to know the leader's module name. Parent = most
    # recent non-leader node produced during this run_id, else root.
    _leader_peer_nodes = [
        n for n in graph.nodes.values()
        if n.run_id == run_id
        and n.module != "root"
        and not n.is_leader_verdict
    ]
    if _leader_peer_nodes:
        _leader_parent = max(_leader_peer_nodes, key=lambda n: n.timestamp)
        _leader_parent_id = _leader_parent.id
    else:
        if graph.root_id is None:
            graph.ensure_root()
        _leader_parent_id = graph.root_id
    objective_met = bool(ctx.objective_met)
    graph.add_node(
        parent_id=_leader_parent_id,
        module=entry.name,
        approach=scenario.objective.goal or f"{entry.name} run",
        module_output=result or "",
        leaked_info=ctx.objective_met_fragment or "",
        reflection=(
            "objective_met=true" if objective_met else "objective_met=false"
        ),
        # Explicit status skips auto_classify — we already know the
        # outcome from ctx.objective_met, no need for the similarity
        # heuristic to second-guess it.
        status=(
            NodeStatus.PROMISING.value if objective_met else NodeStatus.DEAD.value
        ),
        score=10 if objective_met else 1,
        run_id=run_id,
        source=NodeSource.LEADER.value,
    )

    # Save graph + memory
    memory.save_graph(graph)
    memory.save_run_log(run_id, ctx.turns)
    # C8 — persist the rolling CONTINUOUS conversation for the next invocation.
    # TRIALS never writes it (sibling rollouts have no shared arc to resume).
    if effective_scenario_mode == ScenarioMode.CONTINUOUS:
        memory.save_conversation(ctx.turns)
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

    duration_s = time.monotonic() - run_started_at

    return RunResult(
        run_id=run_id,
        scenario=scenario,
        result=result,
        ctx=ctx,
        graph=graph,
        memory=memory,
        duration_s=duration_s,
        seed=effective_seed,
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
