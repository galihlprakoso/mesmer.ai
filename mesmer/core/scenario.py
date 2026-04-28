"""Scenario — load and run attack scenarios from YAML."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mesmer.core.artifacts import ArtifactError, ArtifactSpec
from mesmer.core.constants import ScenarioMode
from mesmer.core.keys import ThrottleConfig


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
    # Appended to every user message sent to this target. Used by the bench
    # runner to put each attacker turn inside a `pre_prompt + attacker +
    # post_prompt` sandwich — matches Tensor Trust's canonical defense shape
    # and generalises to any per-turn defence suffix. Default "" = no-op.
    user_turn_suffix: str = ""

    # Declarative rate-limit policy for TARGET-side calls — symmetric with
    # ``AgentConfig.throttle``. ``None`` = no throttling (legacy behaviour).
    # Today only the ``openai`` adapter honours this; other adapters accept
    # the field but ignore it. The pool is pulled from the same process-level
    # cache as the agent's, keyed on the sorted tuple of API keys — so two
    # bench targets pointing at the same provider key automatically share
    # one throttle budget. First caller wins on configuration; subsequent
    # targets declaring a different throttle see theirs ignored (matches
    # agent-side semantics).
    throttle: ThrottleConfig | None = None

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

    Mesmer uses role-aware model routing:

      1. ``model`` — the executive and manager brain. For the hackathon this
         is Claude Opus 4.7, because those roles own planning and judgment.
      2. ``sub_module_model`` — employee / leaf technique brain. Defaults to
         Claude Haiku 4.5 so low-level probes remain cheaper while staying on
         Claude.
      3. ``judge_model`` — the evaluator model. Defaults to ``model`` when
         unset.

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
    model: str = "anthropic/claude-opus-4-7"
    # Optional legacy field. Kept for old scenario YAML compatibility, but
    # model ensemble rotation is intentionally disabled.
    models: list[str] = field(default_factory=list)
    sub_module_model: str = "anthropic/claude-haiku-4-5"
    # Model used by the judge / refinement LLM calls. Empty → falls back to
    # the attacker model. Keeping this separate stops the judge from drifting
    # with the attacker rotation.
    judge_model: str = ""
    api_key: str = ""          # resolved from ${ENV_VAR}
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

    # Declarative rate-limit policy for the attacker's API keys. None = no
    # throttling (legacy behaviour). See :class:`mesmer.core.keys.ThrottleConfig`.
    # The pool lives in a process-level cache keyed by the sorted key
    # tuple so sibling bench trials that share keys share a throttle.
    throttle: ThrottleConfig | None = None

    # Optional PRNG seed for reproducibility. When set (non-None), the bench
    # orchestrator uses this to seed Python's ``random`` module before each
    # trial so technique selection, tie-breaks, and any other ``random.*``
    # calls are deterministic across reruns. The attacker LLM's sampling is
    # NOT deterministic even with seed set (provider-side temperature) —
    # we use seed for the mesmer-level randomness and N-seed averaging for
    # the LLM-level variance. ``None`` means "no reseeding" (legacy behaviour).
    seed: int | None = None

    # Internal — single API key only. Comma-separated pools are intentionally
    # not supported; if a legacy value is provided, only the first key is used.
    _keys: list[str] = field(default_factory=list, repr=False)
    _pool: "object | None" = field(default=None, repr=False)

    def __post_init__(self):
        """Normalize config and build the single-key pool.

        Context-budget fields are clamped to safe ranges — an invalid YAML
        value degrades to the default instead of crashing the run.
        """
        if self.models:
            # Normalise: strip whitespace, drop empties.
            self.models = [m.strip() for m in self.models if m and m.strip()]
            if self.models:
                self.model = self.models[0]

        if not isinstance(self.sub_module_model, str) or not self.sub_module_model.strip():
            self.sub_module_model = "anthropic/claude-haiku-4-5"

        if self.api_key:
            first_key = self.api_key.split(",", 1)[0].strip()
            self.api_key = first_key
            self._keys = [first_key] if first_key else []

        # Build a single-key pool that supports shared throttling. The pool
        # is pulled from a process-level cache keyed by the configured API key
        # so when the bench harness constructs N AgentConfigs per trial, they
        # all share one throttle.
        from mesmer.core.keys import get_or_create_pool
        self._pool = get_or_create_pool(list(self._keys), throttle=self.throttle)

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
        """The KeyPool backing this config. Exposes throttle state for the loop."""
        return self._pool

    def next_key(self) -> str:
        """Return the configured API key.

        Empty string means litellm will fall back to provider env-var auth.
        Mesmer intentionally does not rotate across API keys.
        """
        return self.api_key

    @property
    def key_count(self) -> int:
        return len(self._keys)

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
        return 0

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
    """A complete attack scenario.

    Modules are referenced by name from the registry and orchestrated by a
    scenario-scoped *executive* that the runner synthesizes in memory at
    run start. Single-manager scenarios use a one-element list. The
    executive itself is never authored in YAML — it's purely a runtime
    construct that owns the user-facing chat and dispatches managers as
    sub-modules.
    """

    name: str
    description: str
    target: TargetConfig
    objective: Objective
    # Manager-level modules from the registry that the executive can
    # dispatch. Order is a hint to the executive (presented in this order
    # in its tool list) but not a contract — the executive picks dispatch
    # order based on conversation, judge feedback, and TAPER frontier.
    modules: list[str] = field(default_factory=list)
    # Declarative durable documents the scenario expects. These are not
    # graph outputs; agents update them intentionally via update_artifact.
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    # Optional override for the synthesized executive's system prompt.
    # When None, the runner loads the default
    # ``mesmer/core/agent/prompts/executive.prompt.md``. Use this when the
    # generic "orchestrate the listed modules to achieve the objective"
    # framing isn't enough — e.g. a scenario that wants the executive to
    # dispatch one module strictly before another and pivot framing.
    leader_prompt: str | None = None
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


def _parse_throttle(raw: dict | None) -> ThrottleConfig | None:
    """Parse the optional ``agent.throttle`` YAML block.

    Absent / empty block → ``None`` (no throttling, legacy behaviour).
    Present → :class:`ThrottleConfig`. Its ``__post_init__`` clamps
    nonsense values so a typoed YAML field degrades to a safe default
    instead of crashing the scenario at run time.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    return ThrottleConfig(
        max_rpm=raw.get("max_rpm"),
        max_concurrent=raw.get("max_concurrent"),
        max_wait_seconds=raw.get("max_wait_seconds", 0.0) or 0.0,
    )


