"""Runtime actor specs for the shared ReAct engine.

Modules are authored capabilities loaded from ``module.yaml``. The scenario
executive is a runtime coordinator synthesized from a scenario. Both can run
through the same ReAct loop, so the loop consumes ``ReactActorSpec`` instead of
pretending every actor is a registry module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mesmer.core.module import DEFAULT_TIER, SubModuleEntry


class ActorRole(str, Enum):
    """Runtime role used for prompt and tool policy."""

    EXECUTIVE = "executive"
    MODULE = "module"


@dataclass
class ToolPolicySpec:
    """Declarative tool grants for a runtime actor.

    ``builtin`` names are values from :class:`mesmer.core.constants.ToolName`.
    ``dispatch_submodules`` grants dynamic registry tools for
    ``ReactActorSpec.sub_modules``. External grants are reserved for future MCP
    and plugin tools; the current materializer rejects unknown sources until an
    explicit resolver exists.
    """

    builtin: list[str] = field(default_factory=list)
    dispatch_submodules: bool = False
    external: list[dict] = field(default_factory=list)


@dataclass
class ReactActorSpec:
    """Minimal runtime contract required by the ReAct engine."""

    name: str
    role: ActorRole
    description: str = ""
    theory: str = ""
    system_prompt: str = ""
    sub_modules: list[SubModuleEntry] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    judge_rubric: str = ""
    reset_target: bool = False
    tier: int = DEFAULT_TIER
    tool_policy: ToolPolicySpec | None = None

    def __post_init__(self) -> None:
        self.sub_modules = [
            SubModuleEntry(name=e) if isinstance(e, str) else e
            for e in self.sub_modules
        ]
        if not isinstance(self.role, ActorRole):
            self.role = ActorRole(self.role)
        if self.tool_policy is not None and not isinstance(self.tool_policy, ToolPolicySpec):
            self.tool_policy = ToolPolicySpec(**self.tool_policy)

    @property
    def sub_module_names(self) -> list[str]:
        return [e.name for e in self.sub_modules]


@dataclass
class ExecutiveSpec:
    """Scenario-scoped coordinator synthesized at run start."""

    name: str
    description: str
    system_prompt: str
    ordered_modules: list[str]
    suppress_belief_context: bool = False
    ordered_output_requirements: dict = field(default_factory=dict)

    def as_actor(self) -> ReactActorSpec:
        return ReactActorSpec(
            name=self.name,
            role=ActorRole.EXECUTIVE,
            description=self.description,
            theory="Coordinates manager modules and converses with the operator.",
            system_prompt=self.system_prompt,
            sub_modules=[SubModuleEntry(name=n) for n in self.ordered_modules],
            parameters={
                "suppress_belief_context": self.suppress_belief_context,
                "ordered_modules": list(self.ordered_modules),
                "ordered_output_requirements": self.ordered_output_requirements,
            },
            judge_rubric="",
            reset_target=False,
            tier=0,
            tool_policy=ToolPolicySpec(
                dispatch_submodules=True,
                builtin=[
                    "ask_human",
                    "talk_to_operator",
                    "list_artifacts",
                    "read_artifact",
                    "search_artifacts",
                    "update_artifact",
                    "conclude",
                ],
            ),
        )


def ensure_actor(obj) -> ReactActorSpec:
    """Coerce legacy ``ModuleConfig`` callers onto the runtime actor contract."""

    if isinstance(obj, ReactActorSpec):
        return obj
    from mesmer.core.module import ModuleConfig

    if isinstance(obj, ModuleConfig):
        return obj.as_actor()
    raise TypeError(f"Expected ReactActorSpec-compatible object, got {type(obj)!r}")


__all__ = [
    "ActorRole",
    "ExecutiveSpec",
    "ReactActorSpec",
    "ToolPolicySpec",
    "ensure_actor",
]
