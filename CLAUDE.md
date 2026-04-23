# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`mesmer` — a cognitive hacking toolkit for LLMs. It red-teams LLM targets by running ReAct agents that pick cognitive-science-based techniques (foot-in-door, authority bias, narrative transport, etc.), judging each attempt, and persisting an MCTS-inspired attack graph per-target so successive runs get smarter.

## Commands

```bash
# Dependency install (uv is the project's canonical tool; uv.lock is committed)
uv sync

# Run the CLI
uv run mesmer run scenarios/extract-system-prompt.yaml --verbose
uv run mesmer graph show scenarios/extract-system-prompt.yaml
uv run mesmer debrief scenarios/extract-system-prompt.yaml
uv run mesmer stats

# Tests (pytest-asyncio is configured; async tests are first-class)
uv run pytest                              # full suite
uv run pytest tests/test_graph.py          # single file
uv run pytest tests/test_graph.py::test_name -xvs   # single test, verbose, stop-on-fail
uv run pytest -k "judge"                   # by keyword

# Lint
uv run ruff check .
uv run ruff format .

# Web UI (optional — pyproject `web` extra + Svelte frontend)
uv sync --extra web
uv run uvicorn mesmer.interfaces.web.backend.server:app --reload
cd mesmer/interfaces/web/frontend && npm install && npm run dev
```

Environment: set `OPENROUTER_API_KEY` (or whichever provider key the scenario references — scenarios use `${VAR}` placeholders resolved by `mesmer/core/scenario.py`). Comma-separated keys enable round-robin rotation via `mesmer/core/keys.py`.

## Architecture

Read `README.md` for the product story and `AGENT-INTELLIGENCE.md` for the reasoning behind the current agent design. The key architectural shape:

### Everything is a module; every module is a ReAct agent

A module is either a `module.yaml` (declarative: system prompt + sub-module list) or a `module.py` (custom `async def run(ctx, **kwargs)`). `Registry.auto_discover()` (`mesmer/core/registry.py`) walks a module root, recursing into subdirectories, and any directory containing `module.yaml`/`module.py` becomes a registered module. Built-in modules live in the top-level `modules/` directory (attacks/, profilers/, techniques/) — **note: `modules/` is a sibling of the `mesmer/` package, not inside it**. `BUILTIN_MODULES` in `mesmer/core/runner.py` resolves this path.

Sub-modules are exposed to the parent agent as OpenAI-style function-calling tools. A "leader" like `system-prompt-extraction` is just a module whose `sub_modules` list references profilers and techniques. The leader delegates; sub-modules run their own ReAct loop and return a string result.

### The ReAct loop (`mesmer/core/agent/engine.py`)

`run_react_loop` is the universal execution engine. The cycle is **Plan → Execute → Judge → Reflect → Update**:

1. **Plan** — leader sees attack graph state (dead ends, frontier, best score) plus reflections from prior attempts.
2. **Execute** — agent emits either a target message (`send_message` tool) or a sub-module call.
3. **Judge** — `mesmer/core/agent/judge.py` scores the attempt 1-10 and extracts insights (separate LLM call with a technique-specific `judge_rubric` composed from the module + scenario).
4. **Reflect** — generate 1-3 "frontier" suggestions for next moves.
5. **Update** — results are written into the per-target `AttackGraph` and `TargetMemory`.

Retry + key-rotation logic lives in `core/agent/retry.py::_completion_with_retry`: rate-limit errors cool down the offending key (`compute_cooldown`, `KeyPool.cool_down`) and rotate, rather than sleeping. When all keys are cooled the loop emits `rate_limit_wall` and stops.

### Turn budgets and session resets

`Context` (in `core/agent/context.py`) tracks turns and raises `TurnBudgetExhausted` when exceeded. `ModuleConfig.reset_target: bool` (see `core/module.py`) controls whether the target connection is reset before the module runs — useful for sibling modules that shouldn't share the target's conversational memory. Leave `False` for chained attacks like foot-in-door that depend on continuity.

### Agent package layout (`core/agent/`)

Everything attacker-runtime lives under `mesmer/core/agent/`. Rules:

