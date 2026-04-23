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

    # Appended to every outgoing user message by :meth:`_apply_suffix`. Each
    # concrete adapter reads this from its ``TargetConfig`` in ``__init__``.
    # Used by the bench runner to wrap attacker turns with a per-turn defence
    # suffix (e.g. Tensor Trust's ``post_prompt``) so the target sees a
    # ``{pre_prompt}{attacker}{post_prompt}`` sandwich on every turn.
    user_turn_suffix: str = ""

    def _apply_suffix(self, text: str) -> str:
        """Append :attr:`user_turn_suffix` to an outgoing user message.

        No-op when the suffix is empty — preserves legacy behaviour for every
        non-bench scenario. Adapters should call this at the very top of
        ``send()`` before building adapter-specific payloads, so the suffix
        is visible to both the target and to any downstream logging / escape.
        """
        if not self.user_turn_suffix:
            return text
        return f"{text}\n{self.user_turn_suffix}"

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
