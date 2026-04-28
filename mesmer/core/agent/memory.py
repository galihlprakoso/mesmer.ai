"""Persistence — file-based memory per target and global stats.

Storage layout:
    ~/.mesmer/
    └── targets/
    │   └── {target-hash}/
    │       ├── graph.json       # the full attack graph (attempt history
    │       │                    # AND canonical source of module outputs —
    │       │                    # every AttackNode carries module_output)
    │       ├── profile.md       # optional free-form human notes
    │       ├── artifacts/       # durable Markdown artifact documents
    │       ├── chat.jsonl       # append-only operator <> leader chat log
    │       ├── conversation.json  # CONTINUOUS-mode rolling transcript
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

from mesmer.core.agent.context import Turn
from mesmer.core.artifacts import ArtifactStore
from mesmer.core.belief_graph import BeliefGraph
from mesmer.core.constants import TurnKind
from mesmer.core.graph import AttackGraph, hash_target
from mesmer.core.persistence import (
    FileStorageProvider,
    StorageProvider,
    join_storage_key,
    workspace_prefix,
)

if TYPE_CHECKING:
    from mesmer.core.scenario import TargetConfig


MESMER_HOME = Path.home() / ".mesmer"


class TargetMemory:
    """Persistence for a specific target.

    The default backend is local files. Callers may inject another
    :class:`StorageProvider` and a non-``local`` workspace id when Mesmer grows
    into hosted/team deployments.
    """

    def __init__(
        self,
        target_config: TargetConfig,
        *,
        storage: StorageProvider | None = None,
        workspace_id: str = "local",
    ) -> None:
        self.target_hash = hash_target(
            adapter=target_config.adapter,
            url=target_config.url or target_config.base_url,
            model=target_config.model,
        )
        self.storage = storage or FileStorageProvider(MESMER_HOME)
        self.workspace_id = workspace_id or "local"
        self._prefix = join_storage_key(
            workspace_prefix(self.workspace_id), "targets", self.target_hash
        )
        self.base_dir = self._path_for_prefix()

    @classmethod
    def from_target_hash(
        cls,
        target_hash: str,
        *,
        storage: StorageProvider | None = None,
        workspace_id: str = "local",
    ) -> "TargetMemory":
        """Build memory around an already-known target hash.

        Useful for web routes that address persisted target state directly and
        do not have a full ``TargetConfig`` in hand.
        """
        obj = cls.__new__(cls)
        obj.target_hash = str(target_hash)
        obj.storage = storage or FileStorageProvider(MESMER_HOME)
        obj.workspace_id = workspace_id or "local"
        obj._prefix = join_storage_key(
            workspace_prefix(obj.workspace_id), "targets", obj.target_hash
        )
        obj.base_dir = obj._path_for_prefix()
        return obj

    def _key(self, name: str) -> str:
        return join_storage_key(self._prefix, name)

    def _path_for_prefix(self) -> Path:
        if isinstance(self.storage, FileStorageProvider):
            return self.storage.resolve(self._prefix)
        return Path(self._prefix)

    def _path_for(self, name: str) -> Path:
        if isinstance(self.storage, FileStorageProvider):
            return self.storage.resolve(self._key(name))
        return Path(self._key(name))

    @property
    def graph_path(self) -> Path:
        return self._path_for("graph.json")

    @property
    def profile_path(self) -> Path:
        return self._path_for("profile.md")

    @property
    def artifacts_dir(self) -> Path:
        """Directory of durable Markdown artifact documents."""
        return self._path_for("artifacts")

    @property
    def chat_path(self) -> Path:
        """Append-only operator <> leader chat log (JSONL, one row per
        message, ``{role, content, timestamp}``)."""
        return self._path_for("chat.jsonl")

    @property
    def conversation_path(self) -> Path:
        """C8 — cross-run conversation persistence for continuous mode.

        Stores the rolling ``Turn`` list so that when mesmer is re-invoked
        against the same target with ``scenario_mode == CONTINUOUS`` the
        attacker can pick up where it left off. In TRIALS mode this file is
        never written.
        """
        return self._path_for("conversation.json")

    def load_graph(self) -> AttackGraph:
        """Load existing graph or return a fresh one."""
        key = self._key("graph.json")
        if self.storage.exists(key):
            try:
                return AttackGraph.from_json(self.storage.read_text(key))
            except (json.JSONDecodeError, KeyError):
                pass
        return AttackGraph()

    def save_graph(self, graph: AttackGraph) -> None:
        self.storage.write_text(self._key("graph.json"), graph.to_json())

    # --- Belief Attack Graph (Session 2 wiring) ---

    @property
    def belief_graph_path(self) -> Path:
        """Belief graph snapshot — typed planner state.

        Sibling of ``graph_path``; AttackGraph keeps its own execution-trace
        file. Both load/save independently so the belief graph can be
        deleted (or rebuilt from the delta log) without touching the
        execution history.
        """
        return self._path_for("belief_graph.json")

    @property
    def belief_deltas_path(self) -> Path:
        """Belief graph append-only delta log.

        Each line is one ``GraphDelta`` serialised to JSON. Replayed
        via :meth:`BeliefGraph.replay` if the snapshot ever corrupts.
        """
        return self._path_for("belief_deltas.jsonl")

    def load_belief_graph(self) -> BeliefGraph:
        """Load the persisted belief graph or return a fresh one.

        Falls back to delta-log replay when the snapshot is missing or
        unparseable but the log exists. Returns an empty
        :class:`BeliefGraph` (just the singleton TargetNode) when
        neither file exists — fresh runs against a new target.
        """
        snapshot_key = self._key("belief_graph.json")
        deltas_key = self._key("belief_deltas.jsonl")
        if self.storage.exists(snapshot_key):
            try:
                return BeliefGraph.from_json(self.storage.read_text(snapshot_key))
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
        if self.storage.exists(deltas_key):
            try:
                return BeliefGraph.replay_jsonl(
                    self.storage.read_text(deltas_key),
                    target_hash=self.target_hash,
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
        return BeliefGraph(target_hash=self.target_hash)

    def save_belief_graph(self, graph: BeliefGraph) -> None:
        """Write snapshot + append unsaved deltas to the JSONL log.

        The storage provider owns the physical write. After appending deltas,
        the in-memory delta queue is cleared so calling this twice in a row is
        a no-op for the JSONL file.
        """
        self.storage.write_text(self._key("belief_graph.json"), graph.to_json())
        if graph.deltas:
            rows = "".join(
                json.dumps(delta.to_dict(), sort_keys=True, default=str) + "\n"
                for delta in graph.deltas
            )
            self.storage.append_text(self._key("belief_deltas.jsonl"), rows)
            graph.deltas = []

    def delete_belief_graph(self) -> None:
        """Wipe both the snapshot and the delta log.

        Used by the runner when ``--fresh`` is set so a clean run
        starts without prior beliefs. The legacy ``--fresh`` already
        bypasses the AttackGraph load; this gives the belief graph
        the same affordance.
        """
        for key in (self._key("belief_graph.json"), self._key("belief_deltas.jsonl")):
            self.storage.delete(key, missing_ok=True)

    def has_belief_graph(self) -> bool:
        return self.storage.exists(self._key("belief_graph.json")) or self.storage.exists(
            self._key("belief_deltas.jsonl")
        )

    def load_profile(self) -> str | None:
        """Return the human-readable profile.md, if present.

        The canonical source of module-output truth is ``graph.json``
        (each ``AttackNode.module_output`` carries the running module's
        conclude text). profile.md is an optional free-form note the
        web UI and ``mesmer graph show`` display.
        """
        key = self._key("profile.md")
        if self.storage.exists(key):
            return self.storage.read_text(key)
        return None

    def save_profile(self, profile: str) -> None:
        """Atomically write profile.md."""
        self.storage.write_text(self._key("profile.md"), profile, atomic=True)

    def load_artifacts(self) -> ArtifactStore:
        return ArtifactStore.from_storage(self.storage, self._key("artifacts"))

    def save_artifacts(self, artifacts: ArtifactStore) -> None:
        artifacts.to_storage(self.storage, self._key("artifacts"))

    # --- Chat log (operator <> leader) -----------------------------------

    def append_chat(self, role: str, content: str, timestamp: float) -> None:
        """Append one chat row to chat.jsonl.

        Role is ``"user"`` or ``"assistant"`` (mirroring OpenAI's chat
        roles). The file is JSONL — one JSON object per line — so reads
        are bounded-cost via tail.
        """
        row = {"role": role, "content": content, "timestamp": timestamp}
        self.storage.append_text(self._key("chat.jsonl"), json.dumps(row) + "\n")

    def load_chat(self, limit: int = 20) -> list[dict]:
        """Return the last ``limit`` chat rows, oldest-first.

        Silently skips malformed rows — a single corrupt line shouldn't
        sink the whole history.
        """
        key = self._key("chat.jsonl")
        if not self.storage.exists(key):
            return []
        rows: list[dict] = []
        for line in self.storage.read_text(key).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:] if limit and len(rows) > limit else rows

    def clear_chat(self) -> None:
        self.storage.delete(self._key("chat.jsonl"), missing_ok=True)

    def save_run_log(self, run_id: str, turns: list[Turn]) -> None:
        body = "".join(json.dumps(turn.to_dict()) + "\n" for turn in turns)
        self.storage.write_text(self._key(f"runs/{run_id}.jsonl"), body)

    def save_conversation(self, turns: list[Turn]) -> None:
        """Persist the CONTINUOUS-mode rolling transcript.

        Overwrites whatever was there — mesmer treats this file as the
        canonical "what the target has heard so far" state. The per-run
        ``runs/*.jsonl`` log is separate and append-only; this file is the
        consolidated arc.
        """
        import time as _time

        payload = {
            "saved_at": _time.time(),
            "turns": [t.to_dict() for t in turns],
        }
        self.storage.write_text(
            self._key("conversation.json"),
            json.dumps(payload, indent=2, default=str),
        )

    def load_conversation(self) -> list[Turn]:
        """Return the persisted rolling transcript, or ``[]`` when absent.

        Silently returns an empty list on parse/schema errors — a corrupt
        conversation file shouldn't kill the run; the attacker just starts
        fresh and the next save overwrites the bad blob.
        """
        key = self._key("conversation.json")
        if not self.storage.exists(key):
            return []
        try:
            raw = json.loads(self.storage.read_text(key))
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
                turns.append(
                    Turn(
                        sent=str(item.get("sent", "") or ""),
                        received=str(item.get("received", "") or ""),
                        module=str(item.get("module", "") or ""),
                        timestamp=float(item.get("timestamp") or 0.0),
                        is_error=bool(item.get("is_error", False)),
                        kind=TurnKind(item.get("kind") or TurnKind.EXCHANGE.value),
                    )
                )
            except (TypeError, ValueError):
                continue
        return turns

    def delete_conversation(self) -> None:
        self.storage.delete(self._key("conversation.json"), missing_ok=True)

    def list_runs(self) -> list[str]:
        """List run IDs, newest first."""
        keys = self.storage.list_files(self._key("runs"), suffix=".jsonl")
        keys = sorted(keys, key=self.storage.modified_at, reverse=True)
        return [Path(key).stem for key in keys]

    def exists(self) -> bool:
        return self.storage.exists(self._key("graph.json"))


class GlobalMemory:
    """Cross-target technique effectiveness tracking."""

    base_dir = MESMER_HOME / "global"
    storage_provider: StorageProvider | None = None
    workspace_id = "local"

    @classmethod
    def stats_path(cls) -> Path:
        return cls.base_dir / "techniques.json"

    @classmethod
    def _storage(cls) -> StorageProvider:
        return cls.storage_provider or FileStorageProvider(cls.base_dir.parent)

    @classmethod
    def _prefix(cls) -> str:
        return join_storage_key(workspace_prefix(cls.workspace_id), cls.base_dir.name)

    @classmethod
    def _stats_key(cls) -> str:
        return join_storage_key(cls._prefix(), "techniques.json")

    @classmethod
    def load_stats(cls) -> dict:
        storage = cls._storage()
        key = cls._stats_key()
        if storage.exists(key):
            try:
                return json.loads(storage.read_text(key))
            except (json.JSONDecodeError, KeyError):
                pass
        return {}

    @classmethod
    def save_stats(cls, stats: dict) -> None:
        cls._storage().write_text(cls._stats_key(), json.dumps(stats, indent=2))

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
