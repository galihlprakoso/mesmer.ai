"""Tests for module catalog composition."""

from __future__ import annotations

from mesmer.core.modules import (
    CompositeModuleCatalog,
    FileModuleCatalog,
    ModuleSource,
    StorageModuleCatalog,
    workspace_modules_prefix,
)
from mesmer.core.persistence import FileStorageProvider
from mesmer.core.registry import Registry
from mesmer.core.runner import build_module_registry


def _module_yaml(name: str, description: str = "desc", tier: int = 2) -> str:
    return "\n".join(
        [
            f"name: {name}",
            f"description: {description}",
            "theory: test theory",
            "system_prompt: test prompt",
            f"tier: {tier}",
            "",
        ]
    )


def test_file_module_catalog_discovers_category(tmp_path):
    module_dir = tmp_path / "techniques" / "custom-technique"
    module_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text(_module_yaml("custom-technique"), encoding="utf-8")

    records = FileModuleCatalog(
        tmp_path,
        source=ModuleSource.BUILTIN,
        source_id="mesmer",
    ).list_records()

    assert len(records) == 1
    assert records[0].config.name == "custom-technique"
    assert records[0].category == "techniques"
    assert records[0].source == ModuleSource.BUILTIN
    assert records[0].editable is False


def test_storage_module_catalog_loads_workspace_modules(tmp_path):
    storage = FileStorageProvider(tmp_path / ".mesmer")
    prefix = workspace_modules_prefix("team-a")
    storage.write_text(
        f"{prefix}/custom/my-module/module.yaml",
        _module_yaml("my-module", description="workspace module"),
    )

    records = StorageModuleCatalog(
        storage,
        prefix,
        source=ModuleSource.WORKSPACE,
        source_id="team-a",
    ).list_records()

    assert len(records) == 1
    assert records[0].config.name == "my-module"
    assert records[0].category == "custom"
    assert records[0].source_id == "team-a"
    assert records[0].editable is True


def test_composite_catalog_later_records_override_earlier_records(tmp_path):
    builtin_dir = tmp_path / "builtin" / "techniques" / "same-name"
    workspace_dir = tmp_path / "workspace" / "overrides" / "same-name"
    builtin_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    (builtin_dir / "module.yaml").write_text(
        _module_yaml("same-name", description="builtin"),
        encoding="utf-8",
    )
    (workspace_dir / "module.yaml").write_text(
        _module_yaml("same-name", description="workspace override", tier=1),
        encoding="utf-8",
    )

    registry = Registry()
    registry.load_catalog(
        CompositeModuleCatalog(
            FileModuleCatalog(builtin_dir.parent.parent, source=ModuleSource.BUILTIN),
            FileModuleCatalog(
                workspace_dir.parent.parent,
                source=ModuleSource.WORKSPACE,
                source_id="team-a",
                editable=True,
            ),
        )
    )

    module = registry.get("same-name")
    assert module is not None
    assert module.description == "workspace override"
    assert module.tier == 1
    info = registry.list_modules()[0]
    assert info["source"] == "workspace"
    assert info["editable"] is True


def test_build_module_registry_includes_workspace_modules(tmp_path, monkeypatch):
    storage_root = tmp_path / ".mesmer"
    storage = FileStorageProvider(storage_root)
    prefix = workspace_modules_prefix("team-a")
    storage.write_text(
        f"{prefix}/custom/team-module/module.yaml",
        _module_yaml("team-module", description="team module"),
    )
    monkeypatch.setattr("mesmer.core.runner.MESMER_HOME", storage_root)

    registry = build_module_registry(workspace_id="team-a")

    assert "team-module" in registry
    info = next(item for item in registry.list_modules() if item["name"] == "team-module")
    assert info["source"] == "workspace"
    assert info["source_id"] == "team-a"
