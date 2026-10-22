"""Workspace-only file access with path traversal and symlink escape checks."""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """Raised when a workspace operation is invalid or escapes the workspace."""


class Workspace:
    """A deliberately small file API restricted to one workspace directory.

    This class does not execute files or commands. Callers must separately
    authorize write operations with the policy layer.
    """

    def __init__(self, root: str | Path, *, max_file_bytes: int = 1_000_000) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes

    def resolve(self, relative_path: str | Path) -> Path:
        """Resolve a relative path and reject paths that leave the workspace."""
        supplied = Path(relative_path)
        if supplied.is_absolute():
            raise WorkspaceError("Absolute paths are not allowed.")
        if not supplied.parts or any(part == ".." for part in supplied.parts):
            raise WorkspaceError("Path traversal is not allowed.")

        target = (self.root / supplied).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("Path escapes the workspace.") from exc
        if target == self.root:
            raise WorkspaceError("A file path is required.")
        return target

    def read_text(self, relative_path: str | Path) -> str:
        target = self.resolve(relative_path)
        if not target.is_file():
            raise WorkspaceError("File does not exist.")
        if target.stat().st_size > self.max_file_bytes:
            raise WorkspaceError("File exceeds the configured size limit.")
        return target.read_text(encoding="utf-8")

    def write_text(self, relative_path: str | Path, content: str) -> Path:
        if not isinstance(content, str):
            raise WorkspaceError("Content must be text.")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise WorkspaceError("Content exceeds the configured size limit.")

        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after parent creation to catch a pre-existing symlink.
        target = self.resolve(relative_path)
        target.write_text(content, encoding="utf-8")
        return target

    def list_files(self, relative_dir: str | Path = ".") -> list[str]:
        """List files beneath a workspace directory; never follows symlink dirs."""
        if str(relative_dir) == ".":
            directory = self.root
        else:
            directory = self.resolve(relative_dir)
        if not directory.is_dir():
            raise WorkspaceError("Directory does not exist.")

        results: list[str] = []
        for path in directory.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_file():
                resolved = path.resolve()
                try:
                    resolved.relative_to(self.root)
                except ValueError:
                    continue
                results.append(resolved.relative_to(self.root).as_posix())
        return sorted(results)
