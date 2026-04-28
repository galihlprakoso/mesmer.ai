"""Mesmer Web UI — FastAPI server.

Serves the Svelte SPA and provides a REST + WebSocket API for
real-time attack execution, graph inspection, and human-in-the-loop.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mesmer.core.agent.context import Context, HumanQuestionBroker
from mesmer.core.agent.parsing import parse_llm_json
from mesmer.core.artifacts import ArtifactError, ArtifactStore, artifact_title, validate_artifact_id
from mesmer.core.constants import LogEvent, ScenarioMode
from mesmer.core.graph import AttackGraph
from mesmer.core.agent.memory import TargetMemory, GlobalMemory
from mesmer.core.registry import Registry
from mesmer.core.runner import (
    BUILTIN_MODULES,
    RunConfig,
    execute_run,
    list_scenarios as _list_scenarios,
    list_modules as _list_modules,
    list_targets as _list_targets,
)
from mesmer.core.scenario import load_scenario
from mesmer.interfaces.web.backend.events import EventBus
from mesmer.interfaces.web.backend.leader_chat import run_leader_chat

# Resolve paths
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    scenario_path: str
    model: str | None = None
    max_turns: int | None = None
    hints: list[str] = []
    fresh: bool = False
    # Optional ScenarioMode override ("trials" or "continuous"). None = honour
    # the YAML's mode field. Mirrors the CLI --mode flag.
    scenario_mode: str | None = None


class DebriefRequest(BaseModel):
    scenario_path: str


class LeaderChatRequest(BaseModel):
    scenario_path: str
    message: str


class TargetTestRequest(BaseModel):
    scenario_path: str
    message: str | None = None
    timeout_s: float = 20.0


class CreateScenarioRequest(BaseModel):
    name: str
    yaml_content: str


class UpdateScenarioRequest(BaseModel):
    yaml_content: str


class ValidateScenarioRequest(BaseModel):
    yaml_content: str


class EditorChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class EditorChatRequest(BaseModel):
    yaml_content: str
    message: str
    history: list[EditorChatMessage] = []


# ---------------------------------------------------------------------------
# Helpers — scenario CRUD
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:80] or "scenario"


def _resolve_under(base: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``base``, refusing parent escapes.

    Returns the absolute path if it is a descendant of ``base``,
    otherwise ``None``. ``rel`` is a forward-slash path coming
    from the frontend (the ``{name:path}`` segment).
    """
    base_abs = base.resolve()
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base_abs)
    except ValueError:
        return None
    return candidate


def _validate_yaml_via_loader(yaml_content: str) -> tuple[bool, str | None]:
    """Write ``yaml_content`` to a temp file and run ``load_scenario``.

    Returns ``(True, None)`` on success or ``(False, error_message)``.
    Any failure surfaces the loader's exception text verbatim — that's
    what users get in the editor's lint badge.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(yaml_content)
        tmp_path = Path(fh.name)
    try:
        load_scenario(str(tmp_path))
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        tmp_path.unlink(missing_ok=True)


def _editor_chat_system_prompt(modules: list[dict]) -> str:
    """System prompt for the vibe-code editor chat.

    Lists the live module roster (so the LLM picks real names), the
    target adapters, the YAML schema, and the JSON envelope it must
    return. Generated per request because the registry is cheap.
    """
    module_lines = "\n".join(
        f"- {m['name']} (tier {m.get('tier', 2)}) — {m.get('description', '').strip()}"
        for m in modules
    )
    return f"""You are a YAML scenario editor for **mesmer**, a cognitive-hacking
red-teaming framework. The user wants to create or modify a scenario
that drives an LLM-vs-LLM attack.

# Scenario YAML schema

