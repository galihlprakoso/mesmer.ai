"""Attack Graph — MCTS-inspired search structure for LLM red-teaming.

Every attack attempt is a node. Dead ends are remembered. Promising leads
are deepened. The agent never re-walks an explored path.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


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
    leaked_info: str = ""                   # what was extracted
    reflection: str = ""                    # why it worked/failed
    status: str = "frontier"                # frontier | alive | promising | dead
    children: list[str] = field(default_factory=list)
    depth: int = 0
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""
    source: str = "agent"                   # agent | human | judge

    # --- helpers ---

    @property
    def is_dead(self) -> bool:
        return self.status == "dead"

    @property
    def is_frontier(self) -> bool:
        return self.status == "frontier"

    @property
    def is_promising(self) -> bool:
        return self.status == "promising"

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
            "reflection": self.reflection,
            "status": self.status,
            "children": self.children,
            "depth": self.depth,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "source": self.source,
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
            status="alive",
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
        reflection: str = "",
        status: str = "alive",
        run_id: str = "",
        source: str = "agent",
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
            reflection=reflection,
            status=status,
            depth=depth,
            run_id=run_id,
            source=source,
        )

        # Auto-classify
        if status == "alive":
            if score >= 5:
                node.status = "promising"
            elif score <= 2 and reflection:
                node.status = "dead"

        self.nodes[node.id] = node
        if parent:
            parent.children.append(node.id)
        return node

    def add_frontier_node(
        self,
        parent_id: str,
        module: str,
        approach: str,
        *,
        source: str = "agent",
        run_id: str = "",
    ) -> AttackNode:
        """Add an unexplored frontier node — a suggested next move."""
        parent = self.nodes.get(parent_id)
        depth = (parent.depth + 1) if parent else 1

        node = AttackNode(
            id=self._make_id(),
            parent_id=parent_id,
            module=module,
            approach=approach,
            status="frontier",
            depth=depth,
            source=source,
            run_id=run_id,
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
        """Add a human insight as a high-priority frontier node."""
        pid = parent_id or self.root_id
        if pid is None:
            self.ensure_root()
            pid = self.root_id

        return self.add_frontier_node(
            parent_id=pid,
            module="human-insight",
            approach=hint_text,
            source="human",
            run_id=run_id,
        )

    def mark_dead(self, node_id: str, reason: str = "") -> None:
        node = self.nodes.get(node_id)
        if node:
            node.status = "dead"
            if reason:
                node.reflection = reason

    def promote_frontier(self, node_id: str) -> AttackNode | None:
        """Mark a frontier node as being explored (alive)."""
        node = self.nodes.get(node_id)
        if node and node.is_frontier:
            node.status = "alive"
            return node
        return None

    def fulfill_frontier(
        self,
        node_id: str,
        *,
        approach: str,
        messages_sent: list[str],
        target_responses: list[str],
        score: int,
        leaked_info: str = "",
        reflection: str = "",
        run_id: str = "",
        module: str | None = None,
    ) -> AttackNode | None:
        """Fill a frontier node with actual attempt results and flip its
        status based on score (dead / alive / promising).

        Used when the leader executes a specific frontier suggestion —
        preserves the parent-child refinement edge instead of creating a
        disconnected sibling of root. TAP-aligned: parent edge means
        "child was proposed by reflecting on parent's result."

        If `module` is provided, overrides the stored module name to match
        the sub-module the leader actually called. This fixes the mismatch
        where a frontier's stored `module` (from a prior reflection or
        persisted run) differs from what the leader executed.

        Returns None if the node is missing or is not in frontier status.
        """
        node = self.nodes.get(node_id)
        if node is None or not node.is_frontier:
            return None

        node.approach = approach or node.approach
        node.messages_sent = messages_sent
        node.target_responses = target_responses
        node.score = score
        node.leaked_info = leaked_info
        node.reflection = reflection
        node.run_id = run_id or node.run_id
        node.timestamp = time.time()
        if module:
            node.module = module

        if score <= 2 and reflection:
            node.status = "dead"
        elif score >= 5:
            node.status = "promising"
        else:
            node.status = "alive"
        return node

    def edit_approach(self, node_id: str, new_approach: str) -> AttackNode | None:
        """Update the approach text of a node (typically a frontier)."""
        node = self.nodes.get(node_id)
        if node:
            node.approach = new_approach
            return node
        return None

    # --- queries ---

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, node_id: str) -> AttackNode | None:
        return self.nodes.get(node_id)

    def iter_nodes(self) -> Iterator[AttackNode]:
        return iter(self.nodes.values())

    def get_frontier_nodes(self, limit: int = 20) -> list[AttackNode]:
        """Frontier nodes, sorted: human-source first, then by parent score desc."""
        frontier = [n for n in self.nodes.values() if n.is_frontier]

        def sort_key(n: AttackNode):
            # human hints get top priority (0), agent gets 1
            source_rank = 0 if n.source == "human" else 1
            # higher parent score = better
            parent = self.nodes.get(n.parent_id) if n.parent_id else None
            parent_score = -(parent.score if parent else 0)
            return (source_rank, parent_score, -n.timestamp)

        frontier.sort(key=sort_key)
        return frontier[:limit]

    def get_promising_nodes(self) -> list[AttackNode]:
        """Nodes with score >= 5, sorted by score desc."""
        promising = [n for n in self.nodes.values() if n.is_promising]
        promising.sort(key=lambda n: -n.score)
        return promising

    def get_dead_nodes(self) -> list[AttackNode]:
        return [n for n in self.nodes.values() if n.is_dead]

    def get_explored_nodes(self) -> list[AttackNode]:
        """All non-frontier, non-root nodes."""
        return [
            n for n in self.nodes.values()
            if n.status in ("alive", "promising", "dead") and n.module != "root"
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
        by_status = {"frontier": 0, "alive": 0, "promising": 0, "dead": 0}
        for n in self.nodes.values():
            by_status[n.status] = by_status.get(n.status, 0) + 1
        return {
            "total": len(self.nodes),
            "by_status": by_status,
            "best_score": self.get_best_score(),
            "depth": max((n.depth for n in self.nodes.values()), default=0),
        }

    # --- formatting for LLM context ---

    def format_summary(self, max_lines: int = 30) -> str:
        """Format graph state for LLM consumption."""
        lines: list[str] = []

        # Stats
        s = self.stats()
        lines.append(
            f"Attack graph: {s['total']} nodes "
            f"({s['by_status'].get('dead', 0)} dead, "
            f"{s['by_status'].get('promising', 0)} promising, "
            f"{s['by_status'].get('frontier', 0)} frontier) — "
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
            for mod, nodes in sorted(by_module.items()):
                best = max(n.score for n in nodes)
                dead_count = sum(1 for n in nodes if n.is_dead)
                lines.append(
                    f"- {mod}: {len(nodes)} attempts, "
                    f"best score {best}, "
                    f"{dead_count} dead ends"
                )
            lines.append("")

        # Best leads
        promising = self.get_promising_nodes()[:5]
        if promising:
            lines.append("## Best Leads")
            for n in promising:
                lines.append(
                    f"- [{n.module}→{n.approach}] score:{n.score} — {n.leaked_info[:100]}"
                )
            lines.append("")

        # Dead ends
        dead = self.get_dead_nodes()[:8]
        if dead:
            lines.append("## Dead Ends (do NOT retry)")
            for n in dead:
                lines.append(f"- ✗ {n.module}→{n.approach}: {n.reflection[:80]}")
            lines.append("")

        # Frontier
        frontier = self.get_frontier_nodes(limit=5)
        if frontier:
            lines.append("## Frontier (suggested next moves — pass frontier_id to execute)")
            for n in frontier:
                parent = self.nodes.get(n.parent_id) if n.parent_id else None
                parent_info = f"parent score:{parent.score}" if parent else "root"
                source_tag = " ★ HUMAN" if n.source == "human" else ""
                lines.append(
                    f"- [{n.id}] {n.module}: {n.approach} ({parent_info}){source_tag}"
                )
            lines.append("")

        return "\n".join(lines[:max_lines])

    def format_dead_ends(self) -> str:
        """Compact list of dead ends for anti-repetition injection."""
        dead = self.get_dead_nodes()
        if not dead:
            return "(none yet)"
        return "\n".join(
            f"- {n.module}→{n.approach}: {n.reflection[:100]}"
            for n in dead
        )

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
