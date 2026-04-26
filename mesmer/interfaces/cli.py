"""Mesmer CLI — cognitive hacking toolkit for LLMs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from mesmer.core.agent.context import HumanQuestionBroker
from mesmer.core.constants import NodeSource, ScenarioMode
from mesmer.core.graph import AttackGraph
from mesmer.core.agent.memory import TargetMemory, GlobalMemory
from mesmer.core.runner import RunConfig, execute_run, BUILTIN_MODULES
from mesmer.core.registry import Registry
from mesmer.core.scenario import load_scenario

console = Console()


@click.group()
@click.version_option(version="0.2.0", prog_name="mesmer")
def cli():
    """Mesmer — Cognitive hacking toolkit for LLMs.

    Treat AI as minds to hack, not software to fuzz.
    """
    pass


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("scenario_path", type=click.Path(exists=True))
@click.option("--model", default=None, help="Override executive/manager model (e.g., anthropic/claude-opus-4-7)")
@click.option("--max-turns", default=None, type=int, help="Override max turns")
@click.option("--verbose", "-v", is_flag=True, help="Show full ReAct reasoning")
@click.option("--output", "-o", default=None, help="Save report to JSON file")
@click.option("--module-path", multiple=True, help="Additional module search paths")
@click.option("--hint", multiple=True, help="Human insight to guide the attack (repeatable)")
@click.option("--hint-file", type=click.Path(exists=True), default=None, help="File with human observations")
@click.option("--fresh", is_flag=True, help="Ignore existing graph, start fresh")
@click.option(
    "--mode",
    "scenario_mode",
    type=click.Choice(["trials", "continuous"], case_sensitive=False),
    default=None,
    help="Override scenario mode. trials: independent sub-module rollouts (default). "
         "continuous: one persistent conversation, delta scoring, cross-run memory.",
)
def run(scenario_path, model, max_turns, verbose, output, module_path, hint, hint_file, fresh, scenario_mode):
    """Run an attack scenario."""
    asyncio.run(_run(scenario_path, model, max_turns, verbose, output, module_path, hint, hint_file, fresh, scenario_mode))


def _make_tty_broker() -> HumanQuestionBroker | None:
    """Build a stdin-backed :class:`HumanQuestionBroker` for interactive CLI runs.

    The synthesized executive owns ``ask_human``; when the operator runs
    ``mesmer run`` from a real terminal we want them to actually be able
    to answer mid-run instead of having every question degrade to an
    empty string.

    Non-interactive runs (CI, piped input, bench) skip this entirely:
    if ``sys.stdin`` isn't a TTY we return ``None`` so the executive's
    ``ask_human`` falls back to the empty-string degrade path and the
    run proceeds autonomously without ever blocking on input.

    Each ``on_question`` fires a background task that reads a single
    line from stdin (via ``asyncio.to_thread`` to keep the loop alive)
    and calls ``broker.answer``. Cancelled answers (run ends, broker
    cancels all) are tolerated — the task just no-ops.
    """
    if not sys.stdin.isatty():
        return None

    pending_tasks: list[asyncio.Task] = []

    def _on_question(payload: dict) -> None:
        qid = payload["question_id"]
        question = payload["question"]
        options = payload.get("options") or []

        prompt_lines = [
            "",
            f"[bold yellow]❓ Executive asks:[/bold yellow] {question}",
        ]
        ctx_text = (payload.get("context") or "").strip()
        if ctx_text:
            prompt_lines.append(f"[dim]context: {ctx_text}[/dim]")
        if options:
            prompt_lines.append("[dim]options:[/dim]")
            for i, opt in enumerate(options, 1):
                prompt_lines.append(f"  [dim]{i}.[/dim] {opt}")
        for line in prompt_lines:
            console.print(line)

        async def _read_and_answer():
            try:
                # Run blocking input() in a thread so the event loop —
                # and the executive's other concurrent work — keeps moving.
                answer = await asyncio.to_thread(input, "> ")
            except (EOFError, asyncio.CancelledError):
                answer = ""
            broker.answer(qid, answer.strip())

        # Schedule on the running loop. ``asyncio.create_task`` requires
        # a running loop, which always holds because ``on_question`` is
        # called from inside the executive's awaited completion.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pending_tasks.append(loop.create_task(_read_and_answer()))

    broker = HumanQuestionBroker(on_question=_on_question)
    return broker


def _make_verbose_log():
    """Create the verbose logging callback for CLI output."""
    def verbose_log(event: str, detail: str = ""):
        styles = {
            "module_start":  ("bold magenta", "\u25b6"),
            "llm_call":      ("dim",          "\u23f3"),
            # LLM_COMPLETION is per-call (any role) — dim so it doesn't drown
            # the higher-signal events, but still visible.
            "llm_completion": ("dim cyan",    "\u2728"),
            "llm_error":     ("bold red",     "\u2717"),
            "reasoning":     ("dim italic",   "\U0001f4ad"),
            "tool_calls":    ("bold blue",    "\U0001f527"),
            "send":          ("bold cyan",    "\u2192"),
            "recv":          ("bold yellow",  "\u2190"),
            "delegate":      ("bold magenta", "\u21b3"),
            "delegate_done": ("magenta",      "\u21b2"),
            "conclude":      ("bold green",   "\u2713"),
            "budget":        ("bold red",     "\u26a0"),
            "circuit_break": ("bold yellow",  "\u26a1"),
            "hard_stop":     ("bold red",     "\U0001f6d1"),
            "judge":         ("bold blue",    "\u2696"),
            "judge_score":   ("bold blue",    "\U0001f4ca"),
            # JUDGE_VERDICT is the full JSON payload — dim so it doesn't
            # scroll past the score line that already summarises it.
            "judge_verdict": ("dim blue",     "\u2696"),
            "judge_error":   ("red",          "\u2696\u2717"),
            "graph_update":  ("cyan",         "\U0001f4ca"),
            "frontier":      ("green",        "\U0001f33f"),
            "tier_gate":     ("bold green",   "\U0001f6aa"),  # door
            "reflect_error": ("red",          "\U0001f4ad\u2717"),
            "send_error":    ("bold red",     "\u2192\u2717"),
            "throttle_wait": ("yellow",       "\u23f8"),
            # Executive \u2194 operator surface (visible without --verbose
            # would be ideal, but we route through the same log channel
            # for now). talk_to_operator broadcasts run forward without
            # waiting; the question/answer pair is what the broker
            # already prints to stdin.
            "operator_reply":   ("bold cyan",    "\U0001f4ac"),  # speech balloon
            "operator_message": ("dim cyan",     "\U0001f4e8"),  # incoming envelope
            "ask_human":        ("bold yellow",  "?"),
            "human_answer":     ("yellow",       "!"),
        }
        style, icon = styles.get(event, ("dim", "\u00b7"))
        console.print(f"[{style}]{icon} {detail}[/{style}]")
    return verbose_log


async def _run(scenario_path, model, max_turns, verbose, output, extra_module_paths, hints, hint_file, fresh, scenario_mode=None):
    """Async run implementation."""
    # Print scenario info
    scenario = load_scenario(scenario_path)
    console.print(Panel(
        f"[bold]{scenario.name}[/bold]\n{scenario.description}\n\n"
        f"Target: {scenario.target.adapter} \u2192 {scenario.target.url or scenario.target.model}\n"
        f"Modules: {', '.join(scenario.modules)}\n"
        f"Agent: {scenario.agent.model}\n"
        f"Objective: {scenario.objective.goal}",
        title="[bold magenta]mesmer v1[/bold magenta]",
        border_style="magenta",
    ))

    # API key info
    agent_config = scenario.agent
    if agent_config.key_count > 0:
        masked = agent_config.api_key[:8] + "..." + agent_config.api_key[-4:] if len(agent_config.api_key) > 12 else "***"
        console.print(f"[dim]API key: {masked}[/dim]")
    else:
        console.print(
            "[yellow]Warning: no api_key in scenario. LiteLLM will fall back to "
            "env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)[/yellow]"
        )

    log_fn = _make_verbose_log() if verbose else None
    # Stdin-backed broker only when running interactively. Non-TTY
    # invocations (CI, piped input, bench) get ``None`` and the
    # executive's ask_human degrades to "" — run proceeds autonomously.
    tty_broker = _make_tty_broker()
    if tty_broker is not None:
        console.print("[dim]Interactive mode — executive may ask you questions via ask_human.[/dim]")
    console.print("\n[bold green]Starting attack...[/bold green]\n")

    config = RunConfig(
        scenario_path=scenario_path,
        model_override=model,
        max_turns_override=max_turns,
        hints=list(hints),
        hint_file=hint_file,
        fresh=fresh,
        extra_module_paths=list(extra_module_paths),
        output_path=output,
        human_broker=tty_broker,
        scenario_mode_override=(
            ScenarioMode(scenario_mode.lower()) if scenario_mode else None
        ),
    )

    try:
        run_result = await execute_run(config, log=log_fn)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    # Print result
    result = run_result.result
    console.print(Panel(
        result,
        title="[bold green]Result[/bold green]" if "success" in result.lower() else "[bold red]Result[/bold red]",
        border_style="green" if "success" in result.lower() else "red",
    ))

    # Print module trace
    table = Table(title="Module Trace")
    table.add_column("Module", style="cyan")
    table.add_column("Instruction", style="white", max_width=50)
    table.add_column("Turns", style="yellow", justify="right")
    table.add_column("Result", style="green", max_width=60)

    for run_log in run_result.ctx.module_log:
        table.add_row(
            run_log.name,
            run_log.instruction[:50],
            str(run_log.turns_used),
            run_log.result[:60],
        )

    console.print(table)
    budget_str = str(run_result.ctx.turn_budget) if run_result.ctx.turn_budget else "\u221e"
    console.print(f"\n[dim]Total turns: {len(run_result.ctx.turns)} / {budget_str}[/dim]")

    # Print graph summary
    _print_run_summary(run_result.graph)

    console.print(f"\n[dim]Graph saved ({len(run_result.graph)} nodes) \u2192 ~/.mesmer/targets/{run_result.memory.target_hash}/[/dim]")


def _print_run_summary(graph: AttackGraph):
    """Print post-run summary with frontier suggestions."""
    stats = graph.stats()
    promising = graph.get_promising_nodes()[:3]
    dead = graph.get_dead_nodes()
    frontier = graph.get_frontier_nodes(limit=5)

    lines = []
    lines.append(f"{stats['total']} nodes, {stats['by_status'].get('dead', 0)} dead, best score: {stats['best_score']}/10")
    lines.append("")

    if promising:
        lines.append("[bold]Key findings:[/bold]")
        for n in promising:
            lines.append(f"  \u2605 {n.module}\u2192{n.approach[:60]} (score:{n.score})")
    if dead:
        dead_mods = set(n.module for n in dead)
        lines.append(f"  \u2717 Dead ends: {', '.join(dead_mods)}")
    lines.append("")

    if frontier:
        lines.append("[bold]Frontier (suggested next moves):[/bold]")
        for i, n in enumerate(frontier, 1):
            tag = " \u2605 HUMAN" if n.source == NodeSource.HUMAN else ""
            lines.append(f"  {i}. {n.approach[:70]}{tag}")
    lines.append("")
    lines.append("[dim]\U0001f4a1 Pass feedback on next run with --hint[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title="[bold]Run Complete[/bold]",
        border_style="blue",
    ))


# ---------------------------------------------------------------------------
# graph commands
# ---------------------------------------------------------------------------

@cli.group()
def graph():
    """Inspect and manage attack graphs."""
    pass


@graph.command("show")
@click.argument("scenario_path", type=click.Path(exists=True))
def graph_show(scenario_path):
    """Show the attack graph for a scenario's target."""
    scenario = load_scenario(scenario_path)
    memory = TargetMemory(scenario.target)

    if not memory.exists():
        console.print("[yellow]No graph found for this target. Run an attack first.[/yellow]")
        return

    g = memory.load_graph()
    stats = g.stats()

    console.print(Panel(
        f"Target: {scenario.target.adapter} \u2192 {scenario.target.url or scenario.target.model}\n"
        f"Hash: {memory.target_hash}\n"
        f"Runs: {g.run_counter}\n"
        f"Nodes: {stats['total']} (depth: {stats['depth']})\n"
        f"  Dead: {stats['by_status'].get('dead', 0)}\n"
        f"  Promising: {stats['by_status'].get('promising', 0)}\n"
        f"  Frontier: {stats['by_status'].get('frontier', 0)}\n"
        f"  Alive: {stats['by_status'].get('alive', 0)}\n"
        f"Best score: {stats['best_score']}/10",
        title="[bold cyan]Attack Graph[/bold cyan]",
        border_style="cyan",
    ))

    promising = g.get_promising_nodes()[:5]
    if promising:
        table = Table(title="Promising Nodes")
        table.add_column("Module", style="cyan")
        table.add_column("Approach", style="white", max_width=50)
        table.add_column("Score", style="green", justify="right")
        table.add_column("Leaked Info", style="yellow", max_width=40)
        for n in promising:
            table.add_row(n.module, n.approach[:50], str(n.score), n.leaked_info[:40])
        console.print(table)

    frontier = g.get_frontier_nodes(limit=10)
    if frontier:
        table = Table(title="Frontier (next moves)")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Module", style="cyan")
        table.add_column("Approach", style="white", max_width=60)
        table.add_column("Source", style="yellow")
        for i, n in enumerate(frontier, 1):
            table.add_row(str(i), n.module, n.approach[:60], n.source)
        console.print(table)

    dead = g.get_dead_nodes()[:8]
    if dead:
        table = Table(title="Dead Ends")
        table.add_column("Module", style="red")
        table.add_column("Approach", style="white", max_width=50)
        table.add_column("Reason", style="dim", max_width=50)
        for n in dead:
            table.add_row(n.module, n.approach[:50], n.reflection[:50])
        console.print(table)


