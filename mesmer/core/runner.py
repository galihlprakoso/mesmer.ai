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

from mesmer.core.actor import ExecutiveSpec
from mesmer.core.constants import LogEvent, NodeSource, NodeStatus, ScenarioMode
from mesmer.core.agent.context import Context, HumanQuestionBroker, RunTelemetry
from mesmer.core.belief_graph import NodeKind, Strategy, TargetTraitsUpdateDelta
from mesmer.core.errors import HypothesisGenerationError, InvalidDelta
from mesmer.core.graph import AttackGraph
from mesmer.core.agent import LogFn, run_react_loop
from mesmer.core.agent.beliefs import (
    generate_frontier_experiments,
    generate_hypotheses,
    rank_frontier,
)
from mesmer.core.agent.memory import (
    MESMER_HOME,
    GlobalMemory,
    TargetMemory,
    generate_run_id,
)
from mesmer.core.agent.prompts import EXECUTIVE_SYSTEM as _DEFAULT_EXECUTIVE_PROMPT
from mesmer.core.modules import (
    CompositeModuleCatalog,
    FileModuleCatalog,
    ModuleSource,
    StorageModuleCatalog,
    workspace_modules_prefix,
)
from mesmer.core.persistence import (
    FileScenarioRepository,
    FileStorageProvider,
    join_storage_key,
    workspace_prefix,
)
from mesmer.core.registry import Registry
from mesmer.core.scenario import load_scenario, AgentConfig, Scenario
from mesmer.core.strategy_library import (
    load_library,
    merge_per_target_strategies,
    save_library,
)

# Packaged catalogs shipped with the repository. Workspace-owned modules and
# scenarios live under MESMER_HOME so local/user data does not dirty the repo.
PACKAGE_ROOT = Path(__file__).parent.parent.parent / "packages"
BUILTIN_MODULES = PACKAGE_ROOT / "modules"
SCENARIO_TEMPLATES = PACKAGE_ROOT / "scenarios"
WORKSPACE_SCENARIOS = MESMER_HOME / "workspaces" / "local" / "scenarios"