```yaml
name: "Human-readable scenario name"           # required
description: "What this scenario probes"       # required
target:
  adapter: openai | echo | rest | websocket    # required
  base_url: "https://..."                      # for openai-compat
  model: "gpt-4o-mini"                         # for openai-compat
  url: "https://..."                           # for rest/ws adapters
  api_key: "${{ENV_VAR_NAME}}"
  system_prompt: ""                            # optional, injected target-side
objective:
  goal: "What the attacker is trying to achieve"
  success_signals: ["short hint", ...]         # optional
  max_turns: 25                                # per-module turn budget
modules: [<manager module name>, ...]          # required, see list below
agent:
  model: "anthropic/claude-opus-4-7"
  sub_module_model: "anthropic/claude-haiku-4-5"
  judge_model: ""                              # falls back to model if empty
  api_key: "${{ANTHROPIC_API_KEY}}"
  temperature: 0.7
mode: trials                                   # trials | continuous
```

# Available modules (use exact names in `modules:`)

{module_lines}

# Response format

You MUST respond with a single JSON object — nothing else, no prose
outside it, no backticks. Schema:

```json
{{
  "reply": "short conversational message to the user",
  "updated_yaml": "<full rewritten YAML>" or null
}}
```

- Set `updated_yaml` to the COMPLETE new YAML when the user wants a
  change applied. Always emit the full file, not a diff or fragment.
- Set `updated_yaml` to `null` when the user asked a question or you
  just need clarification — the editor leaves the YAML untouched.
- Keep `reply` brief (1–3 sentences). Don't paste the YAML in `reply`;
  it's already in `updated_yaml`.
- Preserve any `${{ENV_VAR}}` placeholders the user has in the YAML —
  don't substitute, don't invent new ones unless asked.
