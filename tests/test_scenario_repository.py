"""Tests for scenario repository abstractions."""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from mesmer.core.persistence import (
    FileScenarioRepository,
    ScenarioConflict,
    ScenarioPathError,
    ScenarioValidationError,
)
from mesmer.interfaces.web.backend.server import create_app


SCENARIO_YAML = "\n".join(
    [
        "name: Repo scenario",
        "description: Repository-backed scenario",
        "target:",
        "  adapter: echo",
        "objective:",
        "  goal: Test repository CRUD",
        "modules: [system-prompt-extraction]",
        "agent:",
        "  model: test/model",
        "  api_key: sk-test",
        "",
    ]
)


def test_file_repository_lists_only_valid_scenarios(tmp_path):
    repo = FileScenarioRepository(tmp_path)
    scenario = tmp_path / "scenarios" / "valid.yaml"
    scenario.parent.mkdir(parents=True)
    scenario.write_text(SCENARIO_YAML, encoding="utf-8")
    module_yaml = tmp_path / "modules" / "not-a-scenario.yaml"
    module_yaml.parent.mkdir()
    module_yaml.write_text("name: module-only\n", encoding="utf-8")

    docs = repo.list()

    assert len(docs) == 1
    assert docs[0].path == str(scenario)
    assert docs[0].summary()["name"] == "Repo scenario"


def test_file_repository_create_private_and_duplicate_detection(tmp_path):
    repo = FileScenarioRepository(tmp_path)

    doc = repo.create_private("Repo Scenario", SCENARIO_YAML)

    assert doc.scenario.name == "Repo scenario"
    assert doc.path.endswith("repo-scenario.yaml")
    assert doc.source == "workspace"
    assert doc.editable is True
    with pytest.raises(ScenarioConflict):
        repo.create_private("Repo Scenario", SCENARIO_YAML)


def test_file_repository_update_rejects_invalid_yaml_without_overwrite(tmp_path):
    repo = FileScenarioRepository(tmp_path)
    doc = repo.create_private("Repo Scenario", SCENARIO_YAML)

    with pytest.raises(ScenarioValidationError):
        repo.update(doc.path, "name: broken\n")

    assert repo.get(doc.path).yaml_content == SCENARIO_YAML


def test_file_repository_rejects_parent_escape(tmp_path):
    repo = FileScenarioRepository(tmp_path)

    with pytest.raises(ScenarioPathError):
        repo.get("../outside.yaml")


def test_file_repository_templates_are_copied_to_workspace_on_update(tmp_path):
    templates = tmp_path / "templates"
    workspace = tmp_path / "workspace"
    scenario = templates / "starter.yaml"
    scenario.parent.mkdir()
    scenario.write_text(SCENARIO_YAML, encoding="utf-8")
    repo = FileScenarioRepository(templates, write_root=workspace)

    original = repo.get(str(scenario))
    updated = repo.update(str(scenario), SCENARIO_YAML.replace("Repo scenario", "Copied"))

    assert original.source == "template"
    assert original.editable is False
    assert updated.source == "workspace"
    assert updated.path == str(workspace / "starter.yaml")
    assert scenario.read_text(encoding="utf-8") == SCENARIO_YAML


def test_web_scenario_crud_uses_repository(tmp_path):
    repo = FileScenarioRepository(tmp_path)
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(SCENARIO_YAML, encoding="utf-8")
    client = TestClient(create_app(scenario_dir=str(tmp_path), scenario_repository=repo))

    listed = client.get("/api/scenarios")
    assert listed.status_code == 200
    assert listed.json()[0]["path"] == str(scenario)

    loaded = client.get(f"/api/scenarios/{quote(str(scenario), safe='')}")
    assert loaded.status_code == 200
    assert loaded.json()["yaml_content"] == SCENARIO_YAML

    created = client.post(
        "/api/scenarios",
        json={"name": "Created Scenario", "yaml_content": SCENARIO_YAML},
    )
    assert created.status_code == 200
    created_path = created.json()["path"]

    updated = client.put(
        f"/api/scenarios/{quote(created_path, safe='')}",
        json={"yaml_content": SCENARIO_YAML.replace("Repo scenario", "Updated")},
    )
    assert updated.status_code == 200
    assert repo.get(created_path).scenario.name == "Updated"
