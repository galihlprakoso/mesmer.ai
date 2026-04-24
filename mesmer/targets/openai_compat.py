"""OpenAI-compatible target — works with OpenAI, OpenRouter, Azure, local APIs."""

from __future__ import annotations

import os

import openai

from mesmer.core.keys import KeyPool, ThrottleConfig, get_or_create_pool
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
        user_turn_suffix: str = "",
        throttle: ThrottleConfig | None = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.user_turn_suffix = user_turn_suffix
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

        # Target-side throttle — symmetric with ``AgentConfig.throttle``.
        # When ``throttle`` is set, ``send()`` acquires a slot on a shared
        # :class:`KeyPool` before dispatching the request and releases
        # afterwards. The pool is pulled from the process-level cache keyed
        # on the API-key tuple so sibling bench trials against the same
        # provider key share one budget — essential for Groq-style free
        # tiers where the quota is global to the key, not per-request.
        # ``None`` disables throttling (legacy behaviour).
        self._pool: KeyPool | None = None
        if throttle is not None:
            self._pool = get_or_create_pool([resolved_key], throttle=throttle)

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
        # Per-turn defence suffix (e.g. Tensor Trust post_prompt) gets baked
        # into the user message the API actually sees; history + Turn record
        # the wrapped text too, so logs reflect exactly what the target read.
        wrapped = self._apply_suffix(message)
        self._messages.append({"role": "user", "content": wrapped})

        # Throttle gate — blocks on rpm / concurrency / cooldown-wall caps
        # when one is configured. ``ThrottleTimeout`` propagates naturally:
        # ``execute_run`` catches it into a ``"Error: ..."`` result so the
        # bench orchestrator surfaces the wait-budget failure as the
        # trial's ``error`` field instead of a silent 0-turn stall.
        pool_active = self._pool is not None and self._pool.throttle.is_active
        if pool_active:
            await self._pool.acquire()
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                temperature=self.temperature,
            )
        finally:
            if pool_active:
                self._pool.release()

        reply = response.choices[0].message.content or ""
        self._messages.append({"role": "assistant", "content": reply})
        self._history.append(Turn(sent=wrapped, received=reply))
        self.last_usage = getattr(response, "usage", None)
        # Capture the provider-side checkpoint identifier so reproducibility
        # holds even when model strings (``llama-3.1-8b-instant``) don't date
        # themselves. OpenAI, Groq and most OpenAI-compat providers return it.
        fingerprint = getattr(response, "system_fingerprint", None)
        self.last_fingerprint = fingerprint if fingerprint else None

        return reply

    async def reset(self) -> None:
        """Reset conversation — keeps system prompt, clears history."""
        self._messages = []
        if self.system_prompt:
            self._messages.append({"role": "system", "content": self.system_prompt})
        self._history.clear()

    def get_history(self) -> list[Turn]:
        return list(self._history)
