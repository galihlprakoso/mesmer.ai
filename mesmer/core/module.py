"""Module — the universal building block. Every module is a ReAct agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModuleConfig:
    """
    Configuration for a module, loaded from ``module.yaml``.

    Every module is a ReAct agent defined by:
    - name: unique identifier
    - description: what it does (agent reads this to decide when to use it)
    - theory: cognitive science basis (agent reads this to reason about why/when)
    - system_prompt: the agent's strategy/personality
    - sub_modules: other modules it can delegate to
    - judge_rubric: technique-specific scoring guidance the judge uses when
      evaluating attempts of THIS module (e.g. safety-profiler is judged on
      profile quality, not extraction). Composed with the stock rubric and
      any scenario-level additions.
    """

    name: str
    description: str = ""
    theory: str = ""
    system_prompt: str = ""
    sub_modules: list[str] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    judge_rubric: str = ""
    # When True, the shared target is reset (new session from the target's POV)
    # before this module runs. Breaks the target's compounding memory across
    # sibling modules. Leave False for chained attacks that need continuity
    # (e.g. foot-in-door).
    reset_target: bool = False

    def tool_description(self) -> str:
        """Full description for use as an OpenAI function-calling tool."""
        parts = [self.description]
        if self.theory:
            parts.append(f"\nTheory: {self.theory}")
        return "\n".join(parts)


def load_module_config(path: Path) -> ModuleConfig | None:
    """Load a module from a directory. Returns None if ``module.yaml`` is missing."""
    yaml_path = path / "module.yaml"
    if not yaml_path.exists():
        return None
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    return ModuleConfig(
        name=data["name"],
        description=data.get("description", ""),
        theory=data.get("theory", ""),
        system_prompt=data.get("system_prompt", ""),
        sub_modules=data.get("sub_modules", []),
        parameters=data.get("parameters", {}),
        judge_rubric=data.get("judge_rubric", ""),
        reset_target=bool(data.get("reset_target", False)),
    )
