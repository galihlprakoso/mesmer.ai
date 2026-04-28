"""Scenario repository contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mesmer.core.scenario import Scenario


class ScenarioRepositoryError(Exception):
    """Base class for scenario repository failures."""


class ScenarioNotFound(ScenarioRepositoryError):
    """Requested scenario does not exist."""


class ScenarioConflict(ScenarioRepositoryError):
    """Scenario create/update would overwrite an existing record."""


class ScenarioPathError(ScenarioRepositoryError):
    """Scenario path/id is invalid for this repository."""


class ScenarioValidationError(ScenarioRepositoryError):
    """Scenario YAML is not valid."""


@dataclass(frozen=True)
class ScenarioDocument:
    """Raw YAML plus parsed scenario metadata."""

    path: str
    yaml_content: str
    scenario: Scenario
    source: str = "workspace"
    editable: bool = True

    def summary(self) -> dict:
        target = self.scenario.target
        return {
            "path": self.path,
            "name": self.scenario.name,
            "description": self.scenario.description,
            "target_adapter": target.adapter,
            "target_url": target.url or target.base_url or target.model or "",
            "modules": list(self.scenario.modules),
            "max_turns": self.scenario.objective.max_turns,
            "source": self.source,
            "editable": self.editable,
        }


class ScenarioRepository(Protocol):
    """CRUD contract for web-editable scenarios."""

    def list(self) -> list[ScenarioDocument]:
        """List valid scenarios."""

    def get(self, scenario_path: str) -> ScenarioDocument:
        """Load one scenario document."""

    def create_private(self, name: str, yaml_content: str) -> ScenarioDocument:
        """Create a private scenario."""

    def update(self, scenario_path: str, yaml_content: str) -> ScenarioDocument:
        """Replace an existing scenario."""

    def validate(self, yaml_content: str) -> tuple[bool, str | None]:
        """Validate YAML without persisting it."""

    def resolve_path(self, scenario_path: str) -> str:
        """Resolve an API scenario id/path into a local file path when available."""
