"""Base target interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time


@dataclass
class Turn:
    """A single exchange with the target."""

    sent: str
    received: str
    timestamp: float = field(default_factory=time.time)


class Target(ABC):
    """
    Abstract base for all target adapters.
    A target is the LLM being attacked — it receives messages and returns replies.
    """

    @abstractmethod
    async def send(self, message: str) -> str:
        """Send a message to the target and return its reply."""
        ...

    @abstractmethod
    async def reset(self) -> None:
        """Reset the conversation state (clear history)."""
        ...

    @abstractmethod
    def get_history(self) -> list[Turn]:
        """Get the full conversation history."""
        ...