- **One file per tool** (`tools/send_message.py`, `tools/ask_human.py`, `tools/conclude.py`, `tools/sub_module.py`). Schema + handler collocated — the OpenAI function description and the code that runs when it's called change together. Never introduce a "handlers.py" catch-all.
- **No defensive `getattr(obj, field, default)`** on `Context`, `Turn`, or `ModuleConfig`. Those fields are declared; `getattr` hides typos and means type checkers can't help. If a test passes `MagicMock()`, the test is wrong — set the attributes explicitly.
- **No hardcoded role / tool-name strings.** Use the enums in `core/constants.py`: `CompletionRole`, `ToolName`, `TurnKind`, `ScenarioMode`, `ContextMode`, `BudgetMode`, `NodeStatus`, `NodeSource`, `LogEvent`.
- **All mesmer errors derive from `MesmerError`** in `core/errors.py` (`TurnBudgetExhausted`, `HumanQuestionTimeout`, `CompressionLLMError`). Never use bare `except Exception: return ""` to mask an LLM failure — raise a typed error and catch it at a single boundary that logs a real reason. Compression is the canonical pattern: raise in `_raw_completion`/`_summarise_block`, catch once in `maybe_compress`.
- **LLM-JSON parsing** goes through `core/agent/parsing.py::parse_llm_json` (handles the ```` ```json ... ``` ```` fence the models love to add). Don't reimplement the strip-then-`json.loads` dance per site.
- **Group by cohesion, not abstraction.** Resist the urge to invent "shared X" files (a `serialization.py` that holds half the serialization code while the other half sits elsewhere is worse than no file at all). If a helper is used by exactly one module, inline it; if it's used by one cluster, live with that cluster.

### Persistence layout

State is plain JSON on disk at `~/.mesmer/`:

```
~/.mesmer/
├── targets/{target-hash}/
│   ├── graph.json       # AttackGraph (nodes, edges, scores, dead ends, frontier)
│   ├── profile.json     # target personality + defense patterns (from safety-profiler)
│   ├── episodes.jsonl   # append-only attempt log with reflections
│   └── tactics.json     # per-technique success/failure counts
└── global/techniques.json  # cross-target aggregate stats
```

No database, no embeddings. `core/agent/memory.py` owns `TargetMemory`/`GlobalMemory`; `core/graph.py` owns the attack graph structure. The target hash is derived from the scenario's target config (URL + adapter). `--fresh` bypasses loading the existing graph.

### Targets

`mesmer/targets/base.py` defines the abstract `Target` interface. Four adapters: `echo` (mock), `openai_compat` (REST against any OpenAI-compatible endpoint), `rest` (generic HTTP with templated bodies and JSONPath response extraction), `websocket_target` (declarative WS with configurable handshake, send/receive templates, frame routing). `mesmer/targets/__init__.create_target` dispatches from scenario config.

### Interfaces

- `mesmer/interfaces/cli.py` — Click-based CLI, the primary entry point (`mesmer` console script → `cli:cli`).
- `mesmer/interfaces/web/backend/` — FastAPI + SSE server that streams `log`, `graph_update`, and `key_status` events to a Svelte 5 + D3 frontend in `frontend/`.

Both interfaces go through `core/runner.execute_run(RunConfig, ...)`. When adding run-level behavior, prefer changing `runner.py` so CLI and web stay in sync; the logging protocol is a `LogFn = Callable[[str, str], None]` (event name, detail string).

### Human-in-the-loop

Hints (via `--hint`, `mesmer hint`, or the debrief command) become high-priority frontier nodes in the graph and are explored first on the next run. `HumanQuestionBroker` (in `core/agent/context.py`) is the hook used by co-op/plan modes for mid-run questions — the web UI implements a broker; the CLI can run in plain autonomous mode without one.

## Conventions

- **Python 3.10+**, Pydantic v2, LiteLLM for all provider calls (never import provider SDKs directly — model strings like `openrouter/...`, `anthropic/...`, `ollama/...` drive LiteLLM's dispatch).
- **Async-first.** New code in `core/`, `targets/`, and interfaces should be `async`; tests use `pytest-asyncio`.
- **Modules prefer YAML.** Reach for `module.py` only when you need programmatic control (e.g., stateful orchestration or custom I/O). If you write a Python module, still expose `name`, `description`, `theory`, `system_prompt`, `sub_modules` so the leader can reason about it.
- **Ruff settings:** `target-version = "py311"`, `line-length = 100`.
- **Graph state is the source of truth across runs.** When modifying module behavior, think about what ends up in `graph.json`/`episodes.jsonl` and whether replaying old state still makes sense.