def _parse_artifacts(raw: object) -> list[ArtifactSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"Scenario 'artifacts' must be a list, got {type(raw).__name__}."
        )
    specs: list[ArtifactSpec] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, str):
            data = {"id": item}
        elif isinstance(item, dict):
            data = item
        else:
            raise ValueError(
                f"Scenario artifact entry #{idx} must be a string or mapping, "
                f"got {type(item).__name__}."
            )
        try:
            spec = ArtifactSpec(
                id=str(data.get("id", "")).strip(),
                title=str(data.get("title", "") or "").strip(),
                format=str(data.get("format", "markdown") or "markdown").strip(),
                description=str(data.get("description", "") or "").strip(),
            )
        except (ArtifactError, ValueError) as e:
            raise ValueError(f"Invalid scenario artifact entry #{idx}: {e}") from e
        if spec.id in seen:
            raise ValueError(f"Duplicate scenario artifact id: {spec.id}")
        seen.add(spec.id)
        specs.append(spec)
    return specs


def _scenario_from_data(data: dict) -> Scenario:
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
        user_turn_suffix=target_data.get("user_turn_suffix", ""),
        throttle=_parse_throttle(target_data.get("throttle")),
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

    throttle = _parse_throttle(agent_data.get("throttle"))

    agent = AgentConfig(
        model=agent_data.get("model", "anthropic/claude-opus-4-7"),
        models=list(agent_data.get("models", []) or []),
        sub_module_model=agent_data.get("sub_module_model", "anthropic/claude-haiku-4-5"),
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
        throttle=throttle,
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

    # Schema migration guard — the legacy single ``module: <name>`` field
    # was replaced by ``modules: [<name>, ...]`` plus the synthesized
    # executive layer. A YAML carrying both is ambiguous; one carrying
    # only the legacy field needs a one-line rewrite. Fail loud either
    # way so the operator sees the migration instead of running the wrong
    # leader silently.
    has_legacy = "module" in data and data["module"] is not None and str(data["module"]).strip()
    raw_modules = data.get("modules")
    if has_legacy and raw_modules:
        raise ValueError(
            "Scenario YAML contains both 'module:' (legacy) and 'modules:' "
            "(current). Drop 'module:' — the executive layer reads only "
            "'modules:'."
        )
    if has_legacy:
        raise ValueError(
            f"Scenario YAML uses the legacy 'module: {data['module']!r}' field. "
            f"Rewrite as 'modules: [{data['module']!r}]' — the runner now "
            "synthesizes a scenario-scoped executive that dispatches managers "
            "from the 'modules' list."
        )
    if raw_modules is None:
        raw_modules = []
    if not isinstance(raw_modules, list):
        raise ValueError(
            f"Scenario 'modules' must be a list of module names, got "
            f"{type(raw_modules).__name__}."
        )
    modules = [str(m).strip() for m in raw_modules if str(m).strip()]
    if not modules:
        raise ValueError(
            "Scenario 'modules' is empty — list at least one manager module "
            "name from the registry."
        )

    raw_leader_prompt = data.get("leader_prompt")
    leader_prompt: str | None = None
    if raw_leader_prompt is not None:
        text = str(raw_leader_prompt).strip()
        leader_prompt = text or None

    return Scenario(
        name=data.get("name", "Unnamed Scenario"),
        description=data.get("description", ""),
        target=target,
        objective=objective,
        modules=modules,
        artifacts=_parse_artifacts(data.get("artifacts")),
        leader_prompt=leader_prompt,
        agent=agent,
        module_paths=data.get("module_paths", []),
        judge_rubric_additions=judge_rubric_additions,
        mode=mode,
    )


def load_scenario_from_text(yaml_content: str) -> Scenario:
    """Load a scenario from raw YAML text."""
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise ValueError(
            f"Scenario YAML must be a mapping, got {type(data).__name__}."
        )
    return _scenario_from_data(data)


def load_scenario(path: str | Path) -> Scenario:
    """Load a scenario from a YAML file."""
    return load_scenario_from_text(Path(path).read_text(encoding="utf-8"))
