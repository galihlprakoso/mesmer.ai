"""Scenario — load and run attack scenarios from YAML."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mesmer.core.constants import ScenarioMode


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
    """Agent (attacker brain) configuration — fully declarative.

    Two models live here:

      1. ``model`` / ``models`` — the *attacker* brain. A scenario can declare
         a single ``model`` or an ``models`` ensemble. When an ensemble is
         set, :meth:`next_attacker_model` rotates through it round-robin so
         different sub-modules get different base models — cheap diversity
         without rewriting the framework.
      2. ``judge_model`` — the evaluator model. Kept stable across the run
         so scoring doesn't drift with the attacker rotation. Defaults to
         the first attacker model when unset.

    Context-budget fields (C7 — continuous mode only):

      - ``max_context_tokens`` — hard cap on the attacker prompt token count
        before compression kicks in. ``0`` means "auto-resolve via
        ``litellm.get_max_tokens(model)`` with a 10% safety margin; if that
        lookup fails, disable compression entirely". See
        :meth:`effective_max_context_tokens`.
      - ``compression_keep_recent`` — number of trailing Turns to preserve
        verbatim during compression. ``>=1``.
      - ``compression_target_ratio`` — after compression, aim for roughly
        this fraction of the cap (``0.0..1.0``). A smaller ratio compresses
        more aggressively; larger leaves more headroom before the next firing.
      - ``compression_model`` — model used for the summary LLM call. Empty
        cascade: :attr:`compression_model` → :attr:`judge_model` → attacker
        model. Keeping it configurable lets operators put compression on a
        cheaper model than the judge when cost matters.
    """

    # LiteLLM model string: "openrouter/model", "anthropic/model", "openai/model", etc.
    model: str = "openrouter/anthropic/claude-sonnet-4-20250514"
    # Optional ensemble. When non-empty, ``model`` is overwritten by the first
    # entry on __post_init__ and next_attacker_model() cycles through the list.
    models: list[str] = field(default_factory=list)
    # Model used by the judge / refinement LLM calls. Empty → falls back to
    # the attacker model. Keeping this separate stops the judge from drifting
    # with the attacker rotation.
    judge_model: str = ""
    api_key: str = ""          # resolved from ${ENV_VAR} — supports comma-separated for rotation
    api_base: str = ""         # optional: custom endpoint
    temperature: float = 0.7
    max_tokens: int | None = None
    # Extra params passed to litellm (e.g. {"top_p": 0.9})
    extra: dict = field(default_factory=dict)

    # Context budget + compression (C7). All four default to values that
    # keep the TRIALS mode path entirely unchanged — compression only
    # activates when scenario_mode == CONTINUOUS AND the effective cap > 0.
    max_context_tokens: int = 0
    compression_keep_recent: int = 10
    compression_target_ratio: float = 0.6
    compression_model: str = ""

    # Optional PRNG seed for reproducibility. When set (non-None), the bench
    # orchestrator uses this to seed Python's ``random`` module before each
    # trial so technique selection, tie-breaks, and any other ``random.*``
    # calls are deterministic across reruns. The attacker LLM's sampling is
    # NOT deterministic even with seed set (provider-side temperature) —
    # we use seed for the mesmer-level randomness and N-seed averaging for
    # the LLM-level variance. ``None`` means "no reseeding" (legacy behaviour).
    seed: int | None = None

    # Internal — populated from api_key if comma-separated
    _keys: list[str] = field(default_factory=list, repr=False)
    _pool: "object | None" = field(default=None, repr=False)
    # Internal — round-robin cursor into ``models``.
    _attacker_idx: int = field(default=0, repr=False)

    def __post_init__(self):
        """Parse comma-separated api_key into rotation list + build KeyPool.

        When ``models`` is non-empty, ``model`` is reset to ``models[0]`` so
        the two settings stay in sync — callers may still read ``model`` to
        get the current attacker brain.

        Context-budget fields are clamped to safe ranges — an invalid YAML
        value degrades to the default instead of crashing the run.
        """
        if self.models:
            # Normalise: strip whitespace, drop empties.
            self.models = [m.strip() for m in self.models if m and m.strip()]
            if self.models:
                self.model = self.models[0]

        if self.api_key and "," in self.api_key:
            self._keys = [k.strip() for k in self.api_key.split(",") if k.strip()]
            self.api_key = self._keys[0]  # set first as default
        elif self.api_key:
            self._keys = [self.api_key]

        # Build a KeyPool that supports per-key cooldowns (rate-limit exclusion)
        from mesmer.core.keys import KeyPool
        self._pool = KeyPool(list(self._keys))

        # C7 — validate / clamp context-budget fields. Defensive defaults so
        # a typoed YAML value degrades instead of tripping the compressor.
        try:
            self.max_context_tokens = max(0, int(self.max_context_tokens))
        except (TypeError, ValueError):
            self.max_context_tokens = 0
        try:
            self.compression_keep_recent = max(1, int(self.compression_keep_recent))
        except (TypeError, ValueError):
            self.compression_keep_recent = 10
        try:
            ratio = float(self.compression_target_ratio)
            if not 0.0 < ratio <= 1.0:
                ratio = 0.6
        except (TypeError, ValueError):
            ratio = 0.6
        self.compression_target_ratio = ratio
        if not isinstance(self.compression_model, str):
            self.compression_model = ""

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

    # --- attacker-model rotation (P5) ---

    def next_attacker_model(self) -> str:
        """Return the next attacker model, round-robin over ``models``.

        When the ensemble is empty, always returns ``model`` (the single
        attacker case) — callers can invoke this unconditionally without
        branching on ensemble existence.
        """
        if not self.models:
            return self.model
        chosen = self.models[self._attacker_idx % len(self.models)]
        self._attacker_idx += 1
        return chosen

    @property
    def effective_judge_model(self) -> str:
        """Model to use for judge / refinement LLM calls.

        Falls back to the attacker model when ``judge_model`` is unset —
        preserves single-model behaviour for scenarios that don't opt into
        the separation.
        """
        return self.judge_model or self.model

    @property
    def ensemble_size(self) -> int:
        return len(self.models)

    # --- context budget + compression (C7) ---

    def effective_max_context_tokens(self, model: str) -> int:
        """Resolve the hard cap on attacker-prompt tokens for ``model``.

        Priority:

          1. Explicit ``max_context_tokens > 0`` wins — operator said what
             they wanted, don't second-guess.
          2. Else ask ``litellm.get_max_tokens(model)`` and subtract a 10%
             safety margin (to leave room for the assistant's own reply and
             for tokenizer approximation error).
          3. Else return ``0`` — compression disabled for this invocation.

        The function is best-effort: any exception from litellm is treated
        as "no lookup available" rather than propagating. The compressor
        interprets a 0 cap as "no-op".
        """
        if self.max_context_tokens > 0:
            return self.max_context_tokens
        try:
            import litellm
            resolved = litellm.get_max_tokens(model)
        except Exception:
            return 0
        if not isinstance(resolved, int) or resolved <= 0:
            return 0
        # Reserve 10% headroom for the response + tokenizer slack.
        return int(resolved * 0.9)

    def effective_compression_model(self) -> str:
        """Model for the compression LLM call.

        Cascade: explicit ``compression_model`` → ``judge_model`` → attacker
        model. Lets operators put summarisation on a cheap model without
        touching the attacker or judge selection.
        """
        return self.compression_model or self.effective_judge_model


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
    # Run execution mode — see :class:`ScenarioMode`. ``TRIALS`` (default)
    # preserves the pre-existing independent-rollout behaviour. ``CONTINUOUS``
    # switches to single-conversation semantics (no per-module reset, delta-
    # aware judging, continuation framing, cross-run persistence, and
    # summary-buffer compression).
    mode: ScenarioMode = ScenarioMode.TRIALS

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

    raw_seed = agent_data.get("seed", None)
    try:
        seed_val = int(raw_seed) if raw_seed is not None else None
    except (TypeError, ValueError):
        seed_val = None

    agent = AgentConfig(
        model=agent_data.get("model", "openrouter/anthropic/claude-sonnet-4-20250514"),
        models=list(agent_data.get("models", []) or []),
        judge_model=str(agent_data.get("judge_model", "") or ""),
        api_key=agent_data.get("api_key", ""),
        api_base=agent_data.get("api_base", ""),
        temperature=agent_data.get("temperature", 0.7),
        max_tokens=agent_data.get("max_tokens"),
        extra=agent_data.get("extra", {}),
        # Context budget + compression (C7). Each field is validated /
        # clamped inside AgentConfig.__post_init__, so sloppy YAML still
        # boots with safe defaults.
        max_context_tokens=agent_data.get("max_context_tokens", 0),
        compression_keep_recent=agent_data.get("compression_keep_recent", 10),
        compression_target_ratio=agent_data.get("compression_target_ratio", 0.6),
        compression_model=str(agent_data.get("compression_model", "") or ""),
        seed=seed_val,
    )

    judge_data = data.get("judge", {}) or {}
    judge_rubric_additions = str(judge_data.get("rubric_additions", "") or "").strip()

    # Mode: accept bare string ("continuous" / "trials"). Unknown or empty
    # degrades to the safer legacy TRIALS behaviour so typos don't silently
    # flip a scenario into continuous mode.
    raw_mode = str(data.get("mode", "") or "").strip().lower()
    try:
        mode = ScenarioMode(raw_mode) if raw_mode else ScenarioMode.TRIALS
    except ValueError:
        mode = ScenarioMode.TRIALS

    return Scenario(
        name=data.get("name", "Unnamed Scenario"),
        description=data.get("description", ""),
        target=target,
        objective=objective,
        module=data.get("module", ""),
        agent=agent,
        module_paths=data.get("module_paths", []),
        judge_rubric_additions=judge_rubric_additions,
        mode=mode,
    )
