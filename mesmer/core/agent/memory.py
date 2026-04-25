"""Persistence — file-based memory per target and global stats.

Storage layout:
    ~/.mesmer/
    └── targets/
    │   └── {target-hash}/
    │       ├── graph.json       # the full attack graph (attempt history
    │       │                    # AND canonical source of module outputs —
    │       │                    # every AttackNode carries module_output)
    │       ├── profile.md       # optional free-form human notes
    │       ├── scratchpad.md    # the leader's persistent working notes —
    │       │                    # seeded into ctx.scratchpad[scenario.module]
    │       │                    # at run start; the leader can rewrite it
    │       │                    # via the update_scratchpad tool. Migrated
    │       │                    # from the old plan.md on first init.
    │       ├── chat.jsonl       # append-only operator <> leader chat log
    │       ├── conversation.json  # CONTINUOUS-mode rolling transcript
    │       └── runs/
    │           └── {run-id}.jsonl
    └── global/
        └── techniques.json     # cross-target success rates
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from mesmer.core.agent.context import Turn
from mesmer.core.constants import TurnKind
from mesmer.core.graph import AttackGraph, hash_target

if TYPE_CHECKING:
    from mesmer.core.scenario import TargetConfig


MESMER_HOME = Path.home() / ".mesmer"


def _atomic_write(path: Path, data: str) -> None:
    """Atomic text write: tmpfile in the target's directory, then rename.

    Guarantees readers never see a half-written file even if the writer
    crashes mid-flush (a run that dies during profile synthesis must not
    corrupt the previous profile). Used by :meth:`TargetMemory.save_profile`
    + :meth:`save_target_profile` — anywhere the cost of a torn write is
    the next run mistaking garbage for valid state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