@graph.command("reset")
@click.argument("scenario_path", type=click.Path(exists=True))
@click.confirmation_option(prompt="This will delete the attack graph. Continue?")
def graph_reset(scenario_path):
    """Reset (delete) the attack graph for a scenario's target."""
    scenario = load_scenario(scenario_path)
    memory = TargetMemory(scenario.target)

    if memory.graph_path.exists():
        memory.graph_path.unlink()
        console.print("[green]Graph reset.[/green]")
    else:
        console.print("[yellow]No graph found.[/yellow]")


# ---------------------------------------------------------------------------
# hint
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("scenario_path", type=click.Path(exists=True))
@click.argument("text")
def hint(scenario_path, text):
    """Add a human insight to the attack graph without running."""
    scenario = load_scenario(scenario_path)
    memory = TargetMemory(scenario.target)

    g = memory.load_graph()
    g.ensure_root()
    node = g.add_human_hint(text.strip())
    memory.save_graph(g)

    console.print(f"[bold yellow]\U0001f4a1 Hint saved:[/bold yellow] {text.strip()[:100]}")
    console.print(f"[dim]Node {node.id} added as high-priority frontier[/dim]")


# ---------------------------------------------------------------------------
# debrief
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("scenario_path", type=click.Path(exists=True))
def debrief(scenario_path):
    """Interactive post-run Q&A. Mesmer asks questions, you provide insights."""
    asyncio.run(_debrief(scenario_path))


