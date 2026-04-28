"""Module catalogs.

Modules are not just files; they are installable capabilities. Builtins,
workspace-authored modules, overrides, and future marketplace modules can all
surface the same :class:`ModuleRecord` shape and be composed into a runtime
registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from mesmer.core.module import ModuleConfig, load_module_config, load_module_config_from_text
from mesmer.core.persistence import StorageProvider, join_storage_key


class ModuleSource(str, Enum):
    """Where a module definition came from."""

    BUILTIN = "builtin"
    WORKSPACE = "workspace"
    MARKETPLACE = "marketplace"
    GIT = "git"
    LOCAL_PATH = "local_path"


@dataclass(frozen=True)
class ModuleRecord:
    """One discovered module plus installation/source metadata."""

    config: ModuleConfig
    category: str = ""
    source: ModuleSource = ModuleSource.LOCAL_PATH
    source_id: str = ""
    version: str = ""
    path: str = ""
    editable: bool = False
    overrides: str | None = None


class ModuleCatalog(Protocol):
    """Source of module records."""

    def list_records(self) -> list[ModuleRecord]:
        """Return all valid module records from this catalog."""


class FileModuleCatalog:
    """Discover modules from a filesystem tree."""

    def __init__(
        self,
        root: str | Path,
        *,
        source: ModuleSource = ModuleSource.LOCAL_PATH,
        source_id: str = "",
        editable: bool = False,
    ) -> None:
        self.root = Path(root)
        self.source = source
        self.source_id = source_id
        self.editable = editable

    def list_records(self) -> list[ModuleRecord]:
        if not self.root.exists():
            return []

        direct = load_module_config(self.root)
        if direct:
            return [
                ModuleRecord(
                    config=direct,
                    category=self.root.name,
                    source=self.source,
                    source_id=self.source_id,
                    path=str(self.root),
                    editable=self.editable,
                )
            ]

        records: list[ModuleRecord] = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir() and not child.name.startswith("_"):
                records.extend(self._discover_in_category(child, child.name))
        return records

    def _discover_in_category(self, path: Path, category: str) -> list[ModuleRecord]:
        config = load_module_config(path)
        if config:
            return [
                ModuleRecord(
                    config=config,
                    category=category,
                    source=self.source,
                    source_id=self.source_id,
                    path=str(path),
                    editable=self.editable,
                )
            ]

        records: list[ModuleRecord] = []
        for child in sorted(path.iterdir()):
            if child.is_dir() and not child.name.startswith("_"):
                records.extend(self._discover_in_category(child, category))
        return records


class StorageModuleCatalog:
    """Discover modules from a storage-provider prefix.

    This is the local/cloud bridge for user-authored modules. In local mode it
    can point at ``workspaces/default/modules``. In cloud mode a database-backed
    implementation can expose the same records without using this class.
    """

    def __init__(
        self,
        storage: StorageProvider,
        prefix: str,
        *,
        source: ModuleSource = ModuleSource.WORKSPACE,
        source_id: str = "",
        editable: bool = True,
    ) -> None:
        self.storage = storage
        self.prefix = prefix.strip("/")
        self.source = source
        self.source_id = source_id
        self.editable = editable

    def list_records(self) -> list[ModuleRecord]:
        records: list[ModuleRecord] = []
        for key in self.storage.list_files(self.prefix, suffix="module.yaml"):
            try:
                config = load_module_config_from_text(self.storage.read_text(key))
            except Exception:
                continue
            records.append(
                ModuleRecord(
                    config=config,
                    category=self._category_for(key),
                    source=self.source,
                    source_id=self.source_id,
                    path=key,
                    editable=self.editable,
                    overrides=config.name if self._is_override(key) else None,
                )
            )
        return records

    def _category_for(self, key: str) -> str:
        rel = self._relative_parts(key)
        return rel[0] if rel else ""

    def _is_override(self, key: str) -> bool:
        return "overrides" in self._relative_parts(key)

    def _relative_parts(self, key: str) -> tuple[str, ...]:
        prefix = self.prefix + "/" if self.prefix else ""
        rel = key[len(prefix):] if key.startswith(prefix) else key
        return Path(rel).parts


class CompositeModuleCatalog:
    """Compose catalogs in priority order.

    Later catalogs override earlier catalogs by module name. This lets Mesmer
    load packaged builtins first, then workspace-installed/user-authored
    modules, then workspace overrides.
    """

    def __init__(self, *catalogs: ModuleCatalog) -> None:
        self.catalogs = list(catalogs)

    def list_records(self) -> list[ModuleRecord]:
        by_name: dict[str, ModuleRecord] = {}
        order: list[str] = []
        for catalog in self.catalogs:
            for record in catalog.list_records():
                name = record.config.name
                if name not in by_name:
                    order.append(name)
                by_name[name] = record
        return [by_name[name] for name in order]


def workspace_modules_prefix(workspace_id: str = "local") -> str:
    """Storage prefix for user-authored/installed modules in a workspace."""
    from mesmer.core.persistence import workspace_prefix

    return join_storage_key(workspace_prefix(workspace_id), "modules")