class TargetMemory:
    """File-based persistence for a specific target."""

    def __init__(self, target_config: TargetConfig) -> None:
        self.target_hash = hash_target(
            adapter=target_config.adapter,
            url=target_config.url or target_config.base_url,
            model=target_config.model,
        )
        self.base_dir = MESMER_HOME / "targets" / self.target_hash
        self._migrate_legacy_plan_to_scratchpad()

    def _migrate_legacy_plan_to_scratchpad(self) -> None:
        """One-shot rename of the old `plan.md` to `scratchpad.md`.

        The persistent doc was renamed when we collapsed plan / hints / ideas
        into a single leader-scratchpad concept. Existing targets keep their
        notes — no special migration tooling needed beyond this rename.
        """
        legacy = self.base_dir / "plan.md"
        new = self.base_dir / "scratchpad.md"
        if legacy.exists() and not new.exists():
            try:
                legacy.rename(new)
            except OSError:
                pass  # leave both in place; next save_scratchpad cleans up

    @property
    def graph_path(self) -> Path:
        return self.base_dir / "graph.json"

    @property
    def profile_path(self) -> Path:
        return self.base_dir / "profile.md"

    @property
    def scratchpad_path(self) -> Path:
        """Persisted leader-scratchpad doc.

        Loaded into ``ctx.scratchpad[scenario.module]`` at run start;
        rewritten by the leader's ``update_scratchpad`` tool and by the
        operator via the web UI's leader-chat. No magic — it's just one
        slot of the per-run scratchpad with file-backed persistence.
        """
        return self.base_dir / "scratchpad.md"

    @property
    def chat_path(self) -> Path:
        """Append-only operator <> leader chat log (JSONL, one row per
        message, ``{role, content, timestamp}``)."""
        return self.base_dir / "chat.jsonl"

    @property
    def conversation_path(self) -> Path:
        """C8 — cross-run conversation persistence for continuous mode.

        Stores the rolling ``Turn`` list so that when mesmer is re-invoked
        against the same target with ``scenario_mode == CONTINUOUS`` the
        attacker can pick up where it left off. In TRIALS mode this file is
        never written.
        """
        return self.base_dir / "conversation.json"

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
        """Return the human-readable profile.md, if present.

        The canonical source of module-output truth is ``graph.json``
        (each ``AttackNode.module_output`` carries the running module's
        conclude text). profile.md is an optional free-form note the
        web UI and ``mesmer graph show`` display.
        """
        if self.profile_path.exists():
            return self.profile_path.read_text()
        return None

    def save_profile(self, profile: str) -> None:
        """Atomically write profile.md."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.profile_path, profile)

    def load_scratchpad(self) -> str | None:
        """Load scratchpad.md for this target, if any."""
        if self.scratchpad_path.exists():
            return self.scratchpad_path.read_text()
        return None

    def save_scratchpad(self, content: str) -> None:
        """Save scratchpad.md. Pass empty string to clear."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.scratchpad_path.write_text(content)

    def delete_scratchpad(self) -> None:
        if self.scratchpad_path.exists():
            self.scratchpad_path.unlink()

    # --- Chat log (operator <> leader) -----------------------------------

    def append_chat(self, role: str, content: str, timestamp: float) -> None:
        """Append one chat row to chat.jsonl.

        Role is ``"user"`` or ``"assistant"`` (mirroring OpenAI's chat
        roles). The file is JSONL — one JSON object per line — so reads
        are bounded-cost via tail.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        row = {"role": role, "content": content, "timestamp": timestamp}
        with open(self.chat_path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def load_chat(self, limit: int = 20) -> list[dict]:
        """Return the last ``limit`` chat rows, oldest-first.

        Silently skips malformed rows — a single corrupt line shouldn't
        sink the whole history.
        """
        if not self.chat_path.exists():
            return []
        rows: list[dict] = []
        with open(self.chat_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-limit:] if limit and len(rows) > limit else rows

    def clear_chat(self) -> None:
        if self.chat_path.exists():
            self.chat_path.unlink()

    def save_run_log(self, run_id: str, turns: list[Turn]) -> None:
        runs_dir = self.base_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        with open(runs_dir / f"{run_id}.jsonl", "w") as f:
            for turn in turns:
                f.write(json.dumps(turn.to_dict()) + "\n")

    def save_conversation(self, turns: list[Turn]) -> None:
        """Persist the CONTINUOUS-mode rolling transcript.

        Overwrites whatever was there — mesmer treats this file as the
        canonical "what the target has heard so far" state. The per-run
        ``runs/*.jsonl`` log is separate and append-only; this file is the
        consolidated arc.
        """
        import time as _time
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": _time.time(),
            "turns": [t.to_dict() for t in turns],
        }
        self.conversation_path.write_text(json.dumps(payload, indent=2, default=str))

    def load_conversation(self) -> list[Turn]:
        """Return the persisted rolling transcript, or ``[]`` when absent.

        Silently returns an empty list on parse/schema errors — a corrupt
        conversation file shouldn't kill the run; the attacker just starts
        fresh and the next save overwrites the bad blob.
        """
        p = self.conversation_path
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text())
            items = raw.get("turns", []) if isinstance(raw, dict) else []
        except (json.JSONDecodeError, KeyError, OSError):
            return []
        turns: list[Turn] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                # Turn.__post_init__ coerces the kind string (or missing value
                # from older JSON files) into TurnKind. Unknown kind values
                # raise ValueError — caught below and the row is dropped.
                turns.append(Turn(
                    sent=str(item.get("sent", "") or ""),
                    received=str(item.get("received", "") or ""),
                    module=str(item.get("module", "") or ""),
                    timestamp=float(item.get("timestamp") or 0.0),
                    is_error=bool(item.get("is_error", False)),
                    kind=TurnKind(item.get("kind") or TurnKind.EXCHANGE.value),
                ))
            except (TypeError, ValueError):
                continue
        return turns

    def delete_conversation(self) -> None:
        if self.conversation_path.exists():
            self.conversation_path.unlink()

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