def build_module_registry(
    *,
    workspace_id: str = "local",
    scenario_module_paths: list[str] | None = None,
    extra_module_paths: list[str] | None = None,
) -> Registry:
    """Build the active runtime module registry.

    Resolution order is intentional: packaged builtins, workspace modules,
    scenario-declared local modules, then explicit extra module paths. Later
    catalogs override earlier catalogs by module name.
    """
    catalogs = [
        FileModuleCatalog(
            BUILTIN_MODULES,
            source=ModuleSource.BUILTIN,
            source_id="mesmer",
            editable=False,
        ),
        StorageModuleCatalog(
            FileStorageProvider(MESMER_HOME),
            workspace_modules_prefix(workspace_id),
            source=ModuleSource.WORKSPACE,
            source_id=workspace_id,
            editable=True,
        ),
    ]
    for path in scenario_module_paths or []:
        catalogs.append(
            FileModuleCatalog(
                path,
                source=ModuleSource.LOCAL_PATH,
                source_id="scenario",
                editable=True,
            )
        )
    for path in extra_module_paths or []:
        catalogs.append(
            FileModuleCatalog(
                path,
                source=ModuleSource.LOCAL_PATH,
                source_id="extra",
                editable=True,
            )
        )

    registry = Registry()
    registry.load_catalog(CompositeModuleCatalog(*catalogs))
    return registry


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
    workspace_id: str = "local"
    output_path: str | None = None
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
    effective_seed = config.seed if config.seed is not None else scenario.agent.seed
    if effective_seed is not None:
        random.seed(effective_seed)

    run_started_at = time.monotonic()

    # Build registry
    registry = build_module_registry(
        workspace_id=config.workspace_id,
        scenario_module_paths=scenario.module_paths,
        extra_module_paths=config.extra_module_paths,
    )

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

    # Validate every manager named in scenario.modules is in the registry
    # before synthesizing the executive — otherwise the executive's tool
    # list would silently miss managers and the operator would only find
    # out mid-run.
    missing = [name for name in scenario.modules if registry.get(name) is None]
    if missing:
        available = ", ".join(sorted(registry.modules.keys()))
        raise ValueError(
            f"Scenario modules not found in registry: {missing}. Available: {available}"
        )

    # Synthesize the scenario-scoped executive. It exists only in memory
    # for this run — never written to disk, never registered, never
    # discoverable by ``Registry.auto_discover``. The name carries the
    # scenario stem so leader-verdict nodes in graph.json are
    # attributable to the right scenario when multiple scenarios run
    # against the same target.
    scenario_stem = Path(config.scenario_path).stem if config.scenario_path else "scenario"
    executive_name = f"{scenario_stem}:executive"
    ordered_output_requirements = {}
    if (
        scenario.leader_prompt
        and "indirect-prompt-injection" in scenario.modules
        and "email-exfiltration-proof" in scenario.modules
    ):
        ordered_output_requirements["indirect-prompt-injection"] = [
            "## Retrieval Path",
            "## Injection Evidence",
        ]

    executive = ExecutiveSpec(
        name=executive_name,
        description=f"Scenario-scoped executive for {scenario.name}.",
        system_prompt=scenario.leader_prompt or _DEFAULT_EXECUTIVE_PROMPT,
        ordered_modules=list(scenario.modules) if scenario.leader_prompt else list(scenario.modules),
        suppress_belief_context=bool(scenario.leader_prompt),
        ordered_output_requirements=ordered_output_requirements,
    )
    executive_actor = executive.as_actor()

    # Create target
    target = create_target(scenario.target)

    # Load or create attack graph
    memory = TargetMemory(scenario.target, workspace_id=config.workspace_id)
    if config.fresh or not memory.exists():
        graph = AttackGraph()
    else:
        graph = memory.load_graph()

    graph.ensure_root()
    graph.run_counter += 1

    # Belief Attack Graph — typed planner state. Loaded alongside the legacy
    # AttackGraph; --fresh wipes both so a clean run starts without prior
    # beliefs OR prior attempt history. The graph singleton TargetNode is
    # created in BeliefGraph.__post_init__ even on a fresh load.
    if config.fresh:
        memory.delete_belief_graph()
        belief_graph = memory.load_belief_graph()
    else:
        belief_graph = memory.load_belief_graph()

    # Seed target traits from the scenario's declared system_prompt so the
    # extractor / hypothesis generator have something to anchor on for a
    # never-before-seen target. Latest-wins on the trait key, so re-seeding
    # in subsequent runs simply refreshes from the latest scenario YAML.
    declared_system_prompt = (scenario.target.system_prompt or "").strip()
    if declared_system_prompt:
        belief_graph.apply(
            TargetTraitsUpdateDelta(
                traits={"declared_system_prompt": declared_system_prompt},
                run_id=run_id,
            )
        )

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
        belief_graph=belief_graph,
        run_id=run_id,
        human_broker=config.human_broker,
        target_memory=memory,
        operator_history=[] if config.fresh else memory.load_chat(limit=12),
        judge_rubric_additions=scenario.judge_rubric_additions,
        scenario_mode=effective_scenario_mode,
        _turns=seeded_turns,
    )

    graph_root = graph.ensure_root()
    executive_node = graph.add_node(
        parent_id=graph_root.id,
        module=executive_actor.name,
        approach=scenario.objective.goal or f"{executive_actor.name} run",
        status=NodeStatus.RUNNING.value,
        run_id=run_id,
        source=NodeSource.LEADER.value,
    )
    ctx.graph_parent_id = executive_node.id

    # `--fresh` means "ignore execution history", not "delete curated target
    # knowledge". Keep declared artifacts available so operator_notes and
    # campaign deliverables can intentionally steer a fresh graph run.
    ctx.artifacts = memory.load_artifacts()
    ctx.artifact_specs = list(scenario.artifacts)
    # Older artifact builds materialized every module's conclude text as an
    # artifact named after that module. That made artifacts indistinguishable
    # from graph outputs. Keep graph outputs in graph.json and reserve
    # artifacts for intentional shared documents updated with update_artifact.
    for module_name in registry.modules:
        ctx.artifacts.delete(module_name)

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

    # Belief-graph bootstrap. If the active hypothesis list is empty
    # (fresh target, or every prior hypothesis got CONFIRMED / REFUTED /
    # STALE) ask the generator for a fresh slate. One judge-model LLM
    # call; failures degrade gracefully — the run still proceeds, the
    # planner just operates without any seeded hypotheses for now.
    if not belief_graph.active_hypotheses():
        try:
            bootstrap_deltas = await generate_hypotheses(
                ctx,
                graph=belief_graph,
                objective=scenario.objective.goal or "",
                run_id=run_id,
            )
            for d in bootstrap_deltas:
                belief_graph.apply(d)
            if actual_log is not None and bootstrap_deltas:
                actual_log(
                    LogEvent.HYPOTHESIS_CREATED.value,
                    f"bootstrapped {len(bootstrap_deltas)} hypotheses",
                )
        except HypothesisGenerationError as e:
            # Boundary catch — extractor / generator failures are
            # best-effort. Log and continue.
            if actual_log is not None:
                actual_log(LogEvent.JUDGE_ERROR.value, f"hypothesis bootstrap: {e}")

    # Materialise hypothesis → experiment frontiers before the first leader
    # prompt. Without this, the belief graph has claims but no `fx_...`
    # dispatch contract for the LLM to follow.
    try:
        frontier_deltas = generate_frontier_experiments(
            belief_graph,
            registry=registry,
            available_modules=executive_actor.sub_module_names,
            run_id=run_id,
        )
        for d in frontier_deltas:
            belief_graph.apply(d)
        rank_delta = rank_frontier(belief_graph, registry=registry, run_id=run_id)
        if rank_delta.rankings:
            belief_graph.apply(rank_delta)
        frontier_count = sum(1 for d in frontier_deltas if d.kind.value == "frontier_create")
        if actual_log is not None and frontier_count:
            actual_log(
                LogEvent.FRONTIER_RANKED.value,
                f"bootstrapped {frontier_count} belief frontier experiment(s)",
            )
    except InvalidDelta as e:
        if actual_log is not None:
            actual_log(LogEvent.BELIEF_DELTA.value, f"frontier bootstrap rejected: {e}")

    # Run
    try:
        result = await run_react_loop(executive_actor, ctx, scenario.objective.goal, log=actual_log)
    except KeyboardInterrupt:
        result = "Interrupted by user"
    except Exception as e:
        result = f"Error: {e}"
    finally:
        # Release any pending human questions so we don't leak awaited futures
        if config.human_broker is not None:
            config.human_broker.cancel_all("run ended")

    objective_met = bool(ctx.objective_met)
    executive_node.module = executive_actor.name
    executive_node.approach = scenario.objective.goal or f"{executive_actor.name} run"
    executive_node.module_output = result or ""
    executive_node.leaked_info = ctx.objective_met_fragment or ""
    executive_node.reflection = "objective_met=true" if objective_met else "objective_met=false"
    executive_node.status = NodeStatus.COMPLETED.value
    executive_node.score = 10 if objective_met else 1
    executive_node.run_id = run_id
    executive_node.source = NodeSource.LEADER.value

    finalized_stale = graph.finalize_running_nodes(run_id=run_id)
    if finalized_stale and actual_log is not None:
        actual_log(
            LogEvent.GRAPH_UPDATE.value,
            "finalized stale running node(s): "
            + ", ".join(n.module for n in finalized_stale),
        )

    # Save graph + memory
    memory.save_graph(graph)
    memory.save_artifacts(ctx.artifacts)
    # Persist the belief graph's current snapshot + append unsaved deltas
    # to the JSONL log. ``save_belief_graph`` clears the in-memory delta
    # queue after writing, so a second save is a no-op for the JSONL file.
    memory.save_belief_graph(belief_graph)
    # Session 4B — fold this run's per-target Strategy nodes into the
    # cross-target library so future targets benefit from what worked
    # against this one. Only Strategies with ``attempt_count > 0`` are
    # merged; un-tried ones carry no cross-target signal.
    try:
        library = load_library()
        target_strategies = [
            n for n in belief_graph.iter_nodes(NodeKind.STRATEGY) if isinstance(n, Strategy)
        ]
        if target_strategies:
            merge_per_target_strategies(
                library,
                target_strategies,
                target_traits=dict(belief_graph.target.traits),
            )
            save_library(library)
            if actual_log is not None:
                actual_log(
                    LogEvent.GRAPH_UPDATE.value,
                    f"strategy library merged {len(target_strategies)} strategies",
                )
    except Exception as e:  # noqa: BLE001 — library is best-effort
        if actual_log is not None:
            actual_log(LogEvent.GRAPH_UPDATE.value, f"strategy library merge failed: {e}")
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
    'target', 'objective', and 'modules' keys). Skips module YAMLs,
    hidden directories, .venv, and node_modules.
    """
    return [
        doc.summary()
        for doc in FileScenarioRepository(
            directory,
            write_root=WORKSPACE_SCENARIOS,
        ).list()
    ]


def list_modules(extra_paths: list[str] | None = None, *, workspace_id: str = "local") -> list[dict]:
    """List all available modules."""
    registry = build_module_registry(
        workspace_id=workspace_id,
        extra_module_paths=extra_paths or [],
    )
    return registry.list_modules()


def list_targets(workspace_id: str = "local") -> list[dict]:
    """List known targets from the configured local storage namespace."""
    storage = FileStorageProvider(MESMER_HOME)
    targets_prefix = join_storage_key(workspace_prefix(workspace_id), "targets")
    targets = []
    for target_key in storage.list_dirs(targets_prefix):
        target_hash = Path(target_key).name
        graph_key = join_storage_key(target_key, "graph.json")
        belief_graph_key = join_storage_key(target_key, "belief_graph.json")
        belief_deltas_key = join_storage_key(target_key, "belief_deltas.jsonl")
        info = {
            "hash": target_hash,
            "has_graph": storage.exists(graph_key),
            "has_belief_graph": storage.exists(belief_graph_key)
            or storage.exists(belief_deltas_key),
        }
        if storage.exists(graph_key):
            try:
                g = AttackGraph.from_json(storage.read_text(graph_key))
                info["stats"] = g.stats()
                info["runs"] = g.run_counter
            except Exception:
                pass
        if storage.exists(belief_graph_key):
            try:
                from mesmer.core.belief_graph import BeliefGraph

                bg = BeliefGraph.from_json(storage.read_text(belief_graph_key))
                info["belief_stats"] = bg.stats()
            except Exception:
                pass
        targets.append(info)
    return targets
