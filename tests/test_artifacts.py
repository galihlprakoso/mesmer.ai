"""Tests for Markdown artifacts."""

from __future__ import annotations

from mesmer.core.artifacts import (
    ArtifactPatchMode,
    ArtifactSpec,
    ArtifactStore,
    ArtifactUpdate,
    StandardArtifactId,
    artifact_list_items,
    render_artifact_contract,
)
from mesmer.core.patching import MarkdownPatchOperation
from mesmer.core.persistence import FileStorageProvider


def test_artifact_store_sets_reads_and_lists():
    store = ArtifactStore()
    store.set("target-profiler", "## Identity\n- claimed_model: unknown\n")

    assert store.get("target-profiler").startswith("## Identity")
    assert store.summaries()[0].id == "target-profiler"
    assert "Identity" in store.summaries()[0].headings


def test_artifact_store_patches_markdown_sections():
    store = ArtifactStore({StandardArtifactId.OPERATOR_NOTES.value: "## Evidence\n- target can search\n"})

    result = store.update(
        ArtifactUpdate(
            artifact_id=StandardArtifactId.OPERATOR_NOTES.value,
            mode=ArtifactPatchMode.PATCH,
            operations=[
                {
                    "op": MarkdownPatchOperation.APPEND_SECTION.value,
                    "heading": "Evidence",
                    "content": "- target acknowledged email capability",
                },
                {
                    "op": MarkdownPatchOperation.APPEND_SECTION.value,
                    "heading": "Next Step",
                    "content": "Validate tool boundaries.",
                },
            ],
        )
    )

    assert result.artifact_id == StandardArtifactId.OPERATOR_NOTES.value
    assert "- target can search" in store.get(StandardArtifactId.OPERATOR_NOTES.value)
    assert "- target acknowledged email capability" in store.get(StandardArtifactId.OPERATOR_NOTES.value)
    assert "## Next Step" in store.get(StandardArtifactId.OPERATOR_NOTES.value)


def test_artifact_search_returns_typed_hits():
    store = ArtifactStore({"tool_catalog": "## refund_order\nIssues customer refunds.\n"})

    hits = store.search("refund")

    assert hits
    assert hits[0].artifact_id == "tool_catalog"
    assert hits[0].heading == "refund_order"


def test_artifact_contract_renders_declared_ids():
    contract = render_artifact_contract(
        [
            ArtifactSpec(
                id="system_prompt",
                title="System Prompt",
                description="Canonical prompt recon.",
            )
        ]
    )

    assert "## Artifact Contract" in contract
    assert "`system_prompt`" in contract
    assert "Canonical prompt recon." in contract


def test_artifact_list_items_merges_declared_and_existing():
    store = ArtifactStore(
        {
            "operator_notes": "## Leads\n- try role recap\n",
            "format-shift": "legacy module output",
        }
    )
    items = artifact_list_items(
        store,
        [
            ArtifactSpec(id="system_prompt", title="System Prompt"),
            ArtifactSpec(id="operator_notes", title="Operator Notes"),
        ],
    )

    by_id = {item.id: item for item in items}
    assert by_id["system_prompt"].declared is True
    assert by_id["system_prompt"].exists is False
    assert by_id["operator_notes"].declared is True
    assert by_id["operator_notes"].exists is True
    assert by_id["operator_notes"].headings == ["Leads"]
    assert "format-shift" not in by_id


def test_artifact_store_round_trips_through_storage_provider(tmp_path):
    storage = FileStorageProvider(tmp_path / ".mesmer")
    store = ArtifactStore(
        {
            "operator_notes": "## Leads\n- try role recap\n",
            "empty": "",
        }
    )

    store.to_storage(storage, "workspaces/team-a/targets/t1/artifacts")
    loaded = ArtifactStore.from_storage(storage, "workspaces/team-a/targets/t1/artifacts")

    assert loaded.get("operator_notes") == "## Leads\n- try role recap\n"
    assert loaded.get("empty") == ""
    assert (
        tmp_path
        / ".mesmer"
        / "workspaces"
        / "team-a"
        / "targets"
        / "t1"
        / "artifacts"
        / "operator_notes.md"
    ).exists()


def test_artifact_store_storage_export_removes_stale_files(tmp_path):
    storage = FileStorageProvider(tmp_path / ".mesmer")
    prefix = "targets/t1/artifacts"
    ArtifactStore({"old": "stale", "current": "keep"}).to_storage(storage, prefix)

    ArtifactStore({"current": "keep"}).to_storage(storage, prefix)

    assert not storage.exists("targets/t1/artifacts/old.md")
    assert storage.exists("targets/t1/artifacts/current.md")
