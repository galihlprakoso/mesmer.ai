"""Attack Graph — MCTS-inspired search structure for LLM red-teaming.

Every attack attempt is a node. Dead ends are remembered. Promising leads
are deepened. The agent never re-walks an explored path.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from mesmer.core.constants import (
    DEAD_SCORE_THRESHOLD,
    MIN_TOKENS_FOR_SIMILARITY,
    NodeSource,
    NodeStatus,
    PROMISING_SCORE_THRESHOLD,
    SIMILAR_APPROACH_THRESHOLD,
)


def _approach_tokens(text: str) -> set[str]:
    """Tokenise an approach description for similarity comparison.

    Keeps words of length >= 4 after lowercasing and stripping punctuation.
    Short function words are dropped so they don't dominate the Jaccard
    intersection.
    """
    if not text:
        return set()
    t = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return {w for w in t.split() if len(w) >= 4}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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
    status: str = NodeStatus.FRONTIER.value
    children: list[str] = field(default_factory=list)
    depth: int = 0
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""
    source: str = NodeSource.AGENT.value

    # --- helpers ---

    @property
    def is_dead(self) -> bool:
        return self.status == NodeStatus.DEAD

    @property
    def is_frontier(self) -> bool:
        return self.status == NodeStatus.FRONTIER

    @property
    def is_promising(self) -> bool:
        return self.status == NodeStatus.PROMISING

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
            status=NodeStatus.ALIVE.value,
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
        status: str = NodeStatus.ALIVE.value,
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
            reflection=reflection,
            status=status,
            depth=depth,
            run_id=run_id,
            source=source,
        )

        # Auto-classify
        if status == NodeStatus.ALIVE:
            self._auto_classify(node)

        self.nodes[node.id] = node
        if parent:
            parent.children.append(node.id)
        return node

    def _auto_classify(self, node: AttackNode) -> None:
        """Set status on an un-stored node based on its score and the graph.

        Order matters: the same-module-no-gain check takes precedence over
        the promising threshold. An approach that's been tried twice with
        the same result is dead even if score is 5 — "promising but stuck"
        is a worse frontier than "fresh and untested".
        """
        score = node.score

        # 1. Dead from score alone.
        if score <= DEAD_SCORE_THRESHOLD and node.reflection:
            node.status = NodeStatus.DEAD.value
            return

        # 2. Dead from same-module-no-gain: this approach was already
        #    explored under the same module and didn't improve on that
        #    attempt's score. Keeps the tree from re-walking.
        prior_score = self._best_similar_score(node)
        if prior_score is not None and score <= prior_score:
            node.status = NodeStatus.DEAD.value
            if not node.reflection:
                node.reflection = (
                    f"same-module-no-gain: {node.module} already scored "
                    f"{prior_score} on a similar approach"
                )
            return

        # 3. Promising by score.
        if score >= PROMISING_SCORE_THRESHOLD:
            node.status = NodeStatus.PROMISING.value

    def _best_similar_score(self, node: AttackNode) -> int | None:
        """Highest score seen on a prior node with the same module AND a
        sufficiently-similar approach string. Returns None if no match.
        """
        tokens = _approach_tokens(node.approach)
        if len(tokens) < MIN_TOKENS_FOR_SIMILARITY:
            return None

        best: int | None = None
        for other in self.nodes.values():
            if other.id == node.id:
                continue
            if other.module != node.module:
                continue
            if other.status == NodeStatus.FRONTIER:
                continue  # frontiers are unexplored, can't compare scores
            if other.module == "root":
                continue
            other_tokens = _approach_tokens(other.approach)
            if len(other_tokens) < MIN_TOKENS_FOR_SIMILARITY:
                continue
            if _jaccard(tokens, other_tokens) < SIMILAR_APPROACH_THRESHOLD:
                continue
            if best is None or other.score > best:
                best = other.score
        return best

    def add_frontier_node(
        self,
        parent_id: str,
        module: str,
        approach: str,
        *,
        source: str = NodeSource.AGENT.value,
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
            status=NodeStatus.FRONTIER.value,
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
            source=NodeSource.HUMAN.value,
            run_id=run_id,
        )

    def mark_dead(self, node_id: str, reason: str = "") -> None:
        node = self.nodes.get(node_id)
        if node:
            node.status = NodeStatus.DEAD.value
            if reason:
                node.reflection = reason

    def promote_frontier(self, node_id: str) -> AttackNode | None:
        """Mark a frontier node as being explored (alive)."""
        node = self.nodes.get(node_id)
        if node and node.is_frontier:
            node.status = NodeStatus.ALIVE.value
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

        # Route through the shared classifier. Start from alive — the helper
        # escalates to promising or dead based on score and graph context.
        node.status = NodeStatus.ALIVE.value
        self._auto_classify(node)
        return node

    # --- frontier proposal (P2, MCTS Selection phase) ---

    def propose_frontier(
        self,
        available_modules: list[str],
        *,
        parent_id: str | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        """Rank available modules for the next frontier expansion.

        This is the MCTS Selection step made deterministic: the LLM no longer
        picks which technique to try, the graph does. Rules, in priority order:

          1. Untried modules first — unexplored arms have infinite UCB; run
             them before re-walking anything.
          2. Modules with at least one non-dead attempt, ranked by best score
             (exploit what worked).
          3. Exclude: modules whose every prior attempt is dead in the graph
             (anywhere, not just under this parent). No point retrying a
             technique the target has decisively rebuffed.

        Returns a list of dicts, each with:
          - ``module``: str — the chosen technique
          - ``parent_id``: str — node this frontier will attach under
          - ``rationale``: str — short human-readable reason for telemetry
          - ``best_score``: int — 0 if untried, otherwise the prior best

        The LLM then refines each proposal into a concrete approach one-liner
        (see :func:`mesmer.core.judge.refine_approach`). It cannot re-pick
        modules because it is never shown a menu.
        """
        if not available_modules:
            return []

        attach_to = parent_id or self.root_id
        explored = self.get_explored_nodes()

        per_module: dict[str, dict] = {}
        for mod in available_modules:
            nodes = [n for n in explored if n.module == mod]
            if not nodes:
                per_module[mod] = {"tried": 0, "best": 0, "all_dead": False}
                continue
            per_module[mod] = {
                "tried": len(nodes),
                "best": max(n.score for n in nodes),
                "all_dead": all(n.is_dead for n in nodes),
            }

        # Filter dead-out modules and rank the rest.
        ranked = sorted(
            ((mod, s) for mod, s in per_module.items() if not s["all_dead"]),
            # Priority key: untried first (tried > 0 is False), then best score
            # descending, then module name for stable ordering.
            key=lambda x: (x[1]["tried"] > 0, -x[1]["best"], x[0]),
        )

        results: list[dict] = []
        for mod, stats in ranked[:top_k]:
            if stats["tried"] == 0:
                rationale = "untried — explore new arm"
            else:
                rationale = f"deepen {mod} — prior best score {stats['best']}"
            results.append({
                "module": mod,
                "parent_id": attach_to,
                "rationale": rationale,
                "best_score": stats["best"],
            })
        return results

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
            source_rank = 0 if n.source == NodeSource.HUMAN else 1
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
        explored_statuses = {
            NodeStatus.ALIVE,
            NodeStatus.PROMISING,
            NodeStatus.DEAD,
        }
        return [
            n for n in self.nodes.values()
            if n.status in explored_statuses and n.module != "root"
        ]

    def get_best_score(self) -> int:
        explored = self.get_explored_nodes()
        return max((n.score for n in explored), default=0)

    def latest_explored_node(self) -> AttackNode | None:
        """The most recently-recorded non-root explored node.

        Used by CONTINUOUS-mode attach-point resolution: in a one-chat arc
        each new move is a continuation of the previous one, so a "fresh"
        attempt (no ``frontier_id``) should still dangle under the latest
        explored node, not root. Returns None when the graph has no
        explored attempts yet — callers should fall back to root.
        """
        explored = self.get_explored_nodes()
        if not explored:
            return None
        return max(explored, key=lambda n: n.timestamp)

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

    def format_summary(self, max_lines: int = 30) -> str:
        """Format graph state for LLM consumption."""
        lines: list[str] = []

        # Stats
        s = self.stats()
        lines.append(
            f"Attack graph: {s['total']} nodes "
            f"({s['by_status'].get(NodeStatus.DEAD.value, 0)} dead, "
            f"{s['by_status'].get(NodeStatus.PROMISING.value, 0)} promising, "
            f"{s['by_status'].get(NodeStatus.FRONTIER.value, 0)} frontier) — "
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
                source_tag = " ★ HUMAN" if n.source == NodeSource.HUMAN else ""
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