"""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(scenario_dir: str = ".") -> FastAPI:
    """Create the FastAPI app with all routes."""

    app = FastAPI(title="Mesmer", version="0.2.0")

    # State
    bus = EventBus()
    current_run_task: asyncio.Task | None = None
    current_broker: HumanQuestionBroker | None = None
    # Live ctx of the running attack — set by execute_run's on_ctx_ready hook,
    # cleared on completion/error/stop. The leader-chat endpoint pushes onto
    # ctx.operator_messages when a run is active for the same scenario.
    current_ctx: Context | None = None
    current_scenario_path: str | None = None
    run_state: dict = {"status": "idle"}

    def _set_run_task(task: asyncio.Task | None):
        nonlocal current_run_task
        current_run_task = task

    def _broadcast_question(question: dict):
        """Called by the broker when the agent asks the human something."""
        bus.emit_status("human_question", **question)

    def _load_artifacts_for_target(target_hash: str) -> ArtifactStore:
        live_hash = (
            current_ctx.target_memory.target_hash
            if current_ctx is not None and current_ctx.target_memory is not None
            else None
        )
        if current_ctx is not None and live_hash == target_hash:
            return current_ctx.artifacts
        artifacts_dir = Path.home() / ".mesmer" / "targets" / target_hash / "artifacts"
        return ArtifactStore.from_files(artifacts_dir)

    def _legacy_module_artifact_ids() -> set[str]:
        registry = Registry()
        registry.auto_discover(BUILTIN_MODULES)
        return set(registry.modules)

    # ----- Static files (Svelte SPA) -----

    @app.get("/")
    async def index():
        index_path = FRONTEND_DIST / "index.html"
        if index_path.exists():
            return HTMLResponse(index_path.read_text())
        return HTMLResponse(
            "<html><body><h1>Mesmer Web UI</h1>"
            "<p>Frontend not built yet. Run <code>cd mesmer/interfaces/web/frontend && npm install && npm run build</code></p>"
            "</body></html>"
        )

    # Mount static assets (JS, CSS) if dist exists
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    # ----- API: Scenarios -----

    @app.get("/api/scenarios")
    async def get_scenarios():
        # Enrich each scenario with target_hash + module_tier so the
        # list page can show "has prior runs" badges and tier colours.
        registry = Registry()
        registry.auto_discover(BUILTIN_MODULES)
        items = _list_scenarios(scenario_dir)
        for item in items:
            try:
                s = load_scenario(item["path"])
                memory = TargetMemory(s.target)
                item["target_hash"] = memory.target_hash
                item["has_graph"] = memory.exists()
                # Tier of the FIRST manager in the list — used by the UI to
                # render a tier badge. Multi-manager scenarios just preview
                # the first; the executive itself has no meaningful tier.
                first_mod = s.modules[0] if s.modules else None
                item["module_tier"] = registry.tier_of(first_mod) if first_mod else None
            except Exception:
                # _list_scenarios already filters obviously broken files,
                # but if env-var resolution fails (missing API key) we
                # don't want the whole list to 500. Leave the enrichment
                # fields off and let the frontend fall back to defaults.
                pass
        return items

    @app.get("/api/scenarios/{name:path}")
    async def get_scenario(name: str):
        path = Path(scenario_dir) / name
        if not path.exists():
            return JSONResponse({"error": f"Scenario not found: {name}"}, status_code=404)
        try:
            s = load_scenario(str(path))
            memory = TargetMemory(s.target)
            # Read raw YAML so the editor can populate without re-serialising
            # (preserves comments, ordering, env-var placeholders).
            raw_yaml = path.read_text(encoding="utf-8")
            result = {
                "name": s.name,
                "description": s.description,
                "target": {
                    "adapter": s.target.adapter,
                    "url": s.target.url or s.target.base_url or "",
                    "model": s.target.model or "",
                },
                "target_hash": memory.target_hash,
                "has_graph": memory.exists(),
                "objective": {
                    "goal": s.objective.goal,
                    "success_signals": s.objective.success_signals,
                    "max_turns": s.objective.max_turns,
                },
                "modules": list(s.modules),
                "artifacts": [artifact.to_dict() for artifact in s.artifacts],
                "leader_prompt": s.leader_prompt,
                "agent": {
                    "model": s.agent.model,
                },
                "yaml_content": raw_yaml,
            }
            # Include saved graph data if it exists
            if memory.exists():
                g = memory.load_graph()
                result["graph"] = json.loads(g.to_json())
                result["graph_stats"] = g.stats()
                bus.set_graph(g)
            return result
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/scenarios")
    async def create_scenario(req: CreateScenarioRequest):
        """Create a new scenario in ``scenarios/private/{slug}.yaml``.

        Validates the YAML by running it through ``load_scenario`` after
        write — on parse error the file is deleted and 400 returned.
        """
        slug = _slugify(req.name)
        target_dir = Path(scenario_dir) / "scenarios" / "private"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{slug}.yaml"
        if target_path.exists():
            return JSONResponse(
                {"error": f"Scenario already exists at {target_path}. Pick a different name."},
                status_code=409,
            )
        target_path.write_text(req.yaml_content, encoding="utf-8")
        try:
            load_scenario(str(target_path))
        except Exception as e:
            target_path.unlink(missing_ok=True)
            return JSONResponse(
                {"error": f"YAML is invalid: {type(e).__name__}: {e}"},
                status_code=400,
            )
        # Return path relative to scenario_dir so the frontend can navigate
        # to /api/scenarios/{path}. Always use resolved abs paths for the
        # diff so it works whether scenario_dir is absolute or "." (cwd).
        rel = target_path.resolve().relative_to(Path(scenario_dir).resolve())
        return {"path": str(rel), "name": req.name}

    @app.put("/api/scenarios/{name:path}")
    async def update_scenario(name: str, req: UpdateScenarioRequest):
        """Overwrite an existing scenario file.

        Path-traversal-guarded: refuses paths that resolve outside the
        configured ``scenario_dir``. Validates after write; on parse
        failure the prior content is restored.
        """
        target_path = _resolve_under(Path(scenario_dir), name)
        if target_path is None:
            return JSONResponse(
                {"error": "Refusing to write outside the scenario directory."},
                status_code=400,
            )
        if not target_path.exists():
            return JSONResponse({"error": f"Scenario not found: {name}"}, status_code=404)
        prior = target_path.read_text(encoding="utf-8")
        target_path.write_text(req.yaml_content, encoding="utf-8")
        try:
            load_scenario(str(target_path))
        except Exception as e:
            target_path.write_text(prior, encoding="utf-8")
            return JSONResponse(
                {"error": f"YAML is invalid: {type(e).__name__}: {e}"},
                status_code=400,
            )
        return {"path": name, "status": "saved"}

    @app.post("/api/scenarios/validate")
    async def validate_scenario(req: ValidateScenarioRequest):
        """Validate a YAML payload without writing anything to disk."""
        ok, err = _validate_yaml_via_loader(req.yaml_content)
        if ok:
            return {"ok": True, "error": None}
        return {"ok": False, "error": err}

    @app.post("/api/scenario-editor-chat")
    async def scenario_editor_chat(req: EditorChatRequest):
        """Vibe-code chat: user message + current YAML → reply + edited YAML.

        Single completion (no tool-calling). Reads ``ANTHROPIC_API_KEY``
        from env by default — the LLM that powers this is decoupled from
        any individual scenario's agent config so the editor works even
        for blank/new scenarios.
        """
        import os
        import litellm

        litellm.suppress_debug_info = True

        registry = Registry()
        registry.auto_discover(BUILTIN_MODULES)
        modules = registry.list_modules()

        system_prompt = _editor_chat_system_prompt(modules)
        user_payload = (
            "Current scenario YAML:\n```yaml\n"
            f"{req.yaml_content or '# (empty — user wants to create a new scenario from scratch)'}\n"
            "```\n\nUser message: " + req.message
        )

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for h in req.history[-12:]:  # cap history so prompts stay bounded
            if h.role in ("user", "assistant") and h.content:
                messages.append({"role": h.role, "content": h.content})
        messages.append({"role": "user", "content": user_payload})

        model = os.environ.get("MESMER_EDITOR_MODEL", "anthropic/claude-opus-4-7")
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
        }
        api_key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if api_key:
            kwargs["api_key"] = api_key.strip()

        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content or ""
        except Exception as e:
            return JSONResponse(
                {"error": f"Editor LLM call failed: {type(e).__name__}: {e}"},
                status_code=500,
            )

        try:
            envelope = parse_llm_json(content)
        except Exception:
            # The LLM didn't return JSON — surface the raw text in the
            # reply so the user can still see what it said.
            return {"reply": content, "updated_yaml": None}

        reply = envelope.get("reply") or ""
        updated_yaml = envelope.get("updated_yaml")
        if updated_yaml is not None and not isinstance(updated_yaml, str):
            updated_yaml = None
        return {"reply": reply, "updated_yaml": updated_yaml}

    # ----- API: Modules -----

    @app.get("/api/modules")
    async def get_modules():
        return _list_modules()

    @app.get("/api/modules/{name}")
    async def get_module(name: str):
        from mesmer.core.registry import Registry
        from mesmer.core.runner import BUILTIN_MODULES

        registry = Registry()
        registry.auto_discover(BUILTIN_MODULES)
        mod = registry.get(name)
        if mod is None:
            return JSONResponse({"error": f"Module not found: {name}"}, status_code=404)
        return {
            "name": mod.name,
            "description": mod.description,
            "theory": mod.theory,
            "system_prompt": mod.system_prompt,
            "tier": mod.tier,
            "sub_modules": mod.sub_module_names,
        }

    # ----- API: Targets -----

    @app.get("/api/targets")
    async def get_targets():
        return _list_targets()

    @app.post("/api/target/test")
    async def test_target_connection(req: TargetTestRequest):
        """Send one harmless probe through the configured target adapter."""
        from mesmer.targets import create_target

        try:
            scenario = load_scenario(req.scenario_path)
            target = create_target(scenario.target)
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"Target config failed: {type(e).__name__}: {e}"},
                status_code=400,
            )

        probe = (
            req.message.strip()
            if isinstance(req.message, str) and req.message.strip()
            else "Connection check. Reply briefly with MESMER_TARGET_OK."
        )
        timeout_s = max(1.0, min(float(req.timeout_s or 20.0), 60.0))
        started = time.monotonic()
        try:
            reply = await asyncio.wait_for(target.send(probe), timeout=timeout_s)
            try:
                await target.reset()
            except Exception:
                pass
        except Exception as e:
            return JSONResponse(
                {
                    "ok": False,
                    "adapter": scenario.target.adapter,
                    "model": scenario.target.model or "",
                    "url": scenario.target.url or scenario.target.base_url or "",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": f"{type(e).__name__}: {e}",
                },
                status_code=502,
            )

        preview = (reply or "").strip()
        if len(preview) > 500:
            preview = preview[:500].rstrip() + "..."
        return {
            "ok": True,
            "adapter": scenario.target.adapter,
            "model": scenario.target.model or "",
            "url": scenario.target.url or scenario.target.base_url or "",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "response_preview": preview,
        }

    @app.get("/api/targets/{target_hash}/graph")
    async def get_target_graph(target_hash: str):
        graph_path = Path.home() / ".mesmer" / "targets" / target_hash / "graph.json"
        if not graph_path.exists():
            return JSONResponse({"error": "Graph not found"}, status_code=404)
        try:
            g = AttackGraph.from_json(graph_path.read_text())
            return {
                "graph": json.loads(g.to_json()),
                "stats": g.stats(),
            }
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/targets/{target_hash}/belief-graph")
    async def get_target_belief_graph(target_hash: str):
        """Return the typed Belief Attack Graph snapshot for a target.

        Same shape contract as ``get_target_graph`` (a wrapping object
        with ``graph`` + ``stats`` keys) so the frontend can pick the
        same handler shape for both views. During a live run the graph
        is served from ``current_ctx`` first because the runner only
        persists ``belief_graph.json`` at run completion. Returns 404
        when neither live state nor persisted state exists yet.
        """
        from mesmer.core.belief_graph import BeliefGraph
        from mesmer.core.agent.graph_compiler import GraphContextCompiler
        from mesmer.core.constants import BeliefRole

        def _payload(bg: BeliefGraph):
            return {
                "graph": json.loads(bg.to_json()),
                "stats": bg.stats(),
                "prompt_context": GraphContextCompiler(bg).compile(
                    role=BeliefRole.LEADER,
                    token_budget=1200,
                ),
            }

        live_graph = current_ctx.belief_graph if current_ctx is not None else None
        live_hash = (
            current_ctx.target_memory.target_hash
            if current_ctx is not None and current_ctx.target_memory is not None
            else (live_graph.target_hash if live_graph is not None else None)
        )
        if live_graph is not None and live_hash == target_hash:
            try:
                return _payload(live_graph)
            except Exception as e:  # noqa: BLE001 — surface load errors to UI
                return JSONResponse({"error": str(e)}, status_code=400)

        target_dir = Path.home() / ".mesmer" / "targets" / target_hash
        snapshot_path = target_dir / "belief_graph.json"
        deltas_path = target_dir / "belief_deltas.jsonl"
        if not snapshot_path.exists() and not deltas_path.exists():
            return JSONResponse({"error": "Belief graph not found"}, status_code=404)
        try:
            if snapshot_path.exists():
                bg = BeliefGraph.from_json(snapshot_path.read_text())
            else:
                bg = BeliefGraph.replay(deltas_path, target_hash=target_hash)
            return _payload(bg)
        except Exception as e:  # noqa: BLE001 — surface load errors to UI
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/targets/{target_hash}/artifacts")
    async def get_target_artifacts(target_hash: str):
        """List durable Markdown artifacts for a target."""
        artifacts = _load_artifacts_for_target(target_hash)
        legacy_module_ids = _legacy_module_artifact_ids()
        return {
            "items": [
                summary.to_dict()
                for summary in artifacts.summaries()
                if summary.id not in legacy_module_ids
            ]
        }

    @app.get("/api/targets/{target_hash}/artifacts/search")
    async def search_target_artifacts(target_hash: str, query: str = "", limit: int = 20):
        """Search durable Markdown artifacts for a target."""
        artifacts = _load_artifacts_for_target(target_hash)
        legacy_module_ids = _legacy_module_artifact_ids()
        return {
            "items": [
                hit.to_dict()
                for hit in artifacts.search(query, limit=max(1, min(int(limit or 20), 50)))
                if hit.artifact_id not in legacy_module_ids
            ]
        }

    @app.get("/api/targets/{target_hash}/artifacts/{artifact_id}")
    async def get_target_artifact(target_hash: str, artifact_id: str):
        """Read one durable Markdown artifact for a target."""
        try:
            artifact_id = validate_artifact_id(artifact_id)
        except ArtifactError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        artifacts = _load_artifacts_for_target(target_hash)
        content = artifacts.get(artifact_id)
        if not content.strip():
            return JSONResponse({"error": f"Artifact not found: {artifact_id}"}, status_code=404)
        return {
            "artifact_id": artifact_id,
            "title": artifact_title(artifact_id),
            "content": content,
        }

    # ----- API: Stats -----

    @app.get("/api/stats")
    async def get_stats():
        return GlobalMemory.load_stats()

    # ----- API: Run -----

    @app.get("/api/run/status")
    async def get_run_status():
        return run_state

    @app.post("/api/run")
    async def start_run(req: RunRequest):
        nonlocal current_run_task, current_broker
        if current_run_task and not current_run_task.done():
            return JSONResponse(
                {"error": "A run is already in progress. Stop it first."},
                status_code=409,
            )

        bus.clear_history()
        run_state.update({"status": "running", "scenario": req.scenario_path})

        # Fresh broker per run — prior questions can't outlive a run.
        current_broker = HumanQuestionBroker(on_question=_broadcast_question)

        # Resolve ScenarioMode override from the request (None => honour YAML).
        scenario_mode_override: ScenarioMode | None = None
        if req.scenario_mode:
            try:
                scenario_mode_override = ScenarioMode(req.scenario_mode.lower())
            except ValueError:
                scenario_mode_override = None

        config = RunConfig(
            scenario_path=req.scenario_path,
            model_override=req.model,
            max_turns_override=req.max_turns,
            hints=req.hints,
            fresh=req.fresh,
            human_broker=current_broker,
            scenario_mode_override=scenario_mode_override,
        )

        async def _run():
            nonlocal current_ctx, current_scenario_path
            try:
                bus.emit_status("running", scenario=req.scenario_path)

                def _on_graph_update(graph):
                    bus.set_graph(graph)
                    bus.emit_graph_snapshot()

                def _on_pool_ready(pool):
                    bus.set_key_pool(pool)
                    # Broadcast initial state so the UI shows N/M right away
                    bus.emit_key_status()

                def _on_ctx_ready(ctx: Context):
                    nonlocal current_ctx, current_scenario_path
                    current_ctx = ctx
                    current_scenario_path = req.scenario_path

                result = await execute_run(
                    config,
                    log=bus.log_fn,
                    on_graph_update=_on_graph_update,
                    on_pool_ready=_on_pool_ready,
                    on_ctx_ready=_on_ctx_ready,
                )
                bus.set_graph(result.graph)
                run_state.update(
                    {
                        "status": "completed",
                        "result": result.result,
                        "run_id": result.run_id,
                        "graph_stats": result.graph.stats(),
                    }
                )
                bus.emit_status(
                    "completed",
                    result=result.result,
                    run_id=result.run_id,
                    graph_stats=result.graph.stats(),
                )
            except asyncio.CancelledError:
                run_state.update({"status": "stopped"})
                bus.emit_status("stopped")
            except Exception as e:
                run_state.update({"status": "error", "error": str(e)})
                bus.emit_status("error", error=str(e))
            finally:
                current_ctx = None
                current_scenario_path = None

        current_run_task = asyncio.create_task(_run())
        _set_run_task(current_run_task)

        return {"status": "started", "scenario": req.scenario_path}

    @app.post("/api/run/stop")
    async def stop_run():
        if current_run_task and not current_run_task.done():
            current_run_task.cancel()
            return {"status": "stopping"}
        return {"status": "no_run_active"}

    # ----- API: Chat -----

    @app.get("/api/chat")
    async def get_chat(scenario_path: str, limit: int = 20):
        """Return persisted operator <> leader chat for warm-load on
        page refresh. Last ``limit`` rows, oldest-first."""
        scenario = load_scenario(scenario_path)
        memory = TargetMemory(scenario.target)
        return {"items": memory.load_chat(limit=max(1, min(limit, 100)))}

    @app.post("/api/leader-chat")
    async def leader_chat_endpoint(req: LeaderChatRequest):
        """One operator turn → leader reply.

        Two paths:
          - **Run active for this scenario** — push the message onto the live
            ctx's ``operator_messages`` queue. The leader sees it in its next
            iteration. Persist to chat.jsonl + emit OPERATOR_MESSAGE so the
            chat UI reflects the queue. No LLM call here.
          - **Idle** — delegate to ``leader_chat.run_leader_chat``: the
            leader runs a tool-calling loop over the persisted graph + run
            logs and replies grounded in real data.
        """
        message = (req.message or "").strip()
        if not message:
            return JSONResponse({"error": "message must be non-empty"}, status_code=400)
        scenario = load_scenario(req.scenario_path)
        memory = TargetMemory(scenario.target)

        if (
            current_run_task is not None
            and not current_run_task.done()
            and current_ctx is not None
            and current_scenario_path == req.scenario_path
        ):
            ts = time.time()
            memory.append_chat("user", message, ts)
            current_ctx.operator_messages.append(
                {
                    "role": "user",
                    "content": message,
                    "timestamp": ts,
                }
            )
            bus.log_fn(LogEvent.OPERATOR_MESSAGE.value, message)
            return {"queued": True, "reply": None, "tool_trace": [], "updated_artifact": None}

        def _broadcast_tool_call(name: str, args: dict):
            bus.log_fn(
                "leader_chat_tool_call",
                json.dumps({"name": name, "args": args}, default=str),
            )

        try:
            result = await run_leader_chat(
                scenario,
                memory,
                message,
                on_tool_call=_broadcast_tool_call,
            )
        except Exception as e:
            return JSONResponse(
                {"error": f"Leader-chat failed: {e}"},
                status_code=500,
            )
        return {
            "queued": False,
            "reply": result.reply,
            "tool_trace": result.tool_trace,
            "updated_artifact": result.updated_artifact,
        }

    # ----- API: Debrief -----

    @app.post("/api/debrief")
    async def generate_debrief(req: DebriefRequest):
        scenario = load_scenario(req.scenario_path)
        memory = TargetMemory(scenario.target)

        if not memory.exists():
            return JSONResponse({"error": "No graph found. Run an attack first."}, status_code=404)

        g = memory.load_graph()
        agent_config = scenario.agent

        import litellm

        litellm.suppress_debug_info = True

        prompt = f"""You are debriefing a human operator after an AI red-teaming run.

