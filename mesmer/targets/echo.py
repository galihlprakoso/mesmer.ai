"""Echo target — for testing. Echoes back messages with a prefix."""

from __future__ import annotations

from mesmer.targets.base import Target, Turn


class EchoTarget(Target):
    """
    Simple echo target for testing modules without hitting a real API.
    Returns the sent message prefixed with 'Echo: '.
    """

    def __init__(self):
        self._history: list[Turn] = []

    async def send(self, message: str) -> str:
        reply = f"Echo: {message}"
        self._history.append(Turn(sent=message, received=reply))
        return reply

    async def reset(self) -> None:
        self._history.clear()

    def get_history(self) -> list[Turn]:
        return list(self._history)
