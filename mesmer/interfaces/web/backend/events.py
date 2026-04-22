"""EventBus — bridges the LogFn callback to WebSocket clients.

The loop's LogFn type is Callable[[str, str], None].
EventBus.log_fn matches this signature exactly, so it drops in
as a replacement for the CLI's verbose_log callback.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mesmer.core.graph import AttackGraph


class EventBus:
    """Fan-out event bus: one log_fn callback → N WebSocket clients."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._history: list[dict] = []
        self._graph_ref: AttackGraph | None = None
        self._key_pool = None  # optional KeyPool for key_status pushes

    def set_graph(self, graph: AttackGraph):
        """Hold a reference to the graph for snapshot emission."""
        self._graph_ref = graph

    def set_key_pool(self, pool):
        """Hold a reference to the run's KeyPool so key_status events can be
        broadcast when the loop logs `key_cooled`."""
        self._key_pool = pool

    def subscribe(self) -> asyncio.Queue:
        """Register a new WebSocket client. Returns a queue to await on."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove a WebSocket client."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    @property
    def history(self) -> list[dict]:
        """Replay buffer for late-joining clients."""
        return list(self._history)

    def log_fn(self, event: str, detail: str = ""):
        """The LogFn callback — same (str, str) → None signature."""
        msg = {
            "type": "event",
            "event": event,
            "detail": detail,
            "timestamp": time.time(),
        }
        self._history.append(msg)
        self._broadcast(msg)

        # On graph_update events, also push graph snapshot
        if event == "graph_update" and self._graph_ref is not None:
            self.emit_graph_snapshot()

        # On key_cooled / rate_limit_wall events, also push structured status
        # so the UI doesn't have to parse the log text.
        if event in ("key_cooled", "rate_limit_wall") and self._key_pool is not None:
            self.emit_key_status()

    def emit_graph_snapshot(self):
        """Push full graph state to all subscribers."""
        if self._graph_ref is None:
            return
        msg = {
            "type": "graph",
            "data": json.loads(self._graph_ref.to_json()),
            "stats": self._graph_ref.stats(),
            "timestamp": time.time(),
        }
        self._broadcast(msg)

    def emit_status(self, status: str, **extra):
        """Emit a run status event (started, completed, error, stopped)."""
        msg = {
            "type": "status",
            "status": status,
            "timestamp": time.time(),
            **extra,
        }
        self._history.append(msg)
        self._broadcast(msg)

    def emit_key_status(self):
        """Broadcast current API key pool state (active/total + per-key cooldown info)."""
        if self._key_pool is None:
            return
        statuses = [
            {
                "masked": s.masked,
                "cooled_until": s.cooled_until,
                "reason": s.reason,
            }
            for s in self._key_pool.status()
        ]
        self.emit_status(
            "key_status",
            active=self._key_pool.active_count(),
            total=self._key_pool.total,
            keys=statuses,
        )

    def clear_history(self):
        """Clear replay buffer (e.g., on new run)."""
        self._history.clear()

    def _broadcast(self, msg: dict):
        """Push a message to all subscriber queues."""
        for q in self._subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # drop if client is slow
