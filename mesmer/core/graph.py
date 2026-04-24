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


def _apply_tier_gate(
    live: dict[str, dict],
) -> tuple[dict[str, dict], dict]:
    """Implement the "simple before complex" ladder over tiered module stats.

    Input shape — one entry per live module (all_dead modules already dropped):
    ``{"tier": int, "tried": int, "best": int, "all_dead": False}``.

    Picks the lowest tier that still has a **live** candidate and returns only
    that tier's entries. "Live" = untried, OR tried with ``best >=
    PROMISING_SCORE_THRESHOLD`` (worth deepening).

    If no tier is live (every tier is tried-and-unpromising), returns the full
    ``live`` dict — the escape hatch so a stale tier-0 pool doesn't strand a
    promising tier-2 lead. Callers then fall back to cross-tier ranking.

    Returns a tuple ``(filtered_live, decision)``. ``decision`` is a small
    dict that callers can surface through logs:

        {
            "selected_tier": int | None,  # None when escape hatch fired
            "escape_hatch": bool,
            "by_tier": {0: {"live": 2, "dead_or_stale": 1}, 1: ...},
        }

    The decision dict doesn't depend on the final ranking; it only reflects
    the gate's filter logic, which is the piece the trace cares about.
    """
    decision: dict = {
        "selected_tier": None,
        "escape_hatch": False,
        "by_tier": {},
    }
    if not live:
        return live, decision

    # Group module names by tier.
    by_tier: dict[int, list[str]] = {}
    for mod, stats in live.items():
        by_tier.setdefault(stats["tier"], []).append(mod)

    # Census per tier — how many are live (untried or promising) vs stale.
    census: dict[int, dict[str, int]] = {}
    for tier, members in by_tier.items():
        n_live = sum(
            1 for m in members
            if live[m]["tried"] == 0 or live[m]["best"] >= PROMISING_SCORE_THRESHOLD
        )
        census[tier] = {"live": n_live, "dead_or_stale": len(members) - n_live}
    decision["by_tier"] = census

    # Find the lowest tier with a live candidate.
    for tier in sorted(by_tier.keys()):
        if census[tier]["live"] > 0:
            decision["selected_tier"] = tier
            return {m: live[m] for m in by_tier[tier]}, decision

    # Escape hatch: no tier is live → let cross-tier ranking decide.
    decision["escape_hatch"] = True
    return live, decision


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
        module_output: str = "",
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
            module_output=module_output,
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
        module_output: str = "",
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
        node.module_output = module_output
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
        tiers: dict[str, int] | None = None,
        gate_decision_out: dict | None = None,
    ) -> list[dict]:
        """Rank available modules for the next frontier expansion.

        This is the MCTS Selection step made deterministic: the LLM no longer
        picks which technique to try, the graph does. Rules, in priority order:

          1. **Tier gate** — "simple before complex". Find the lowest tier
             that still has a live candidate (untried module, or a tried
             module that's either promising or not yet fully dead). Filter
             candidates to that tier. Callers pass ``tiers`` — a mapping
             ``module_name → tier`` sourced from :meth:`Registry.tiers_for`.
             When ``tiers`` is ``None``, every module is treated as tier-2 and
             the gate collapses to a no-op — legacy callers see unchanged
             behaviour.
          2. **Escape hatch** — if no tier is live (every tier is saturated
             with dead-or-unpromising tried modules), fall back to cross-tier
             ranking so a dead tier-0 pool doesn't strand a promising tier-2
             lead.
          3. Untried modules first within the gated tier — unexplored arms
             have infinite UCB; run them before re-walking anything.
          4. Modules with at least one non-dead attempt, ranked by best score
             (exploit what worked).
          5. Exclude: modules whose every prior attempt is dead in the graph
             (anywhere, not just under this parent). No point retrying a
             technique the target has decisively rebuffed.

        Returns a list of dicts, each with:
          - ``module``: str — the chosen technique
          - ``parent_id``: str — node this frontier will attach under
          - ``rationale``: str — short human-readable reason for telemetry
          - ``best_score``: int — 0 if untried, otherwise the prior best
          - ``tier``: int — the module's attack-cost tier (default 2 when
            the caller didn't supply the mapping).

        The LLM then refines each proposal into a concrete approach one-liner
        (see :func:`mesmer.core.agent.judge.refine_approach`). It cannot re-pick
        modules because it is never shown a menu.
        """
        if not available_modules:
            return []

        attach_to = parent_id or self.root_id
        explored = self.get_explored_nodes()
        tiers = tiers or {}

        per_module: dict[str, dict] = {}
        for mod in available_modules:
            tier = tiers.get(mod, 2)
            nodes = [n for n in explored if n.module == mod]
            if not nodes:
                per_module[mod] = {
                    "tried": 0, "best": 0, "all_dead": False, "tier": tier,
                }
                continue
            per_module[mod] = {
                "tried": len(nodes),
                "best": max(n.score for n in nodes),
                "all_dead": all(n.is_dead for n in nodes),
                "tier": tier,
            }

        # Drop modules whose every prior attempt is dead — that's a hard
        # exclude regardless of tier.
        live = {mod: s for mod, s in per_module.items() if not s["all_dead"]}

        # Tier gate. A tier is "live" if it contains at least one candidate
        # that's either untried OR promising (best >= PROMISING_SCORE_THRESHOLD).
        # A tier whose only live members are tried-and-unpromising is stale —
        # we've probed it and learned nothing worth deepening; the gate should
        # skip to the next tier rather than keep re-walking.
        gated_modules, decision = _apply_tier_gate(live)

        # When the caller supplied an out-param dict, copy the decision into
        # it so the caller can emit a structured trace event. Keeps the
        # return type stable (still ``list[dict]``) for every existing
        # caller that doesn't care about the gate metadata.
        if gate_decision_out is not None:
            gate_decision_out.clear()
            gate_decision_out.update(decision)

        ranked = sorted(
            gated_modules.items(),
            # Priority key: tier ascending, then untried first (tried > 0 is
            # False), then best score descending, then module name for stable
            # ordering.
            key=lambda x: (x[1]["tier"], x[1]["tried"] > 0, -x[1]["best"], x[0]),
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
                "tier": stats["tier"],
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
            if tiers:
                # Sort by (tier, module name) so the output reads top-down
                # from cheapest to most expensive.
                ordered = sorted(
                    by_module.items(),
                    key=lambda kv: (tiers.get(kv[0], 2), kv[0]),
                )
                for mod, nodes in ordered:
                    best = max(n.score for n in nodes)
                    dead_count = sum(1 for n in nodes if n.is_dead)
                    tier = tiers.get(mod, 2)
                    lines.append(
                        f"- [T{tier}] {mod}: {len(nodes)} attempts, "
                        f"best score {best}, "
                        f"{dead_count} dead ends"
                    )
            else:
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

    # --- Experience queries (graph IS the experience store; no sidecar) ---

    def conversation_history(self) -> list[AttackNode]:
        """Return all explored module executions as an ordered timeline
        (oldest first).

        The "conversation history" view over the graph: the same nodes
        ``get_explored_nodes()`` returns, but sorted by timestamp so
        downstream code can reason about WHEN things happened. The
        graph tree preserves parent→child refinement relationships;
        this method preserves temporal order. Same data, different
        read axis.

        Frontier nodes are excluded (they're proposed, not executed).
        Root is excluded (it's a placeholder, not a real module turn).
        Both ``run_id``s are included — cross-run history is the point:
        a second run seeing the target-profiler turn from the first
        run is how mesmer gets smarter over time.
        """
        return sorted(self.get_explored_nodes(), key=lambda n: n.timestamp)

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
            if n.module == "root" or n.status == NodeStatus.FRONTIER.value:
                continue
            if n.score < min_score:
                continue
            if n.score > best.get(n.module, -1):
                best[n.module] = n.score
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))

    def failed_modules(self, max_score: int = 2) -> list[str]:
        """Modules where every observed attempt scored at or below
        ``max_score`` AND at least one attempt ran.

        Signals "don't re-try this against THIS target" to the Planner.
        Distinct from ``get_dead_nodes()`` which tracks individual-node
        failures; this is a per-module aggregate.
        """
        by_mod: dict[str, list[int]] = {}
        for n in self.iter_nodes():
            if n.module == "root" or n.status == NodeStatus.FRONTIER.value:
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
            if n.module == "root" or n.status == NodeStatus.FRONTIER.value:
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
            if n.module == "root" or n.status == NodeStatus.FRONTIER.value:
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
