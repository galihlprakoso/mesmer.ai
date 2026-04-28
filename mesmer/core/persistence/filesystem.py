"""Filesystem-backed storage provider."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FileStorageProvider:
    """Filesystem implementation of :class:`StorageProvider`.

    Keys are POSIX-style paths relative to ``root``. Absolute paths and parent
    traversal are rejected so higher layers can safely accept route params or
    workspace ids before mapping them to storage keys.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()

    def resolve(self, key: str) -> Path:
        path = Path(str(key))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe storage key: {key!r}")
        return self.root.joinpath(path)

    def exists(self, key: str) -> bool:
        return self.resolve(key).exists()

    def read_text(self, key: str) -> str:
        return self.resolve(key).read_text(encoding="utf-8")

    def write_text(self, key: str, data: str, *, atomic: bool = False) -> None:
        path = self.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not atomic:
            path.write_text(data, encoding="utf-8")
            return

        fd, tmp_path = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp = Path(tmp_path)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

    def append_text(self, key: str, data: str) -> None:
        path = self.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(data)

    def delete(self, key: str, *, missing_ok: bool = True) -> None:
        self.resolve(key).unlink(missing_ok=missing_ok)

    def list_files(self, prefix: str, *, suffix: str = "") -> list[str]:
        base = self.resolve(prefix)
        if not base.exists():
            return []
        files = [p for p in base.rglob("*") if p.is_file() and p.name.endswith(suffix)]
        return sorted(p.relative_to(self.root).as_posix() for p in files)

    def list_dirs(self, prefix: str) -> list[str]:
        base = self.resolve(prefix)
        if not base.exists():
            return []
        dirs = [p for p in base.iterdir() if p.is_dir()]
        return sorted(p.relative_to(self.root).as_posix() for p in dirs)

    def modified_at(self, key: str) -> float:
        return self.resolve(key).stat().st_mtime