async def _debrief(scenario_path):
    """Async debrief implementation."""
    scenario = load_scenario(scenario_path)
    memory = TargetMemory(scenario.target)

    if not memory.exists():
        console.print("[yellow]No graph found. Run an attack first.[/yellow]")
        return

    g = memory.load_graph()
    stats = g.stats()

    console.print(Panel(
        f"Graph: {stats['total']} nodes, best score: {stats['best_score']}/10\n"
        f"Runs: {g.run_counter}",
        title="[bold]Debrief[/bold]",
        border_style="blue",
    ))

    agent_config = scenario.agent
    import litellm
    litellm.suppress_debug_info = True

    prompt = f"""You are debriefing a human operator after an AI red-teaming run.

Attack graph summary:
{g.format_summary()}

Target: {scenario.target.adapter} \u2192 {scenario.target.url or scenario.target.model}
Objective: {scenario.objective.goal}

Based on the graph state, generate 3-5 smart questions to ask the human operator.
Focus on:
- Ambiguous findings that need human interpretation
- Patterns the human might have noticed watching the conversation
- Specific target responses that seem interesting or unusual
- Strategic suggestions for the next run

Format each question on its own line, numbered 1-5. Keep them concise."""

    kwargs = {
        "model": agent_config.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    key = agent_config.next_key()
    if key:
        kwargs["api_key"] = key
    if agent_config.api_base:
        kwargs["api_base"] = agent_config.api_base

    try:
        response = await litellm.acompletion(**kwargs)
        questions = response.choices[0].message.content or "No questions generated."
    except Exception as e:
        console.print(f"[red]Failed to generate questions: {e}[/red]")
        questions = "1. What patterns did you notice in the target's responses?\n2. Any specific target behavior that seemed exploitable?\n3. What should we try differently next run?"

    console.print(f"\n{questions}\n")

    insights_added = 0
    while True:
        try:
            answer = console.input("[bold blue]> [/bold blue]")
        except (EOFError, KeyboardInterrupt):
            break

        if not answer.strip():
            break

        if answer.strip().lower() in ("quit", "exit", "done", "q"):
            break

        g.add_human_hint(answer.strip())
        insights_added += 1
        console.print(f"[dim]  \u2713 Saved as insight #{insights_added}[/dim]")
        console.print("[dim]  (enter another insight, or press Enter/type 'done' to finish)[/dim]")

    if insights_added > 0:
        memory.save_graph(g)
        console.print(f"\n[bold green]Saved {insights_added} human insights to attack graph.[/bold green]")
    else:
        console.print("\n[dim]No insights added.[/dim]")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@cli.command()
def stats():
    """Show global technique effectiveness stats."""
    s = GlobalMemory.load_stats()
    if not s:
        console.print("[yellow]No global stats yet. Run some attacks first.[/yellow]")
        return

    table = Table(title="Global Technique Stats")
    table.add_column("Technique", style="cyan")
    table.add_column("Attempts", style="yellow", justify="right")
    table.add_column("Avg Score", style="green", justify="right")
    table.add_column("Best Score", style="bold green", justify="right")

    for mod, data in sorted(s.items(), key=lambda x: -x[1].get("avg_score", 0)):
        table.add_row(
            mod,
            str(data["attempts"]),
            str(data["avg_score"]),
            str(data["best_score"]),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------

@cli.group()
def modules():
    """Manage and inspect modules."""
    pass


@modules.command("list")
@click.option("--path", default=None, help="Module search path")
def list_modules(path):
    """List all available modules."""
    registry = Registry()
    registry.auto_discover(BUILTIN_MODULES)
    if path:
        registry.auto_discover(path)

    table = Table(title="Available Modules")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white", max_width=60)
    table.add_column("Sub-modules", style="yellow")

    for info in sorted(registry.list_modules(), key=lambda x: x["name"]):
        subs = ", ".join(info["sub_modules"][:3])
        if len(info["sub_modules"]) > 3:
            subs += f" +{len(info['sub_modules']) - 3}"
        table.add_row(info["name"], info["description"], subs or "-")

    console.print(table)


@modules.command("describe")
@click.argument("name")
@click.option("--path", default=None, help="Module search path")
def describe_module(name, path):
    """Show detailed info about a module."""
    registry = Registry()
    registry.auto_discover(BUILTIN_MODULES)
    if path:
        registry.auto_discover(path)

    mod = registry.get(name)
    if mod is None:
        console.print(f"[red]Module '{name}' not found[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold]{mod.name}[/bold]\n\n"
        f"[cyan]Description:[/cyan]\n{mod.description}\n\n"
        f"[cyan]Theory:[/cyan]\n{mod.theory}\n\n"
        f"[cyan]Sub-modules:[/cyan] {', '.join(mod.sub_module_names) or 'none'}\n\n"
        f"[cyan]System prompt:[/cyan]\n[dim]{mod.system_prompt[:500]}{'...' if len(mod.system_prompt) > 500 else ''}[/dim]",
        title=f"[magenta]{mod.name}[/magenta]",
        border_style="magenta",
    ))


# ---------------------------------------------------------------------------
# serve (web UI)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--port", default=8888, help="Port to serve on")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--no-browser", is_flag=True, help="Don't auto-open browser")
@click.option("--scenario-dir", default=".", help="Directory to scan for scenarios")
def serve(port, host, no_browser, scenario_dir):
    """Start the Mesmer web UI."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Web UI requires extra dependencies.[/red]\n"
            "Install with: [bold]pip install mesmer[web][/bold]  or  [bold]uv sync --extra web[/bold]"
        )
        sys.exit(1)

    from mesmer.interfaces.web.backend.server import create_app

    app = create_app(scenario_dir=scenario_dir)

    if not no_browser:
        import webbrowser
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    console.print(Panel(
        f"Serving at [bold]http://{host}:{port}[/bold]\n"
        f"Scenario directory: {scenario_dir}",
        title="[bold magenta]mesmer web UI[/bold magenta]",
        border_style="magenta",
    ))

    uvicorn.run(app, host=host, port=port, log_level="warning")


# ---------------------------------------------------------------------------
# bench — run a reproducible benchmark spec against N targets
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("spec_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--targets",
    default=None,
    help="Comma-separated target IDs to include (default: all targets in the spec).",
)
@click.option(
    "--sample",
    type=int,
    default=None,
    help="Limit to the first N dataset rows (overrides spec's budget.sample). "
         "Useful for fast iterations — use 0 for 'all rows'.",
)
@click.option(
    "--trials",
    type=int,
    default=None,
    help="Override spec's trials_per_row. Set to 1 for smoke runs.",
)
@click.option(
    "--output",
    type=click.Path(file_okay=False),
    default=None,
    help="Where to write per-trial JSONL + summary.json + Markdown table. "
         "Defaults to <spec-dir>/../results/.",
)
@click.option(
    "--concurrency",
    type=int,
    default=None,
    help="Max concurrent in-flight trials (overrides spec's budget.concurrency).",
)
@click.option(
    "--download",
    is_flag=True,
    help="Force re-fetch the dataset from upstream_url even if a cached copy exists.",
)
@click.option(
    "--no-baseline",
    is_flag=True,
    help="Skip the single-turn baseline arm (mesmer arm only). Saves API spend.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Stream per-trial progress to stderr.",
)
@click.option(
    "--no-viz",
    is_flag=True,
    help="Skip post-run {stem}-viz.html generation. Default is on — the "
         "HTML is a derived artefact you can always rebuild with "
         "`mesmer bench-viz {stem}-summary.json`.",
)
@click.option(
    "--row",
    "rows",
    multiple=True,
    default=(),
    help="Run only the dataset row(s) with these sample_id values. "
         "Repeatable: `--row 1234 --row 5678`. When set, overrides "
         "--sample (the row filter is exact, not head-truncating). "
         "Useful for re-running a single failed trial after a fix.",
)
def bench(spec_path, targets, sample, trials, output, concurrency, download,
          no_baseline, verbose, no_viz, rows):
    """Run a benchmark spec and emit reproducible results.

    A spec is a YAML file that binds one or more mesmer modules to one
    dataset and a list of target models. Example::

        mesmer bench benchmarks/specs/tensor-trust-extraction.yaml \\
            --sample 50 --trials 3

    Writes a per-trial JSONL, a summary.json, and a Markdown table under
    the output directory. Use the Markdown as a drop-in for the README.
    """
    asyncio.run(_bench(
        spec_path=spec_path,
        targets=targets,
        sample=sample,
        trials=trials,
        output=output,
        concurrency=concurrency,
        download=download,
        no_baseline=no_baseline,
        verbose=verbose,
        no_viz=no_viz,
        rows=rows,
    ))


async def _bench(
    *,
    spec_path: str,
    targets: str | None,
    sample: int | None,
    trials: int | None,
    output: str | None,
    concurrency: int | None,
    download: bool,
    no_baseline: bool,
    verbose: bool,
    no_viz: bool,
    rows: tuple[str, ...] = (),
):
    from mesmer.bench import load_spec, run_benchmark

    spec_path_obj = Path(spec_path).resolve()
    spec = load_spec(spec_path_obj)

    if concurrency is not None:
        spec.budget.concurrency = concurrency
    if no_baseline:
        spec.budget.run_baseline = False

    target_ids = None
    if targets:
        target_ids = {t.strip() for t in targets.split(",") if t.strip()}

    sample_ids_filter = {r.strip() for r in rows if r and r.strip()} or None

    # Default output dir: <spec>/../../results/. Spec lives in
    # benchmarks/specs/foo.yaml, so parent.parent = benchmarks/.
    if output is None:
        output_dir = spec_path_obj.parent.parent / "results"
    else:
        output_dir = Path(output)

    spec_dir = spec_path_obj.parent.parent   # <benchmarks/>

    rows_label = (
        f"row filter: {', '.join(sorted(sample_ids_filter))}"
        if sample_ids_filter
        else f"{sample or spec.budget.sample or 'all'} rows"
    )
    console.print(Panel(
        f"[bold]{spec.name}[/bold]\n"
        f"Version: {spec.version} · Modules: {', '.join(spec.modules)}\n"
        f"Targets: {', '.join(t.id for t in spec.targets)}\n"
        f"Dataset: {spec.dataset.upstream_url or spec.dataset.local_cache}\n"
        f"Budget: {spec.budget.max_turns} turns · "
        f"{rows_label} · "
        f"{trials or spec.budget.trials_per_row} trials/row\n"
        f"Baseline arm: {'on' if spec.budget.run_baseline else 'off'}",
        title="[bold magenta]mesmer bench[/bold magenta]",
        border_style="magenta",
    ))

    def _progress(msg: str):
        if verbose:
            console.print(f"[dim]{msg}[/dim]")

    summary, trials_list = await run_benchmark(
        spec,
        spec_dir=spec_dir,
        output_dir=output_dir,
        target_filter=target_ids,
        sample_override=sample,
        trials_override=trials,
        sample_ids_filter=sample_ids_filter,
        force_download=download,
        progress=_progress,
        # --verbose streams every ReAct event. With concurrency > 1 trials
        # interleave; orchestrator prefixes each line with the trial handle.
        log_fn=_make_verbose_log() if verbose else None,
        generate_viz=not no_viz,
    )

    # Summary table
    table = Table(
        title=f"Benchmark results — {spec.name} ({spec.version})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Target")
    table.add_column("Arm")
    table.add_column("ASR", justify="right")
    table.add_column("± stderr", justify="right")
    table.add_column("Trials", justify="right")
    table.add_column("Median turns", justify="right")
    table.add_column("Avg tokens", justify="right")
    for c in summary.cells:
        median_str = f"{int(c.median_turns)}" if c.median_turns is not None else "—"
        table.add_row(
            c.target_id,
            c.arm,
            f"{c.asr * 100:.1f}%",
            f"±{c.asr_stderr * 100:.1f}%",
            str(c.n_trials),
            median_str,
            f"{int(c.mean_total_tokens):,}",
        )
    console.print(table)
    console.print(f"[green]Artifacts written to[/green] {output_dir}")


# ---------------------------------------------------------------------------
# bench-viz — backfill the HTML viewer against an existing run
# ---------------------------------------------------------------------------

@cli.command("bench-viz")
@click.argument(
    "summary_path",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
)
@click.option(
    "--offline",
    is_flag=True,
    help="Inline the vendored D3 bundle instead of loading it from the "
         "CDN. Produces a larger HTML (+~280KB) that works with no network.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default=None,
    help="Override the output HTML path. By default writes "
         "<stem>-viz.html next to the summary, or <stem>-viz-index.html "
         "when the payload is split per-target.",
)
def bench_viz_cmd(summary_path: str, offline: bool, output: str | None):
    """Generate an interactive per-trial mind-map HTML for a bench run.

    Reads the run's ``{stem}-summary.json`` and every
    ``events/*.graph.json`` trial snapshot, then writes a self-contained
    HTML file that renders the attack trees with pan/zoom and a per-node
    detail panel. Open the HTML in any browser — no server required.

    Example::

        mesmer bench-viz benchmarks/results/20260424T145633Z-v1-summary.json
    """
    from mesmer.bench.viz import build_viz_html

    result = build_viz_html(
        Path(summary_path),
        offline=offline,
        output_path=Path(output) if output else None,
    )
    console.print(f"[green]Wrote[/green] {result.primary}")
    for extra in result.extras:
        console.print(f"[dim]  + {extra}[/dim]")


if __name__ == "__main__":
    cli()
