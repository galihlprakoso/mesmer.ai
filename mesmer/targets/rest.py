"""REST target — generic HTTP API adapter."""

from __future__ import annotations

import json

import httpx

from mesmer.targets.base import Target, Turn


class RESTTarget(Target):
    """
    Generic REST API target. Sends messages via HTTP POST/PUT
    with configurable body templates and response extraction.
    """

    def __init__(
        self,
        url: str,
        method: str = "POST",
        headers: dict | None = None,
        body_template: str = "",
        response_path: str = "",
    ):
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.body_template = body_template
        self.response_path = response_path
        self._history: list[Turn] = []
        self._messages: list[dict] = []

    async def send(self, message: str) -> str:
        """Send a message via REST API."""
        self._messages.append({"role": "user", "content": message})

        # Build request body from template
        body = self._build_body(message)

        async with httpx.AsyncClient(timeout=60.0) as client:
            if self.method == "POST":
                resp = await client.post(self.url, json=body, headers=self.headers)
            elif self.method == "PUT":
                resp = await client.put(self.url, json=body, headers=self.headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {self.method}")

        resp.raise_for_status()
        reply = self._extract_response(resp.json())

        self._messages.append({"role": "assistant", "content": reply})
        self._history.append(Turn(sent=message, received=reply))

        return reply

    async def reset(self) -> None:
        self._messages.clear()
        self._history.clear()

    def get_history(self) -> list[Turn]:
        return list(self._history)

    def _build_body(self, message: str) -> dict:
        """Build request body from template, substituting placeholders."""
        if not self.body_template:
            return {"message": message, "history": self._messages}

        # Replace {{message}} and {{history}} in the template
        body_str = self.body_template.replace("{{message}}", message)
        body_str = body_str.replace("{{history}}", json.dumps(self._messages))
        return json.loads(body_str)

    def _extract_response(self, data: dict) -> str:
        """Extract the response text using a dotted/bracket path."""
        if not self.response_path:
            # Try common response structures
            if isinstance(data, str):
                return data
            for key in ["response", "message", "content", "text", "reply"]:
                if key in data:
                    val = data[key]
                    return val if isinstance(val, str) else json.dumps(val)
            return json.dumps(data)

        # Navigate the response path (e.g., "choices[0].message.content")
        result = data
        for part in self.response_path.replace("[", ".[").split("."):
            if not part:
                continue
            if part.startswith("[") and part.endswith("]"):
                idx = int(part[1:-1])
                result = result[idx]
            else:
                result = result[part]

        return str(result)
