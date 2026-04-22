"""Scenario — load and run attack scenarios from YAML."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Objective:
    """What we're trying to achieve."""

    goal: str
    success_signals: list[str] = field(default_factory=list)
    max_turns: int = 25


@dataclass
class TargetConfig:
    """Target configuration from scenario YAML."""

    adapter: str = "openai"
    url: str = ""
    base_url: str = ""
    model: str = ""
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    body_template: str = ""
    response_path: str = ""
    api_key: str = ""          # resolved from ${ENV_VAR}
    api_key_env: str = ""      # legacy: env var name to read
    api_keys: list[str] = field(default_factory=list)
    system_prompt: str = ""

    # WebSocket declarative config
    send_template: str = '{"message": "{{message}}"}'
    receive: dict | None = None         # frame routing config
    connect_signal: dict | None = None  # e.g. {"field": "type", "value": "connected"}
    query_params: dict = field(default_factory=dict)
    connect_timeout: float = 10.0
    receive_timeout: float = 90.0


@dataclass
class AgentConfig:
    """Agent (attacker brain) configuration — fully declarative."""

    # LiteLLM model string: "openrouter/model", "anthropic/model", "openai/model", etc.
    model: str = "openrouter/anthropic/claude-sonnet-4-20250514"
    api_key: str = ""          # resolved from ${ENV_VAR} — supports comma-separated for rotation
    api_base: str = ""         # optional: custom endpoint
    temperature: float = 0.7
    max_tokens: int | None = None
    # Extra params passed to litellm (e.g. {"top_p": 0.9})
    extra: dict = field(default_factory=dict)

    # Internal — populated from api_key if comma-separated
    _keys: list[str] = field(default_factory=list, repr=False)
    _pool: "object | None" = field(default=None, repr=False)

    def __post_init__(self):
        """Parse comma-separated api_key into rotation list + build KeyPool."""
        if self.api_key and "," in self.api_key:
            self._keys = [k.strip() for k in self.api_key.split(",") if k.strip()]
            self.api_key = self._keys[0]  # set first as default
        elif self.api_key:
            self._keys = [self.api_key]

        # Build a KeyPool that supports per-key cooldowns (rate-limit exclusion)
        from mesmer.core.keys import KeyPool
        self._pool = KeyPool(list(self._keys))

    @property
    def pool(self):
        """The KeyPool backing this config. Exposes cooldown API for the loop."""
        return self._pool

    def next_key(self) -> str:
        """Return the next key whose cooldown (if any) has expired.

        Empty string if there are no keys OR every key is currently cooled —
        litellm will then fall back to env-var auth for the provider.
        """
        if self._pool is None or not self._keys:
            return self.api_key
        key = self._pool.next()
        return key if key is not None else ""

    @property
    def key_count(self) -> int:
        return len(self._keys)


@dataclass
class Scenario:
    """A complete attack scenario."""

    name: str
    description: str
    target: TargetConfig
    objective: Objective
    module: str
    agent: AgentConfig = field(default_factory=AgentConfig)
    module_paths: list[str] = field(default_factory=list)
    # Optional scenario-specific judge rubric additions. Appended to the stock
    # JUDGE_SYSTEM prompt so the judge credits wins that matter for this target
    # (e.g. refusal-list leaks, persona confirmation, behavioural policies).
    judge_rubric_additions: str = ""

    # Convenience accessors (backward compat)
    @property
    def agent_model(self) -> str:
        return self.agent.model

    @property
    def agent_temperature(self) -> float:
        return self.agent.temperature


def _resolve_env_vars(value):
    """Recursively resolve ${ENV_VAR} placeholders in strings, dicts, lists."""
    if isinstance(value, str) and "${" in value:
        # Full replacement: "${VAR}" → env value (preserves type for single-var case)
        if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
            return os.environ.get(value[2:-1], "")
        # Inline replacement: "Bearer ${VAR}" → "Bearer sk-..."
        return re.sub(
            r'\$\{(\w+)\}',
            lambda m: os.environ.get(m.group(1), ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_scenario(path: str | Path) -> Scenario:
    """Load a scenario from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    # Resolve ${ENV_VAR} placeholders across entire target and agent blocks
    target_data = _resolve_env_vars(data.get("target", {}))
    agent_data = _resolve_env_vars(data.get("agent", {}))

    target = TargetConfig(
        adapter=target_data.get("adapter", "openai"),
        url=target_data.get("url", ""),
        base_url=target_data.get("base_url", ""),
        model=target_data.get("model", ""),
        method=target_data.get("method", "POST"),
        headers=target_data.get("headers", {}),
        body_template=target_data.get("body_template", ""),
        response_path=target_data.get("response_path", ""),
        api_key=target_data.get("api_key", ""),
        api_key_env=target_data.get("api_key_env", ""),
        api_keys=target_data.get("api_keys", []),
        system_prompt=target_data.get("system_prompt", ""),
        # WebSocket declarative
        send_template=target_data.get("send_template", '{"message": "{{message}}"}'),
        receive=target_data.get("receive"),
        connect_signal=target_data.get("connect_signal"),
        query_params=target_data.get("query_params", {}),
        connect_timeout=target_data.get("connect_timeout", 10.0),
        receive_timeout=target_data.get("receive_timeout", 90.0),
    )

    obj_data = data.get("objective", {})
    objective = Objective(
        goal=obj_data.get("goal", ""),
        success_signals=obj_data.get("success_signals", []),
        max_turns=obj_data.get("max_turns", 25),
    )

    agent = AgentConfig(
        model=agent_data.get("model", "openrouter/anthropic/claude-sonnet-4-20250514"),
        api_key=agent_data.get("api_key", ""),
        api_base=agent_data.get("api_base", ""),
        temperature=agent_data.get("temperature", 0.7),
        max_tokens=agent_data.get("max_tokens"),
        extra=agent_data.get("extra", {}),
    )

    judge_data = data.get("judge", {}) or {}
    judge_rubric_additions = str(judge_data.get("rubric_additions", "") or "").strip()

    return Scenario(
        name=data.get("name", "Unnamed Scenario"),
        description=data.get("description", ""),
        target=target,
        objective=objective,
        module=data.get("module", ""),
        agent=agent,
        module_paths=data.get("module_paths", []),
        judge_rubric_additions=judge_rubric_additions,
    )
