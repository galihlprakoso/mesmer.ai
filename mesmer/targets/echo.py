"""Echo target — for testing. Echoes back messages with a prefix."""

from __future__ import annotations

from mesmer.targets.base import Target, Turn


class EchoTarget(Target):
    """
    Simple echo target for testing modules without hitting a real API.
    Returns the sent message prefixed with 'Echo: '.
    """

    def __init__(self, user_turn_suffix: str = ""):
        self.user_turn_suffix = user_turn_suffix
        self._history: list[Turn] = []

    async def send(self, message: str) -> str:
        wrapped = self._apply_suffix(message)
        reply = f"Echo: {wrapped}"
        self._history.append(Turn(sent=wrapped, received=reply))
        return reply

    async def reset(self) -> None:
        self._history.clear()

    def get_history(self) -> list[Turn]:
        return list(self._history)
