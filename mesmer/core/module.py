"""Module — the universal building block. Every module is a ReAct agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModuleConfig:
    """
    Configuration for a module. Can be loaded from YAML or defined in Python.

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

    # For Python modules that override the default ReAct loop
    custom_run: object | None = None  # async def run(ctx, **kwargs) -> str

    @property
    def has_custom_run(self) -> bool:
        return self.custom_run is not None

    def tool_description(self) -> str:
        """Full description for use as an OpenAI function-calling tool."""
        parts = [self.description]
        if self.theory:
            parts.append(f"\nTheory: {self.theory}")
        return "\n".join(parts)


def load_module_config(path: Path) -> ModuleConfig | None:
    """
    Load a module from a directory. Checks for module.yaml first,
    then module.py. Returns None if neither exists.
    """
    yaml_path = path / "module.yaml"
    py_path = path / "module.py"

    if yaml_path.exists():
        return _load_yaml_module(yaml_path)
    elif py_path.exists():
        return _load_python_module(py_path)
    return None


def _load_yaml_module(path: Path) -> ModuleConfig:
    """Load a YAML-defined module."""
    with open(path) as f:
        data = yaml.safe_load(f)

    return ModuleConfig(
        name=data["name"],
        description=data.get("description", ""),
        theory=data.get("theory", ""),
        system_prompt=data.get("system_prompt", ""),
        sub_modules=data.get("sub_modules", []),
        parameters=data.get("parameters", {}),
        judge_rubric=data.get("judge_rubric", ""),
    )


def _load_python_module(path: Path) -> ModuleConfig:
    """
    Load a Python-defined module. The module.py must define a class
    that inherits from or duck-types Module with name, description,
    theory, system_prompt, sub_modules, and an async run() method.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"mesmer_module_{path.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Find the Module class — first class with a 'name' attribute
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if isinstance(attr, type) and hasattr(attr, "name") and attr_name != "Module":
            instance = attr()
            return ModuleConfig(
                name=instance.name,
                description=getattr(instance, "description", ""),
                theory=getattr(instance, "theory", ""),
                system_prompt=getattr(instance, "system_prompt", ""),
                sub_modules=getattr(instance, "sub_modules", []),
                parameters=getattr(instance, "parameters", {}),
                judge_rubric=getattr(instance, "judge_rubric", ""),
                custom_run=getattr(instance, "run", None),
            )

    raise ValueError(f"No Module class found in {path}")
