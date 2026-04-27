"""Attack Graph — persistent execution trace for target runs.

Every executed module is a node. Edges mean runtime delegation / sequence, not
search value propagation. Judge scores and reflections are kept as metadata on
the execution record, but search concepts such as frontier, dead end, and
utility ranking belong to the BeliefGraph.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from mesmer.core.constants import NodeSource, NodeStatus


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

@dataclass
class AttackNode:
    """A single node in the attack graph."""

    id: str                                 # unique ID
    parent_id: str | None = None            # None for root
    module: str = ""                        # technique used
    approach: str = ""                      # one-line description of the angle
    messages_sent: list[str] = field(default_factory=list)
    target_responses: list[str] = field(default_factory=list)
    score: int = 0                          # judge score 1-10
    leaked_info: str = ""                   # judge's extract of what was revealed
    # Raw conclude() text from the module that ran for this node. The
    # canonical persisted form of a module's output — subsequent modules
    # (and future runs) read it via AttackGraph.latest_outputs_by_module
    # to see what prior profilers / planners / attacks actually said.
    # Distinct from ``leaked_info`` (the judge's interpretation) —
    # ``module_output`` is verbatim.
    module_output: str = ""
    reflection: str = ""                    # why it worked/failed
    status: str = NodeStatus.PENDING.value
    children: list[str] = field(default_factory=list)
    depth: int = 0
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""
    source: str = NodeSource.AGENT.value
    agent_trace: list[dict] = field(default_factory=list)

    # --- helpers ---

    @property
    def is_failed(self) -> bool:
        return self.status == NodeStatus.FAILED.value

    @property
    def is_pending(self) -> bool:
        return self.status == NodeStatus.PENDING.value

    @property
    def is_completed(self) -> bool:
        return self.status == NodeStatus.COMPLETED.value

    @property
    def is_leader_verdict(self) -> bool:
        """True iff this node is the leader's own execution node (written
        once per run by ``execute_run`` after the leader's ReAct loop).
        Distinguished by source, not by a special module name — the
        leader is a real module whose name comes from ``scenario.module``.
        Consumers filter on this to skip the leader's verdict when
        reasoning about sub-module attack attempts (TAPER trace, frontier
        ranking, winning-module attribution).
        """
        return self.source == NodeSource.LEADER.value

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "module": self.module,
            "approach": self.approach,
            "messages_sent": self.messages_sent,
            "target_responses": self.target_responses,
            "score": self.score,
            "leaked_info": self.leaked_info,
            "module_output": self.module_output,
            "reflection": self.reflection,
            "status": self.status,
            "children": self.children,
            "depth": self.depth,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "source": self.source,
            "agent_trace": self.agent_trace,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AttackNode:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

class AttackGraph:
    """
    The central data structure. A rooted tree of attack attempts.

    Storage is a flat dict[id → node] with parent/child pointers.
    Serialises to/from JSON.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, AttackNode] = {}
        self.root_id: str | None = None
        self.run_counter: int = 0  # incremented each run

    # --- construction ---

    def _make_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def ensure_root(self) -> AttackNode:
        """Create root node if it doesn't exist yet."""
        if self.root_id and self.root_id in self.nodes:
            return self.nodes[self.root_id]
        root = AttackNode(
            id=self._make_id(),
            parent_id=None,
            module="root",
            approach="initial state — no info yet",
            status=NodeStatus.COMPLETED.value,
            depth=0,
        )
        self.root_id = root.id
        self.nodes[root.id] = root
        return root

    def add_node(
        self,
        parent_id: str,
        module: str,
        approach: str,
        *,
        messages_sent: list[str] | None = None,
        target_responses: list[str] | None = None,
        score: int = 0,
        leaked_info: str = "",
        module_output: str = "",
        reflection: str = "",
        status: str = NodeStatus.COMPLETED.value,
        run_id: str = "",
        source: str = NodeSource.AGENT.value,
    ) -> AttackNode:
        """Add an explored node to the graph."""
        parent = self.nodes.get(parent_id)
        depth = (parent.depth + 1) if parent else 1

        node = AttackNode(
            id=self._make_id(),
            parent_id=parent_id,
            module=module,
            approach=approach,
            messages_sent=messages_sent or [],
            target_responses=target_responses or [],
            score=score,
            leaked_info=leaked_info,
            module_output=module_output,
            reflection=reflection,
            status=status,
            depth=depth,
            run_id=run_id,
            source=source,
        )

        self.nodes[node.id] = node
        if parent:
            parent.children.append(node.id)
        return node

    def add_human_hint(
        self,
        hint_text: str,
        parent_id: str | None = None,
        run_id: str = "",
    ) -> AttackNode:
        """Record a human operator hint as an execution-trace note."""
        pid = parent_id or self.root_id
        if pid is None:
            self.ensure_root()
            pid = self.root_id

        return self.add_node(
            parent_id=pid,
            module="human-insight",
            approach=hint_text,
            status=NodeStatus.COMPLETED.value,
            source=NodeSource.HUMAN.value,
            run_id=run_id,
        )

    def mark_failed(self, node_id: str, reason: str = "") -> None:
        node = self.nodes.get(node_id)
        if node:
            node.status = NodeStatus.FAILED.value
            if reason:
                node.reflection = reason

    def append_agent_trace(
        self,
        node_id: str,
        *,
        event: str,
        detail: str = "",
        actor: str = "",
        depth: int = 0,
        iteration: int | None = None,
        payload: dict | None = None,
    ) -> None:
        """Append one ReAct runtime event to an execution node."""
        node = self.nodes.get(node_id)
        if node is None:
            return
        item = {
            "timestamp": time.time(),
            "event": event,
            "detail": detail,
            "actor": actor or node.module,
            "depth": depth,
        }
        if iteration is not None:
            item["iteration"] = iteration
        if payload:
            item["payload"] = payload
        node.agent_trace.append(item)

    # --- queries ---

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, node_id: str) -> AttackNode | None:
        return self.nodes.get(node_id)

    def iter_nodes(self) -> Iterator[AttackNode]:
        return iter(self.nodes.values())

    def get_high_scoring_nodes(self, min_score: int = 7) -> list[AttackNode]:
        """Executed nodes with useful judge scores, sorted by score desc."""
        nodes = [
            n for n in self.get_explored_nodes()
            if n.score >= min_score and not n.is_leader_verdict
        ]
        nodes.sort(key=lambda n: -n.score)
        return nodes

    def get_failed_nodes(self) -> list[AttackNode]:
        return [n for n in self.nodes.values() if n.is_failed]

    def get_explored_nodes(self) -> list[AttackNode]:
        """All non-root execution records."""
        explored_statuses = {
            NodeStatus.COMPLETED,
            NodeStatus.FAILED,
            NodeStatus.BLOCKED,
            NodeStatus.SKIPPED,
        }
        return [
            n for n in self.nodes.values()
            if n.status in {s.value for s in explored_statuses} and n.module != "root"
        ]

    def get_best_score(self) -> int:
        explored = self.get_explored_nodes()
        return max((n.score for n in explored), default=0)

    def get_path(self, node_id: str) -> list[AttackNode]:
        """Walk from node to root, return path root→node."""
        path = []
        current = self.nodes.get(node_id)
        while current:
            path.append(current)
            current = self.nodes.get(current.parent_id) if current.parent_id else None
        path.reverse()
        return path

    # --- statistics ---

    def stats(self) -> dict:
        by_status = {s.value: 0 for s in NodeStatus}
        for n in self.nodes.values():
            by_status[n.status] = by_status.get(n.status, 0) + 1
        return {
            "total": len(self.nodes),
            "by_status": by_status,
            "best_score": self.get_best_score(),
            "depth": max((n.depth for n in self.nodes.values()), default=0),
        }

    # --- formatting for LLM context ---

    def format_summary(
        self,
        max_lines: int = 30,
        *,
        tiers: dict[str, int] | None = None,
    ) -> str:
        """Format graph state for LLM consumption.

        When ``tiers`` is supplied, the "Explored Paths" section is grouped
        by tier so the leader can see at a glance which tiers have been
        probed. Legacy callers (no ``tiers``) get the flat module list.
        """
        lines: list[str] = []

        # Stats
        s = self.stats()
        lines.append(
            f"Attack graph: {s['total']} nodes "
            f"({s['by_status'].get(NodeStatus.FAILED.value, 0)} failed, "
            f"{s['by_status'].get(NodeStatus.COMPLETED.value, 0)} completed, "
            f"{s['by_status'].get(NodeStatus.BLOCKED.value, 0)} blocked) — "
            f"best score: {s['best_score']}/10"
        )
        lines.append("")

        # Group explored by module
        explored = self.get_explored_nodes()
        by_module: dict[str, list[AttackNode]] = {}
        for n in explored:
            by_module.setdefault(n.module, []).append(n)

        if by_module:
            lines.append("## Explored Paths")
            if tiers:
                # Sort by (tier, module name) so the output reads top-down
                # from cheapest to most expensive.
                ordered = sorted(
                    by_module.items(),
                    key=lambda kv: (tiers.get(kv[0], 2), kv[0]),
                )
                for mod, nodes in ordered:
                    best = max(n.score for n in nodes)
                    failed_count = sum(1 for n in nodes if n.is_failed)
                    tier = tiers.get(mod, 2)
                    lines.append(
                        f"- [T{tier}] {mod}: {len(nodes)} attempts, "
                        f"best score {best}, "
                        f"{failed_count} failed"
                    )
            else:
                for mod, nodes in sorted(by_module.items()):
                    best = max(n.score for n in nodes)
                    failed_count = sum(1 for n in nodes if n.is_failed)
                    lines.append(
                        f"- {mod}: {len(nodes)} attempts, "
                        f"best score {best}, "
                        f"{failed_count} failed"
                    )
            lines.append("")

        high_scoring = self.get_high_scoring_nodes()[:5]
        if high_scoring:
            lines.append("## High-Scoring Executions")
            for n in high_scoring:
                lines.append(
                    f"- [{n.module}→{n.approach}] score:{n.score} — {n.leaked_info[:100]}"
                )
            lines.append("")

        failed = self.get_failed_nodes()[:8]
        if failed:
            lines.append("## Failed Executions")
            for n in failed:
                lines.append(f"- ✗ {n.module}→{n.approach}: {n.reflection[:80]}")
            lines.append("")

        return "\n".join(lines[:max_lines])

    # --- Experience queries (graph IS the experience store; no sidecar) ---

    def conversation_history(self) -> list[AttackNode]:
        """Return all sub-module attempts as an ordered timeline
        (oldest first).

        The "conversation history" view over the graph: the same nodes
        ``get_explored_nodes()`` returns, but sorted by timestamp so
        downstream code can reason about WHEN things happened. The
        graph tree preserves parent→child refinement relationships;
        this method preserves temporal order. Same data, different
        read axis.

        Root is excluded (it's a placeholder, not a real module turn).
        Leader-verdict nodes are excluded (they're verdicts, not
        attempts — including them would conflate "the leader's final
        conclude" with "a module's attack attempt" in the timeline).
        Both ``run_id``s are included — cross-run history is the point:
        a second run seeing the target-profiler turn from the first
        run is how mesmer gets smarter over time.
        """
        return sorted(
            (n for n in self.get_explored_nodes() if not n.is_leader_verdict),
            key=lambda n: n.timestamp,
        )

    def render_conversation_history(
        self,
        *,
        last_n: int = 8,
        max_chars_per_turn: int = 1600,
    ) -> str:
        """Compact markdown rendering of the last N module turns.

        Each turn is an entry in the inter-module conversation: which
        module ran, what it was asked to do, what it concluded.
        Ordered oldest-first so the reader sees the chain of reasoning
        in sequence.

        Entries exceeding ``max_chars_per_turn`` are truncated with an
        explicit suffix pointing at graph.json — never silently.
        Turns whose ``module_output`` is empty still render (with the
        approach string as context) so readers see that the module
        ran even if it authored no conclude text.
        """
        turns = self.conversation_history()
        if not turns:
            return ""
        recent = turns[-last_n:] if last_n > 0 else turns
        parts: list[str] = []
        base_index = len(turns) - len(recent)
        for i, n in enumerate(recent, start=base_index + 1):
            output = (n.module_output or "").rstrip()
            if len(output) > max_chars_per_turn:
                head = output[:max_chars_per_turn].rstrip()
                remaining = len(output) - max_chars_per_turn
                output = (
                    f"{head}\n\n[+{remaining} chars — "
                    "see graph.json for the full text]"
                )
            header = f"**{i}. `{n.module}`** (score {n.score}) — {n.approach}"
            if output:
                parts.append(header + "\n" + output)
            else:
                parts.append(header + "\n_(module produced no conclude text)_")
        return "\n\n".join(parts)

    def winning_modules(self, min_score: int = 7) -> list[tuple[str, int]]:
        """Modules that have scored at least ``min_score`` somewhere in the
        graph, sorted by best score descending then module name.

        Returns ``[(module_name, best_score), ...]``. Drives the Planner's
        "these techniques previously worked against THIS target" signal —
        a second run against a familiar target goes straight to what won
        last time instead of re-probing the tier ladder from scratch.
        """
        best: dict[str, int] = {}
        for n in self.iter_nodes():
            if n.module == "root":
                continue
            if n.score < min_score:
                continue
            if n.score > best.get(n.module, -1):
                best[n.module] = n.score
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))

    def failed_modules(self, max_score: int = 2) -> list[str]:
        """Modules where every observed attempt scored at or below
        ``max_score`` AND at least one attempt ran.

        Signals "low-yield modules against THIS target" to the Planner.
        This is a score aggregate, not an AttackGraph lifecycle status.
        """
        by_mod: dict[str, list[int]] = {}
        for n in self.iter_nodes():
            if n.module == "root":
                continue
            by_mod.setdefault(n.module, []).append(n.score)
        return sorted(
            mod for mod, scores in by_mod.items()
            if scores and max(scores) <= max_score
        )

    def verbatim_leaks(self, min_score: int = 5) -> list[str]:
        """Distinct ``leaked_info`` strings from nodes scoring at least
        ``min_score``. Deduplicated by exact string (case-preserving).

        Attack modules read this to reference fragments the target has
        already disclosed — e.g. ``prefix-commitment`` can open with a
        previously-leaked phrase, or ``authority-bias`` can cite
        owner-declared rules the target has admitted.
        """
        seen: set[str] = set()
        out: list[str] = []
        for n in self.iter_nodes():
            if n.module == "root":
                continue
            leaked = (n.leaked_info or "").strip()
            if not leaked or n.score < min_score:
                continue
            if leaked in seen:
                continue
            seen.add(leaked)
            out.append(leaked)
        return out

    def refusal_templates_from_turns(self, min_length: int = 3) -> list[str]:
        """Short target responses that look like refusal templates.

        Heuristic only: strips to stripped-response text, keeps entries of
        length >= ``min_length`` words, dedupes verbatim. Used as input
        when profile.json's ``refusal_templates`` is empty (e.g. the
        target-profiler hasn't run yet) so injection-family modules can
        still mirror observed refusals.
        """
        seen: set[str] = set()
        out: list[str] = []
        for n in self.iter_nodes():
            if n.module == "root":
                continue
            for resp in n.target_responses:
                s = (resp or "").strip()
                if not s:
                    continue
                # Refusals tend to be short. Filter out long responses —
                # they're usually substantive leaks, not template refusals.
                if len(s.split()) < min_length or len(s.split()) > 40:
                    continue
                if s in seen:
                    continue
                seen.add(s)
                out.append(s)
        return out

    def render_learned_experience(self, *, max_entries: int = 8) -> str:
        """Compact markdown summary of everything the graph has taught us.

        Rendered into the leader's user message when non-empty, and
        passed to attack modules via their first user message so they
        can see what's already been tried / what's worked / what
        fragments are available for re-use.

        Intentionally short: this is a PROMPT-LEVEL artifact, not a
        data dump. Long lists are truncated by `max_entries` but NEVER
        silently — the "+N more" suffix tells the reader how much was
        left off so they can pull graph.json for the full history.
        """
        parts: list[str] = []

        wins = self.winning_modules()
        if wins:
            bits = ", ".join(f"`{mod}` (best {s})" for mod, s in wins[:max_entries])
            more = f" (+{len(wins) - max_entries} more)" if len(wins) > max_entries else ""
            parts.append(f"**Modules that worked here before:** {bits}{more}")

        fails = self.failed_modules()
        if fails:
            bits = ", ".join(f"`{m}`" for m in fails[:max_entries])
            more = f" (+{len(fails) - max_entries} more)" if len(fails) > max_entries else ""
            parts.append(f"**Modules that failed here (skip):** {bits}{more}")

        leaks = self.verbatim_leaks()
        if leaks:
            sample = "\n".join(f"  - \"{leak[:120]}\"" for leak in leaks[:max_entries])
            more = (
                f"\n  (+{len(leaks) - max_entries} more in graph.json)"
                if len(leaks) > max_entries else ""
            )
            parts.append("**Verbatim leaks to reference / build on:**\n" + sample + more)

        return "\n\n".join(parts)

    def format_explored_approaches(self) -> str:
        """Compact list of all explored approaches for dedup."""
        explored = self.get_explored_nodes()
        if not explored:
            return "(none yet)"
        return "\n".join(
            f"- [{n.status}] {n.module}→{n.approach} (score:{n.score})"
            for n in explored
        )

    # --- serialization ---

    def to_json(self, indent: int = 2) -> str:
        return json.dumps({
            "root_id": self.root_id,
            "run_counter": self.run_counter,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }, indent=indent, default=str)

    @classmethod
    def from_json(cls, data: str) -> AttackGraph:
        raw = json.loads(data)
        graph = cls()
        graph.root_id = raw.get("root_id")
        graph.run_counter = raw.get("run_counter", 0)
        for nid, nd in raw.get("nodes", {}).items():
            graph.nodes[nid] = AttackNode.from_dict(nd)
        return graph

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())

    @classmethod
    def load(cls, path: Path) -> AttackGraph:
        if path.exists():
            return cls.from_json(path.read_text())
        return cls()


# ---------------------------------------------------------------------------
# Target hashing — same target ⇒ same graph
# ---------------------------------------------------------------------------

def hash_target(adapter: str, url: str = "", model: str = "") -> str:
    """Deterministic hash for a target config. Same target ⇒ same directory."""
    key = f"{adapter}|{url or model}".lower().strip()
    return hashlib.sha256(key.encode()).hexdigest()[:16]
