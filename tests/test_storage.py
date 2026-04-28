"""Tests for storage provider primitives."""

from __future__ import annotations

import os

import pytest

from mesmer.core.persistence import FileStorageProvider, join_storage_key, workspace_prefix


def test_join_storage_key_skips_empty_segments():
    assert join_storage_key("", "targets", "/abc/", "graph.json") == "targets/abc/graph.json"


def test_workspace_prefix_preserves_local_layout():
    assert workspace_prefix("local") == ""
    assert workspace_prefix("") == ""
    assert workspace_prefix("team-a") == "workspaces/team-a"


def test_workspace_prefix_rejects_path_separators():
    with pytest.raises(ValueError):
        workspace_prefix("../team-a")
    with pytest.raises(ValueError):
        workspace_prefix("team/a")


def test_file_storage_rejects_absolute_and_parent_keys(tmp_path):
    storage = FileStorageProvider(tmp_path)

    with pytest.raises(ValueError):
        storage.write_text("/absolute/path", "nope")
    with pytest.raises(ValueError):
        storage.write_text("../escape", "nope")


def test_file_storage_atomic_write_keeps_previous_content_on_replace_failure(tmp_path, monkeypatch):
    storage = FileStorageProvider(tmp_path)
    storage.write_text("targets/t1/profile.md", "first", atomic=True)

    def fail_replace(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        storage.write_text("targets/t1/profile.md", "second", atomic=True)

    assert storage.read_text("targets/t1/profile.md") == "first"
    leftovers = list((tmp_path / "targets" / "t1").glob("profile.md.*.tmp"))
    assert leftovers == []
