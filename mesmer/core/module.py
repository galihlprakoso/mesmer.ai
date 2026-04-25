"""Module — the universal building block. Every module is a ReAct agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mesmer.core.errors import InvalidModuleConfig


# Allowed tier values. Semantics (see ``ModuleConfig.tier``):
#   0 — naive / direct one-shot probe
#   1 — structural / payload-shaping
#   2 — cognitive / social manipulation (default)
#   3 — composed (lever × carrier)
_TIER_MIN = 0
_TIER_MAX = 3
DEFAULT_TIER = 2


@dataclass
class SubModuleEntry:
    """Per-entry config for a sub-module reference in a leader's ``sub_modules`` list.

    YAML accepts either a bare string (shorthand for ``name`` only) or a mapping::

        sub_modules:
          - target-profiler           # shorthand
          - name: attack-planner
            see_siblings: true        # inject sibling roster into context
          - name: recon-util
            call_siblings: true       # expose siblings as callable tools

    Attributes:
        name: module name as registered in the Registry.
        see_siblings: when True, the engine injects a ``## Available modules``
            block (name + description + theory for every sibling) into this
            sub-module's user message before it runs. Use for planner-style
            modules that need to reason about which siblings to recommend.
        call_siblings: when True, sibling modules are added to this sub-module's
            tool list, enabling direct peer delegation without routing back
            through the leader. Intended for utility/orchestrator sub-modules.
    """

    name: str
    see_siblings: bool = False
    call_siblings: bool = False

    def __str__(self) -> str:  # lets ", ".join(module.sub_modules) work unchanged
        return self.name


@dataclass
class ModuleConfig:
    """
    Configuration for a module, loaded from ``module.yaml``.

    Every module is a ReAct agent defined by:
    - name: unique identifier
    - description: what it does (agent reads this to decide when to use it)
    - theory: cognitive science basis (agent reads this to reason about why/when)
    - system_prompt: the agent's strategy/personality
    - sub_modules: other modules it can delegate to (list of SubModuleEntry)
    - judge_rubric: technique-specific scoring guidance the judge uses when
      evaluating attempts of THIS module (e.g. target-profiler is judged on
      profile quality, not extraction). Composed with the stock rubric and
      any scenario-level additions.
    - tier: attack cost / complexity bucket. Drives the "simple first"
      frontier ladder enforced by :meth:`AttackGraph.propose_frontier` via
      :meth:`Registry.tiers_for`. Semantics:

      * **0** — naive / direct: one-shot request, no delegation, no multi-turn.
        Cheapest. A real-world red-teamer's first probe.
      * **1** — structural / payload-shaping: a single or a few messages whose
        leverage is the payload structure (delimiters, fake role tokens,
        prefix commitment).
      * **2** — cognitive / social manipulation: multi-turn, emotional /
        contextual framing. All legacy modules default here so no edits are
        needed for backward compatibility.
      * **3** — composed: a leader that stacks a tier-2 cognitive lever with
        a tier-0/1 carrier. Reserved — no authored module yet.

      Values outside 0..3 raise :class:`InvalidModuleConfig` at load time so
      a typoed YAML fails loud instead of silently collapsing to the default.
    """

    name: str
    description: str = ""
    theory: str = ""
    system_prompt: str = ""
    sub_modules: list[SubModuleEntry] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    judge_rubric: str = ""
    # When True, the shared target is reset (new session from the target's POV)
    # before this module runs. Breaks the target's compounding memory across
    # sibling modules. Leave False for chained attacks that need continuity
    # (e.g. foot-in-door).
    reset_target: bool = False
    # Attack cost bucket — see class docstring for semantics.
    tier: int = DEFAULT_TIER

    def __post_init__(self) -> None:
        # Coerce any plain strings that tests or callers pass directly.
        self.sub_modules = [
            SubModuleEntry(name=e) if isinstance(e, str) else e
            for e in self.sub_modules
        ]

    @property
    def sub_module_names(self) -> list[str]:
        """Flat list of sub-module names — use when only names are needed."""
        return [e.name for e in self.sub_modules]

    def tool_description(self) -> str:
        """Full description for use as an OpenAI function-calling tool."""
        parts = [self.description]
        if self.theory:
            parts.append(f"\nTheory: {self.theory}")
        return "\n".join(parts)


def _coerce_tier(raw: object, module_name: str) -> int:
    """Parse + validate the ``tier`` value from a ``module.yaml``.

    Missing field → :data:`DEFAULT_TIER` (legacy modules stay tier-2).
    Non-int / out-of-range → :class:`InvalidModuleConfig`.
    """
    if raw is None:
        return DEFAULT_TIER
    try:
        tier = int(raw)
    except (TypeError, ValueError) as e:
        raise InvalidModuleConfig(
            module_name, "tier", raw,
            reason=f"must be an integer ({_TIER_MIN}..{_TIER_MAX})",
        ) from e
    if tier < _TIER_MIN or tier > _TIER_MAX:
        raise InvalidModuleConfig(
            module_name, "tier", tier,
            reason=f"must be in {_TIER_MIN}..{_TIER_MAX}",
        )
    return tier


def load_module_config(path: Path) -> ModuleConfig | None:
    """Load a module from a directory. Returns None if ``module.yaml`` is missing."""
    yaml_path = path / "module.yaml"
    if not yaml_path.exists():
        return None
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    name = data["name"]
    return ModuleConfig(
        name=name,
        description=data.get("description", ""),
        theory=data.get("theory", ""),
        system_prompt=data.get("system_prompt", ""),
        sub_modules=_parse_sub_modules(data.get("sub_modules", [])),
        parameters=data.get("parameters", {}),
        judge_rubric=data.get("judge_rubric", ""),
        reset_target=bool(data.get("reset_target", False)),
        tier=_coerce_tier(data.get("tier"), name),
    )


def _parse_sub_modules(raw: list) -> list[SubModuleEntry]:
    """Parse ``sub_modules`` YAML list into :class:`SubModuleEntry` objects.

    Accepts mixed entries — bare strings (shorthand) and dicts with optional
    ``see_siblings`` / ``call_siblings`` flags::

        sub_modules:
          - bare-string-module
          - name: planner-module
            see_siblings: true
    """
    entries: list[SubModuleEntry] = []
    for item in raw:
        if isinstance(item, str):
            entries.append(SubModuleEntry(name=item))
        elif isinstance(item, dict):
            entries.append(
                SubModuleEntry(
                    name=item["name"],
                    see_siblings=bool(item.get("see_siblings", False)),
                    call_siblings=bool(item.get("call_siblings", False)),
                )
            )
    return entries