Attack graph summary:
{g.format_summary()}

Target: {scenario.target.adapter} → {scenario.target.url or scenario.target.model}
Objective: {scenario.objective.goal}

Based on the graph state, generate 3-5 smart questions to ask the human operator.
Focus on ambiguous findings, patterns, and strategic suggestions.
Return as JSON array of strings."""

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
            content = response.choices[0].message.content or "[]"
            # Try to parse as JSON, fallback to splitting lines
            try:
                questions = json.loads(content)
            except json.JSONDecodeError:
                questions = [line.strip() for line in content.split("\n") if line.strip()]
            return {"questions": questions}
        except Exception as e:
            return {
                "questions": [
                    "What patterns did you notice in the target's responses?",
                    "Any specific target behavior that seemed exploitable?",
                    "What should we try differently next run?",
                ],
                "error": str(e),
            }

    # ----- WebSocket -----

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        queue = bus.subscribe()

        try:
            # Send current state
            await ws.send_json({"type": "status", **run_state})

            # Send replay buffer
            for msg in bus.history:
                await ws.send_json(msg)

            # Bidirectional: receive client messages + send events
            async def _send_events():
                while True:
                    msg = await queue.get()
                    await ws.send_json(msg)

            async def _receive_messages():
                while True:
                    data = await ws.receive_json()
                    mtype = data.get("type")

                    if mtype == "human_answer":
                        # Co-op: human is answering an agent question
                        qid = data.get("question_id")
                        answer = data.get("answer", "")
                        if qid and current_broker is not None:
                            delivered = current_broker.answer(qid, answer)
                            bus.emit_status(
                                "human_answered",
                                question_id=qid,
                                delivered=delivered,
                                answer=answer,
                            )

            # Run both concurrently
            await asyncio.gather(
                _send_events(),
                _receive_messages(),
            )

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            bus.unsubscribe(queue)

    return app
