"""Persistence — file-based memory per target and global stats.

Storage layout:
    ~/.mesmer/
    └── targets/
    │   └── {target-hash}/
    │       ├── graph.json      # the full attack graph
    │       ├── profile.md      # target personality + defenses
    │       └── runs/
    │           └── {run-id}.jsonl
    └── global/
        └── techniques.json     # cross-target success rates
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from mesmer.core.graph import AttackGraph, hash_target

if TYPE_CHECKING:
    from mesmer.core.context import Turn
    from mesmer.core.scenario import TargetConfig


MESMER_HOME = Path.home() / ".mesmer"


class TargetMemory:
    """File-based persistence for a specific target."""

    def __init__(self, target_config: TargetConfig) -> None:
        self.target_hash = hash_target(
            adapter=target_config.adapter,
            url=target_config.url or target_config.base_url,
            model=target_config.model,
        )
        self.base_dir = MESMER_HOME / "targets" / self.target_hash

    @property
    def graph_path(self) -> Path:
        return self.base_dir / "graph.json"

    @property
    def profile_path(self) -> Path:
        return self.base_dir / "profile.md"

    @property
    def plan_path(self) -> Path:
        return self.base_dir / "plan.md"

    def load_graph(self) -> AttackGraph:
        """Load existing graph or return a fresh one."""
        if self.graph_path.exists():
            try:
                return AttackGraph.from_json(self.graph_path.read_text())
            except (json.JSONDecodeError, KeyError):
                pass
        return AttackGraph()

    def save_graph(self, graph: AttackGraph) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(graph.to_json())

    def load_profile(self) -> str | None:
        if self.profile_path.exists():
            return self.profile_path.read_text()
        return None

    def save_profile(self, profile: str) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(profile)

    def load_plan(self) -> str | None:
        """Load the plan.md for this target, if any."""
        if self.plan_path.exists():
            return self.plan_path.read_text()
        return None

    def save_plan(self, plan: str) -> None:
        """Save plan.md. Pass empty string to clear."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(plan)

    def delete_plan(self) -> None:
        if self.plan_path.exists():
            self.plan_path.unlink()

    def save_run_log(self, run_id: str, turns: list[Turn]) -> None:
        runs_dir = self.base_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        with open(runs_dir / f"{run_id}.jsonl", "w") as f:
            for turn in turns:
                f.write(json.dumps(turn.to_dict()) + "\n")

    def list_runs(self) -> list[str]:
        """List run IDs, newest first."""
        runs_dir = self.base_dir / "runs"
        if not runs_dir.exists():
            return []
        files = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [f.stem for f in files]

    def exists(self) -> bool:
        return self.graph_path.exists()


class GlobalMemory:
    """Cross-target technique effectiveness tracking."""

    base_dir = MESMER_HOME / "global"

    @classmethod
    def stats_path(cls) -> Path:
        return cls.base_dir / "techniques.json"

    @classmethod
    def load_stats(cls) -> dict:
        p = cls.stats_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (json.JSONDecodeError, KeyError):
                pass
        return {}

    @classmethod
    def save_stats(cls, stats: dict) -> None:
        cls.base_dir.mkdir(parents=True, exist_ok=True)
        cls.stats_path().write_text(json.dumps(stats, indent=2))

    @classmethod
    def update_from_graph(cls, graph: AttackGraph) -> None:
        """Aggregate technique scores from a graph into global stats."""
        stats = cls.load_stats()

        for node in graph.get_explored_nodes():
            mod = node.module
            if not mod or mod == "root":
                continue
            if mod not in stats:
                stats[mod] = {"attempts": 0, "total_score": 0, "best_score": 0, "avg_score": 0.0}
            stats[mod]["attempts"] += 1
            stats[mod]["total_score"] += node.score
            stats[mod]["best_score"] = max(stats[mod]["best_score"], node.score)
            if stats[mod]["attempts"] > 0:
                stats[mod]["avg_score"] = round(
                    stats[mod]["total_score"] / stats[mod]["attempts"], 1
                )

        cls.save_stats(stats)

    @classmethod
    def format_stats(cls) -> str:
        """Format global stats for LLM consumption or CLI display."""
        stats = cls.load_stats()
        if not stats:
            return "(no global stats yet)"

        lines = ["## Global Technique Stats (across all targets)"]
        # Sort by avg_score desc
        for mod, s in sorted(stats.items(), key=lambda x: -x[1].get("avg_score", 0)):
            lines.append(
                f"- {mod}: {s['attempts']} attempts, "
                f"avg score {s['avg_score']}, "
                f"best {s['best_score']}"
            )
        return "\n".join(lines)


def generate_run_id() -> str:
    """Generate a unique run ID."""
    return uuid.uuid4().hex[:8]
