"""Storage provider contracts."""

from __future__ import annotations

from typing import Protocol


class StorageProvider(Protocol):
    """Minimal text/blob storage contract used by runtime persistence."""

    def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""

    def read_text(self, key: str) -> str:
        """Read a UTF-8 text object."""

    def write_text(self, key: str, data: str, *, atomic: bool = False) -> None:
        """Write a UTF-8 text object."""

    def append_text(self, key: str, data: str) -> None:
        """Append UTF-8 text to an object, creating it if needed."""

    def delete(self, key: str, *, missing_ok: bool = True) -> None:
        """Delete an object."""

    def list_files(self, prefix: str, *, suffix: str = "") -> list[str]:
        """List file keys beneath ``prefix``."""

    def list_dirs(self, prefix: str) -> list[str]:
        """List immediate directory keys beneath ``prefix``."""

    def modified_at(self, key: str) -> float:
        """Return modification time for ordering."""


def workspace_prefix(workspace_id: str) -> str:
    """Return the storage prefix for a workspace.

    ``local`` preserves Mesmer's original on-disk layout under ``~/.mesmer``.
    Non-local workspace ids opt into an explicit namespace, which gives the
    hosted/cloud path a natural tenant boundary later.
    """
    workspace_id = (workspace_id or "local").strip()
    if workspace_id == "local":
        return ""
    if "/" in workspace_id or "\\" in workspace_id or workspace_id in {".", ".."}:
        raise ValueError(f"Unsafe workspace id: {workspace_id!r}")
    return f"workspaces/{workspace_id}"


def join_storage_key(*parts: str) -> str:
    """Join storage key fragments with POSIX separators."""
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))
