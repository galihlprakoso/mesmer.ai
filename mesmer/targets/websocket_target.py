"""
Declarative WebSocket target adapter.

Fully configurable via YAML — send templates with placeholders,
frame-type routing, response assembly, connection handshakes.
Works with any WebSocket protocol without code changes.

Example configs:

  # Simple: send JSON, get JSON back
  send_template: '{"text": "{{message}}"}'
  receive:
    response_field: reply

  # Complex multi-frame (like VPA):
  send_template: |
    {"type": "chat_message", "id": "{{request_id}}", "message": "{{message}}"}
  receive:
    request_id_field: requestId
    type_field: type
    frames:
      text:
        content_field: content
        action: set_response
      done:
        action: complete
      error:
        content_field: content
        action: error
      interim:
        content_field: content
        action: accumulate
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from urllib.parse import urlencode

import websockets
import websockets.exceptions

from mesmer.targets.base import Target, Turn


class WebSocketTarget(Target):
    """
    Declarative WebSocket target. Every aspect of the protocol is
    configured via constructor args (loaded from scenario YAML).
    """

    def __init__(
        self,
        url: str,
        # Outgoing message template — {{message}} and {{request_id}} are replaced
        send_template: str = '{"message": "{{message}}"}',
        # Incoming frame parsing
        receive: dict | None = None,
        # Connection handshake
        connect_signal: dict | None = None,
        # URL params & headers
        query_params: dict | None = None,
        headers: dict | None = None,
        # Timeouts
        connect_timeout: float = 10.0,
        receive_timeout: float = 90.0,
    ):
        self.base_url = url
        self.send_template = send_template
        self.receive_config = receive or {"response_field": "response"}
        self.connect_signal = connect_signal  # e.g. {"field": "type", "value": "connected"}
        self.query_params = query_params or {}
        self.headers = headers or {}
        self.connect_timeout = connect_timeout
        self.receive_timeout = receive_timeout
        self._history: list[Turn] = []
        self._ws = None

        # Parse receive config
        self._type_field = self.receive_config.get("type_field")
        self._request_id_field = self.receive_config.get("request_id_field")
        self._response_field = self.receive_config.get("response_field")
        self._frames = self.receive_config.get("frames", {})
        self._is_multiframe = bool(self._frames)

    # ── Connection ─────────────────────────────────────────────

    def _build_url(self) -> str:
        if self.query_params:
            return f"{self.base_url}?{urlencode(self.query_params)}"
        return self.base_url

    async def _ensure_connected(self):
        if self._ws is not None and self._ws_open():
            return

        url = self._build_url()
        self._ws = await websockets.connect(
            url,
            additional_headers=self.headers if self.headers else None,
            open_timeout=self.connect_timeout,
            close_timeout=10,
        )

        # Wait for connection signal if configured
        if self.connect_signal:
            field = self.connect_signal.get("field", "type")
            value = self.connect_signal.get("value", "connected")
            try:
                raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self.connect_timeout
                )
                msg = json.loads(raw)
                if msg.get(field) != value:
                    raise ConnectionError(
                        f"Expected {field}={value}, got {field}={msg.get(field)}"
                    )
            except asyncio.TimeoutError:
                raise ConnectionError(
                    f"Timed out waiting for {field}={value} ({self.connect_timeout}s)"
                )

    # ── Send & Receive ─────────────────────────────────────────

    async def send(self, message: str) -> str:
        """Send a message with auto-reconnect on connection drop."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                await self._ensure_connected()

                request_id = f"req-{int(time.time() * 1000)}-{_rand(5)}"

                # Render the send template
                payload_str = self.send_template
                payload_str = payload_str.replace("{{message}}", _json_escape(message))
                payload_str = payload_str.replace("{{request_id}}", request_id)
                payload_str = payload_str.replace("{{timestamp}}", str(int(time.time() * 1000)))

                await self._ws.send(payload_str)

                # Receive
                if self._is_multiframe:
                    reply = await self._receive_multiframe(request_id)
                else:
                    reply = await self._receive_single()

                self._history.append(Turn(sent=message, received=reply))
                return reply

            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.ConnectionClosedError,
                    websockets.exceptions.ConnectionClosedOK,
                    ConnectionError,
                    OSError) as e:
                # Connection dropped — force reconnect on next attempt
                self._ws = None
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)  # brief pause before reconnect
                    continue
                raise RuntimeError(f"WebSocket connection lost after {max_retries} attempts: {e}") from e

    async def _receive_single(self) -> str:
        """Single-frame response — wait for one message, extract field."""
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self.receive_timeout)
        except asyncio.TimeoutError:
            return "(timeout — no response)"

        try:
            data = json.loads(raw)
            if self._response_field:
                return _extract(data, self._response_field)
            return json.dumps(data)
        except (json.JSONDecodeError, TypeError):
            return raw if isinstance(raw, str) else raw.decode()

    async def _receive_multiframe(self, request_id: str) -> str:
        """
        Multi-frame response — route each frame by type_field,
        apply the configured action, stop on 'complete' or 'error'.
        """
        response_text = ""
        accumulated = []

        try:
            while True:
                raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self.receive_timeout
                )
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                # Filter by request_id if configured
                if self._request_id_field:
                    msg_rid = msg.get(self._request_id_field)
                    if msg_rid and msg_rid != request_id:
                        continue

                # Route by frame type
                frame_type = msg.get(self._type_field, "") if self._type_field else ""
                frame_cfg = self._frames.get(frame_type, self._frames.get("*"))

                if frame_cfg is None:
                    # Unknown frame type — check if connect_signal, skip it
                    if self.connect_signal and msg.get(self.connect_signal.get("field")) == self.connect_signal.get("value"):
                        continue
                    continue

                action = frame_cfg.get("action", "ignore")
                content_field = frame_cfg.get("content_field", "content")
                content = _extract(msg, content_field) if content_field else ""

                if action == "set_response":
                    response_text = content

                elif action == "append_response":
                    response_text += content

                elif action == "accumulate":
                    if content:
                        accumulated.append(content)

                elif action == "complete":
                    # Optionally extract content from the done frame too
                    if content_field and content and not response_text:
                        response_text = content
                    break

                elif action == "error":
                    raise RuntimeError(f"Target error: {content or 'Unknown'}")

                elif action == "ignore":
                    continue

        except asyncio.TimeoutError:
            if not response_text:
                response_text = "(timeout — no response)"

        return response_text

    # ── Lifecycle ──────────────────────────────────────────────

    async def reset(self) -> None:
        await self.close()
        self._history.clear()

    def get_history(self) -> list[Turn]:
        return list(self._history)

    def _ws_open(self) -> bool:
        """Check if the WebSocket connection is still open (works across websockets versions)."""
        ws = self._ws
        if ws is None:
            return False
        # websockets >=13 uses .state on the protocol
        if hasattr(ws, "protocol") and hasattr(ws.protocol, "state"):
            from websockets.protocol import State
            return ws.protocol.state is State.OPEN
        # websockets >=13 direct .state
        if hasattr(ws, "state"):
            try:
                from websockets.protocol import State
                return ws.state is State.OPEN
            except ImportError:
                pass
        # Legacy websockets <13 had .closed
        if hasattr(ws, "closed"):
            return not ws.closed
        # Fallback: assume open, let errors be caught on send/recv
        return True

    async def close(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None


# ── Helpers ────────────────────────────────────────────────────

def _rand(n: int = 5) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


def _json_escape(s: str) -> str:
    """Escape a string for embedding inside a JSON template."""
    return json.dumps(s)[1:-1]  # json.dumps adds quotes, strip them


def _extract(data: dict, path: str) -> str:
    """Extract a value using dotted/bracket path (e.g., 'data.content' or 'choices[0].text')."""
    result = data
    for part in path.replace("[", ".[").split("."):
        if not part:
            continue
        try:
            if part.startswith("[") and part.endswith("]"):
                result = result[int(part[1:-1])]
            else:
                result = result[part]
        except (KeyError, IndexError, TypeError):
            return ""
    return str(result) if result is not None else ""
