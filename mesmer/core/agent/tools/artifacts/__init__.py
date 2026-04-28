"""Artifact tools grouped under ``tools/artifacts``."""

from mesmer.core.agent.tools.artifacts import (
    list_artifacts,
    read_artifact,
    search_artifacts,
    update_artifact,
)

HANDLERS = {
    list_artifacts.NAME: list_artifacts.handle,
    read_artifact.NAME: read_artifact.handle,
    search_artifacts.NAME: search_artifacts.handle,
    update_artifact.NAME: update_artifact.handle,
}

SCHEMAS = {
    list_artifacts.NAME.value: list_artifacts.SCHEMA,
    read_artifact.NAME.value: read_artifact.SCHEMA,
    search_artifacts.NAME.value: search_artifacts.SCHEMA,
    update_artifact.NAME.value: update_artifact.SCHEMA,
}

__all__ = [
    "HANDLERS",
    "SCHEMAS",
    "list_artifacts",
    "read_artifact",
    "search_artifacts",
    "update_artifact",
]
