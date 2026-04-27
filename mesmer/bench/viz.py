"""Bench visualization — render a run's per-trial decision trees as HTML.

After a bench run emits ``{stem}-summary.json`` + ``events/*.graph.json``,
this module reads those artefacts and writes a self-contained
``{stem}-viz.html`` next to them. Open the HTML in a browser, pan/zoom
the tree, click a node for full messages + judge verdict + reflection.

No build step, no backend — the HTML embeds its own data blob via
``<script type="application/json" id="bench-data">`` and loads D3 either
from CDN (default) or inlined from ``_assets/d3.v7.min.js`` (``offline=True``).

Contract: this is a pure post-processing step over artefacts that are
already on disk. :func:`build_viz_html` never calls an LLM, never mutates
the summary, and never crashes the surrounding ``run_benchmark`` — callers
that want it opt in, and the orchestrator catches exceptions around it.

Auto-split rule: if the inlined payload would exceed
:data:`VIZ_INLINE_BYTES_LIMIT`, emit one HTML per target plus an
``{stem}-viz-index.html`` that links them. Below the limit, a single
``{stem}-viz.html``. The limit is a module-level constant so tests can
patch it to exercise the split path.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Template + vendored D3 sit next to this file.
_HERE = Path(__file__).parent
TEMPLATE_PATH = _HERE / "viz_template.html"
D3_ASSET_PATH = _HERE / "_assets" / "d3.v7.min.js"
D3_CDN_URL = "https://d3js.org/d3.v7.min.js"

# Above this inlined-JSON size, split per-target instead of emitting a
# single HTML. 50 MB leaves plenty of headroom for 50-row iteration runs
# while catching the 569-row publication runs before they produce an
# unloadable HTML.
VIZ_INLINE_BYTES_LIMIT = 50 * 1024 * 1024


@dataclass(frozen=True)
class VizResult:
    """Paths written by :func:`build_viz_html`.

    ``primary`` is the HTML a user should open first — the single combined
    file when inlining, or the index file when split across targets.
    ``extras`` lists the per-target files written alongside the index (empty
    when not split).
    """

    primary: Path
    extras: tuple[Path, ...] = ()

    @property
    def all_paths(self) -> tuple[Path, ...]:
        return (self.primary, *self.extras)


def build_viz_html(
    summary_path: Path | str,
    *,
    offline: bool = False,
    output_path: Path | str | None = None,
) -> VizResult:
    """Render a bench run's per-trial trees into a self-contained HTML file.

    :param summary_path: path to ``{stem}-summary.json`` emitted by
        :func:`mesmer.bench.orchestrator.run_benchmark`. The sibling
        ``{stem}-{target}__{arm}.jsonl`` files and ``events/`` directory
        are located relative to this path.
    :param offline: if true, inline ``_assets/d3.v7.min.js`` into the HTML
        instead of loading from the CDN. Inflates file size by ~280 KB but
        lets the HTML render with no network.
    :param output_path: override the default output location. When set and
        the run needs splitting, this path is treated as the index file and
        per-target files land as siblings with ``-{target}`` suffixes.

    :returns: a :class:`VizResult` naming the primary file (what to open)
        and any per-target extras produced by the auto-split path.

    :raises FileNotFoundError: if ``summary_path`` or the template cannot
        be read. Missing per-trial ``graph.json`` files are *not* errors —
        trials without a snapshot render a friendly "no graph captured"
        state in the viewer.
    """
    summary_path = Path(summary_path).resolve()
    if not summary_path.is_file():
        raise FileNotFoundError(f"Bench summary not found: {summary_path}")
    run_dir = summary_path.parent
    stem = _derive_stem(summary_path)

    summary = json.loads(summary_path.read_text())
    trials_payload, missing = _collect_trials(run_dir, stem, summary)

    if missing:
        logger.info(
            "bench-viz: %d trial(s) had no graph.json snapshot; rendering as "
            "empty trees. Example: %s",
            len(missing), missing[0],
        )

    template_src = TEMPLATE_PATH.read_text()
    d3_tag = _d3_script_tag(offline=offline)
    header_title = _header_title(summary)
    default_primary = (
        Path(output_path).resolve() if output_path else run_dir / f"{stem}-viz.html"
    )

    if _size_estimate(trials_payload) <= VIZ_INLINE_BYTES_LIMIT:
        html = _render_template(
            template_src,
            meta=_meta_for(summary),
            trials=trials_payload,
            d3_tag=d3_tag,
            header_title=header_title,
            title=_page_title(summary, scope=None),
        )
        default_primary.write_text(html)
        return VizResult(primary=default_primary, extras=())

    return _emit_split(
        trials_payload=trials_payload,
        summary=summary,
        run_dir=run_dir,
        stem=stem,
        template_src=template_src,
        d3_tag=d3_tag,
        header_title=header_title,
        primary_override=default_primary if output_path else None,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _derive_stem(summary_path: Path) -> str:
    """Strip ``-summary.json`` from the summary filename.

    Example: ``20260424T145633Z-v1-summary.json`` → ``20260424T145633Z-v1``.
    """
    name = summary_path.name
    suffix = "-summary.json"
    if not name.endswith(suffix):
        raise ValueError(
            f"Unexpected summary filename: {name!r}. "
            f"Expected '<stem>-summary.json'."
        )
    return name[: -len(suffix)]


def _meta_for(summary: dict) -> dict:
    """Pull rendering-relevant metadata out of the summary JSON.

    Also embeds a snapshot of the module registry so the viz can render
    each node's full ``description`` / ``theory`` / ``system_prompt`` /
    ``judge_rubric`` without the user having to crack open the YAMLs.
    Built-in modules are auto-discovered from ``BUILTIN_MODULES`` — the
    same path :func:`mesmer.core.runner.execute_run` resolves at run
    time. Discovery failures are non-fatal: the viz degrades to "no
    module config available" instead of crashing the build.
    """
    return {
        "spec": summary.get("spec"),
        "version": summary.get("version"),
        "module": summary.get("module"),
        "date": summary.get("date"),
        "dataset_sha256": summary.get("dataset_sha256", ""),
        "modules": _registry_snapshot(),
    }


def _registry_snapshot() -> dict:
    """Return ``{module_name: {description, theory, system_prompt, ...}}``.

    Pure side-effect-free read over the on-disk module YAMLs. Empty dict
    on any error so a missing registry never aborts the viz build.
    """
    try:
        from mesmer.core.registry import Registry
        from mesmer.core.runner import BUILTIN_MODULES
    except Exception as e:                               # pragma: no cover
        logger.warning("bench-viz: registry import failed: %s", e)
        return {}
    try:
        registry = Registry()
        registry.auto_discover(BUILTIN_MODULES)
    except Exception as e:                               # pragma: no cover
        logger.warning("bench-viz: registry auto_discover failed: %s", e)
        return {}
    out: dict[str, dict] = {}
    for name, mod in registry.modules.items():
        out[name] = {
            "name": name,
            "description": mod.description,
            "theory": mod.theory,
            "system_prompt": mod.system_prompt,
            "judge_rubric": mod.judge_rubric,
            "tier": mod.tier,
            "reset_target": bool(mod.reset_target),
            "sub_modules": mod.sub_module_names,
        }
    return out


def _header_title(summary: dict) -> str:
    spec = summary.get("spec") or "mesmer bench"
    version = summary.get("version") or ""
    return f"{spec} {version}".strip()


def _page_title(summary: dict, *, scope: str | None) -> str:
    base = _header_title(summary)
    date = summary.get("date") or ""
    suffix = f" · {scope}" if scope else ""
    return f"{base} — {date}{suffix}".strip(" —")


def _d3_script_tag(*, offline: bool) -> str:
    """Return the ``<script>`` tag that pulls in D3.

    Offline mode inlines the vendored minified bundle verbatim so the HTML
    works with no network. Online mode uses the CDN URL.
    """
    if offline:
        if not D3_ASSET_PATH.is_file():
            raise FileNotFoundError(
                f"offline=True requested but D3 asset missing at "
                f"{D3_ASSET_PATH}. Reinstall the package or drop "
                f"d3.v7.min.js there."
            )
        src = D3_ASSET_PATH.read_text()
        return f"<script>\n{src}\n</script>"
    return f'<script src="{D3_CDN_URL}"></script>'


def _collect_trials(
    run_dir: Path, stem: str, summary: dict,
) -> tuple[list[dict], list[str]]:
    """Walk every (target, arm) jsonl + its events/*.graph.json siblings.

    Returns ``(trials, missing_events_paths)``. Missing snapshot files are
    logged but don't abort — some arms (baseline) never write a snapshot,
    and partially-written runs should still render the trials they have.
    """
    trials: list[dict] = []
    missing: list[str] = []

    # Pull the list of (target, arm) pairs from the summary's cells. We
    # could glob for ``{stem}-*__*.jsonl`` instead, but the summary is the
    # source of truth for what belongs to *this* run.
    cells = summary.get("cells") or {}
    pairs: list[tuple[str, str]] = []
    if isinstance(cells, dict):
        for key, cell in cells.items():
            target = cell.get("target") or key.split("__")[0]
            arm = cell.get("arm") or key.split("__")[-1]
            pairs.append((target, arm))
    elif isinstance(cells, list):
        for cell in cells:
            pairs.append((cell["target"], cell["arm"]))

    for target, arm in pairs:
        jsonl = run_dir / f"{stem}-{target}__{arm}.jsonl"
        if not jsonl.is_file():
            continue
        with jsonl.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                graph_dict, graph_missing = _load_graph(run_dir, row)
                if graph_missing:
                    missing.append(graph_missing)
                trials.append(_trial_payload(row, graph_dict))

    return trials, missing


def _load_graph(run_dir: Path, row: dict) -> tuple[dict | None, str]:
    """Load the trial's graph snapshot; return ``(graph, missing_path)``.

    When the snapshot exists, returns ``(graph_dict, "")``.
    When the snapshot is missing or malformed, returns ``(None, path_str)``.
    Baseline trials legitimately have no snapshot — they're reported but
    don't abort the run.
    """
    events_rel = (row.get("trace") or {}).get("events_path") or ""
    if not events_rel:
        return None, ""
    # ``events_path`` in the trial row is relative to the *parent of
    # events_dir* — orchestrator._maybe_flush_events returns
    # ``events_dir.name / {trial_id}.jsonl`` (e.g. ``events/abc.jsonl``).
    # The graph snapshot is the same basename with ``.graph.json``.
    events_path = run_dir / events_rel
    graph_path = events_path.with_suffix(".graph.json")
    if not graph_path.is_file():
        return None, str(graph_path)
    try:
        return json.loads(graph_path.read_text()), ""
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("bench-viz: failed to read %s: %s", graph_path, e)
        return None, str(graph_path)


def _trial_payload(row: dict, graph_dict: dict | None) -> dict:
    """Project a trial jsonl row + graph snapshot into the viewer's shape.

    Keeps the payload flat and JS-friendly: the viewer filters by
    ``target/arm/sample_id/seed`` without nested lookups. ``module_tiers``
    is derived once here from the zip of ``modules_called`` + ``tier_sequence``
    so the client can color nodes without re-zipping per render.

    Baseline trials never write a graph snapshot, so we synthesize one
    here from the trial's ``baseline_attack_prompt`` /
    ``baseline_target_response`` fields. The result is a tiny two-node
    tree (``root`` → ``baseline``) that the renderer treats exactly like
    a real graph — no special baseline-only viz code path.
    """
    trace = row.get("trace") or {}
    modules_called = list(trace.get("modules_called") or [])
    tier_sequence = list(trace.get("tier_sequence") or [])
    module_tiers: dict[str, int] = {}
    for mod, tier in zip(modules_called, tier_sequence):
        if isinstance(mod, str) and isinstance(tier, int):
            module_tiers[mod] = tier

    arm = row.get("arm", "")
    if graph_dict is None and arm == "baseline":
        graph_dict = _synthesize_baseline_graph(row)

    return {
        "trial_id": row.get("trial_id", ""),
        "target": row.get("target", ""),
        "arm": arm,
        "sample_id": row.get("sample_id", ""),
        "seed": row.get("seed", 0),
        "success": bool(row.get("success")),
        "canary_turn": row.get("canary_turn"),
        "matched_text": row.get("matched_text", ""),
        "turns": row.get("turns", 0),
        "total_tokens": row.get("total_tokens") or {},
        "duration_s": float(row.get("duration_s") or 0.0),
        "error": row.get("error") or "",
        "fingerprint": row.get("fingerprint", ""),
        "run_id": row.get("run_id", ""),
        # Provenance + defense sandwich (every arm).
        "target_model": row.get("target_model", ""),
        "attacker_model": row.get("attacker_model", ""),
        "target_system_prompt": row.get("target_system_prompt", ""),
        "target_user_turn_suffix": row.get("target_user_turn_suffix", ""),
        # Baseline-only fields. Empty for mesmer; the renderer reads them
        # only when ``arm == "baseline"``.
        "baseline_attack_prompt": row.get("baseline_attack_prompt", ""),
        "baseline_target_response": row.get("baseline_target_response", ""),
        "trace": {
            "modules_called": modules_called,
            "tier_sequence": tier_sequence,
            "winning_module": trace.get("winning_module"),
            "winning_tier": trace.get("winning_tier"),
            "per_module_scores": trace.get("per_module_scores") or {},
            "dead_ends": trace.get("dead_ends") or [],
            "n_llm_calls": trace.get("n_llm_calls", 0),
            "profiler_ran_first": trace.get("profiler_ran_first", False),
            "ladder_monotonic": trace.get("ladder_monotonic", True),
            "events_path": trace.get("events_path", ""),
        },
        "module_tiers": module_tiers,
        # Keep only ``root_id`` + ``nodes`` — drop ``run_id`` duplicate +
        # ``n_nodes`` which the viewer can compute.
        "graph": None if graph_dict is None else {
            "root_id": graph_dict.get("root_id"),
            "nodes": graph_dict.get("nodes") or {},
        },
    }


def _synthesize_baseline_graph(row: dict) -> dict | None:
    """Build a two-node ``root → baseline`` tree from a baseline jsonl row.

    Returns ``None`` when the row carries neither a recorded attack
    prompt nor a target response (early-aborts before send) — the viewer
    keeps its existing "no graph captured" empty-state in that case.

    The synthesized node always uses execution status ``completed``. Score
    mirrors trial outcome so the viewer can still tint high-scoring rows.
    """
    attack = row.get("baseline_attack_prompt") or ""
    response = row.get("baseline_target_response") or ""
    if not attack and not response:
        return None

    success = bool(row.get("success"))
    score = 10 if success else 1
    matched = row.get("matched_text") or ""
    leaked = matched if success else ""
    reflection = (
        f"baseline single-shot attack — {'matched canary' if success else 'no canary match'}"
    )

    root_id = "baseline-root"
    node_id = "baseline-trial"
    return {
        "root_id": root_id,
        "nodes": {
            root_id: {
                "id": root_id,
                "parent_id": None,
                "module": "root",
                "approach": "baseline arm — single-shot replay",
                "messages_sent": [],
                "target_responses": [],
                "score": 0,
                "leaked_info": "",
                "module_output": "",
                "reflection": "",
                "status": "completed",
                "children": [node_id],
                "depth": 0,
                "run_id": "",
                "source": "agent",
            },
            node_id: {
                "id": node_id,
                "parent_id": root_id,
                "module": "baseline",
                "approach": "Replay the dataset's recorded baseline_attack column.",
                "messages_sent": [attack],
                "target_responses": [response],
                "score": score,
                "leaked_info": leaked,
                "module_output": response,
                "reflection": reflection,
                "status": "completed",
                "children": [],
                "depth": 1,
                "run_id": "",
                "source": "agent",
            },
        },
    }


def _size_estimate(trials: list[dict]) -> int:
    """Estimate bytes the inlined JSON payload will contribute.

    Uses the actual ``json.dumps`` length — slightly pessimistic vs the
    real emitted size (we don't escape twice) but cheap and deterministic.
    """
    return len(json.dumps(trials, separators=(",", ":"), default=str))


def _render_template(
    template_src: str,
    *,
    meta: dict,
    trials: list[dict],
    d3_tag: str,
    header_title: str,
    title: str,
) -> str:
    """Substitute the four template placeholders and return full HTML.

    The data blob lives in a ``<script type="application/json">`` so we
    only need to escape ``</script>`` sequences to keep the parser honest.
    """
    payload = {
        "meta": meta,
        "trials": trials,
    }
    data_json = json.dumps(payload, separators=(",", ":"), default=str)
    # Defensive escape — a target reply that contained literal "</script>"
    # would otherwise terminate the JSON block and break the page.
    data_json = data_json.replace("</", "<\\/")

    return (
        template_src
        .replace("{{BENCH_DATA_JSON}}", data_json)
        .replace("{{D3_SCRIPT_TAG}}", d3_tag)
        .replace("{{TITLE}}", _escape_html(title))
        .replace("{{HEADER_TITLE}}", _escape_html(header_title))
    )


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _emit_split(
    *,
    trials_payload: list[dict],
    summary: dict,
    run_dir: Path,
    stem: str,
    template_src: str,
    d3_tag: str,
    header_title: str,
    primary_override: Path | None,
) -> VizResult:
    """Emit one HTML per target + a tiny index page when inlining is unsafe."""
    by_target: dict[str, list[dict]] = {}
    for t in trials_payload:
        by_target.setdefault(t["target"], []).append(t)

    meta = _meta_for(summary)
    target_files: list[tuple[str, Path]] = []
    for target, trials in sorted(by_target.items()):
        target_stem = _safe_stem(target)
        per_target_path = run_dir / f"{stem}-viz-{target_stem}.html"
        html = _render_template(
            template_src,
            meta=meta,
            trials=trials,
            d3_tag=d3_tag,
            header_title=f"{header_title} · {target}",
            title=_page_title(summary, scope=target),
        )
        per_target_path.write_text(html)
        target_files.append((target, per_target_path))

    index_path = (
        primary_override if primary_override is not None
        else run_dir / f"{stem}-viz-index.html"
    )
    index_html = _render_index(summary=summary, target_files=target_files)
    index_path.write_text(index_html)

    return VizResult(primary=index_path, extras=tuple(p for _, p in target_files))


def _render_index(
    *, summary: dict, target_files: list[tuple[str, Path]],
) -> str:
    """Render the tiny index page that links per-target HTML files.

    Plain HTML, no D3, no JS. Purpose is navigation only — the payload is
    too big to inline, so we point at the per-target files that are small
    enough to load.
    """
    meta = _meta_for(summary)
    rows = "\n".join(
        f'<li><a href="{f.name}">{_escape_html(target)}</a></li>'
        for target, f in target_files
    )
    title = _page_title(summary, scope=None)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_escape_html(title)} — index</title>"
        f"<style>body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;"
        f"background:#0f1115;color:#e6e8ee;padding:40px;max-width:640px;margin:auto}}"
        f"h1{{font-size:18px;margin:0 0 4px}}p{{color:#8a93a6;margin:0 0 20px}}"
        f"ul{{padding-left:0;list-style:none}}"
        f"li{{padding:10px 14px;border:1px solid #2a2f3d;border-radius:6px;"
        f"margin-bottom:8px;background:#161922}}"
        f"a{{color:#7aa2f7;text-decoration:none;font-family:ui-monospace,Menlo,monospace}}"
        f"a:hover{{color:#a5c4ff}}</style></head><body>"
        f"<h1>{_escape_html(meta.get('spec') or 'bench')} "
        f"{_escape_html(meta.get('version') or '')}</h1>"
        f"<p>run {_escape_html(meta.get('date') or '')} · "
        f"payload too large to inline — pick a target:</p>"
        f"<ul>{rows}</ul></body></html>"
    )


def _safe_stem(s: str) -> str:
    """Sanitise a target id for use in a filename."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


__all__ = [
    "build_viz_html",
    "VizResult",
    "VIZ_INLINE_BYTES_LIMIT",
    "D3_CDN_URL",
    "TEMPLATE_PATH",
    "D3_ASSET_PATH",
]
