"""Filesystem-backed scenario repository."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from mesmer.core.persistence.scenarios import (
    ScenarioConflict,
    ScenarioDocument,
    ScenarioNotFound,
    ScenarioPathError,
    ScenarioValidationError,
)
from mesmer.core.scenario import load_scenario_from_text


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:80] or "scenario"


class FileScenarioRepository:
    """Filesystem-backed scenario repository.

    ``root`` is the packaged/read catalog of scenario templates. ``write_root``
    is the local workspace-owned catalog. When both are supplied, packaged
    templates are listed as read-only and edits are saved as workspace copies.
    """

    def __init__(self, root: str | Path = ".", *, write_root: str | Path | None = None) -> None:
        self.root = Path(root)
        self.write_root = Path(write_root) if write_root is not None else self.root

    def list(self) -> list[ScenarioDocument]:
        skip_dirs = {".venv", "node_modules", ".git", "__pycache__", "dist"}
        docs: list[ScenarioDocument] = []
        seen: set[Path] = set()
        for ext in ("*.yaml", "*.yml"):
            for root in self._roots():
                for path in sorted(root.rglob(ext)):
                    resolved = path.resolve()
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    try:
                        parts = path.relative_to(root).parts
                    except ValueError:
                        parts = path.parts
                    if any(part.startswith(".") or part in skip_dirs for part in parts):
                        continue
                    try:
                        raw = path.read_text(encoding="utf-8")
                        data = yaml.safe_load(raw)
                        if not isinstance(data, dict):
                            continue
                        if not all(k in data for k in ("target", "objective", "modules")):
                            continue
                        docs.append(self._document_from_path(path))
                    except Exception:
                        continue
        return docs

    def get(self, scenario_path: str) -> ScenarioDocument:
        path = self._resolve(scenario_path)
        if not path.exists():
            raise ScenarioNotFound(f"Scenario not found: {scenario_path}")
        return self._document_from_path(path)

    def create_private(self, name: str, yaml_content: str) -> ScenarioDocument:
        ok, err = self.validate(yaml_content)
        if not ok:
            raise ScenarioValidationError(f"YAML is invalid: {err}")
        target_path = self.write_root / f"{_slugify(name)}.yaml"
        if target_path.exists():
            raise ScenarioConflict(
                f"Scenario already exists at {target_path}. Pick a different name."
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(yaml_content, encoding="utf-8")
        return self._document_from_path(target_path)

    def update(self, scenario_path: str, yaml_content: str) -> ScenarioDocument:
        path = self._resolve(scenario_path)
        if not path.exists():
            raise ScenarioNotFound(f"Scenario not found: {scenario_path}")
        ok, err = self.validate(yaml_content)
        if not ok:
            raise ScenarioValidationError(f"YAML is invalid: {err}")

        if not self._is_writable_path(path):
            path = self._workspace_copy_path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_content, encoding="utf-8")
        return self._document_from_path(path)

    def validate(self, yaml_content: str) -> tuple[bool, str | None]:
        try:
            load_scenario_from_text(yaml_content)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        return True, None

    def resolve_path(self, scenario_path: str) -> str:
        return str(self._resolve(scenario_path))

    def _document_from_path(self, path: Path) -> ScenarioDocument:
        raw = path.read_text(encoding="utf-8")
        return ScenarioDocument(
            path=str(path),
            yaml_content=raw,
            scenario=load_scenario_from_text(raw),
            source="workspace" if self._is_writable_path(path) else "template",
            editable=self._is_writable_path(path),
        )

    def _resolve(self, scenario_path: str) -> Path:
        raw = Path(scenario_path)
        roots = self._roots()
        if raw.is_absolute():
            candidate = raw.resolve()
            if self._is_under_any_root(candidate, roots):
                return candidate
            raise ScenarioPathError(
                "Refusing to access a scenario outside the scenario directories."
            )

        for root in roots:
            candidate = (root / raw).resolve()
            if not self._is_under_root(candidate, root):
                continue
            if candidate.exists():
                return candidate

        fallback = (self.write_root / raw).resolve()
        if not self._is_under_root(fallback, self.write_root):
            raise ScenarioPathError(
                "Refusing to access a scenario outside the scenario directories."
            )
        return fallback

    def _roots(self) -> list[Path]:
        roots: list[Path] = []
        for root in (self.root, self.write_root):
            if root.resolve() not in {r.resolve() for r in roots}:
                roots.append(root)
        return roots

    def _is_writable_path(self, path: Path) -> bool:
        return self._is_under_root(path.resolve(), self.write_root)

    def _workspace_copy_path(self, path: Path) -> Path:
        try:
            rel = path.resolve().relative_to(self.root.resolve())
        except ValueError:
            rel = Path(path.name)
        return self.write_root / rel

    @staticmethod
    def _is_under_any_root(path: Path, roots: list[Path]) -> bool:
        return any(FileScenarioRepository._is_under_root(path, root) for root in roots)

    @staticmethod
    def _is_under_root(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True
