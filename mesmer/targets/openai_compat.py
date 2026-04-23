"""OpenAI-compatible target — works with OpenAI, OpenRouter, Azure, local APIs."""

from __future__ import annotations

import os

import openai

from mesmer.targets.base import Target, Turn


class OpenAITarget(Target):
    """
    Target that communicates via OpenAI-compatible chat completions API.
    Works with OpenAI, OpenRouter, Azure OpenAI, Ollama, vLLM, etc.
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        api_key: str = "",
        api_key_env: str = "",
        system_prompt: str = "",
        temperature: float = 0.7,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self._history: list[Turn] = []

        # Resolve API key: explicit > env var name > OPENAI_API_KEY
        resolved_key = api_key
        if not resolved_key and api_key_env:
            resolved_key = os.environ.get(api_key_env, "")
        if not resolved_key:
            resolved_key = os.environ.get("OPENAI_API_KEY", "")

        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=resolved_key or "missing-key",
        )

        # Messages sent to the target API (includes system prompt)
        self._messages: list[dict] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

        # Most-recent completion's ``usage`` block (OpenAI shape:
        # prompt_tokens / completion_tokens / total_tokens). Exposed so
        # the benchmark's baseline arm can record target-side token use
        # — the ReAct loop gets this via ctx.telemetry, but the baseline
        # bypasses the loop. ``None`` when the provider omits usage.
        self.last_usage: object | None = None

    async def send(self, message: str) -> str:
        """Send a message and get the target's reply."""
        self._messages.append({"role": "user", "content": message})

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages,
            temperature=self.temperature,
        )

        reply = response.choices[0].message.content or ""
        self._messages.append({"role": "assistant", "content": reply})
        self._history.append(Turn(sent=message, received=reply))
        self.last_usage = getattr(response, "usage", None)

        return reply

    async def reset(self) -> None:
        """Reset conversation — keeps system prompt, clears history."""
        self._messages = []
        if self.system_prompt:
            self._messages.append({"role": "system", "content": self.system_prompt})
        self._history.clear()

    def get_history(self) -> list[Turn]:
        return list(self._history)
