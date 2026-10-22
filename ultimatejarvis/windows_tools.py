"""Explicit, read-only Windows inspection tools for UltimateJarvis.

These tools expose bounded diagnostics and workspace-only text access. They do
not inspect arbitrary host paths, launch programs, access the network, or mutate
files. Workspace path confinement remains application-level, not an OS sandbox.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .diagnostics import collect_diagnostics
from .orchestrator import ToolRegistry, ToolSpec
from .policy import Risk
from .workspace import Workspace


def build_read_only_registry(workspace_root: str | Path) -> ToolRegistry:
    """Build a small registry whose capabilities are fixed by trusted code."""
    workspace = Workspace(workspace_root)
    registry = ToolRegistry()

    def system_diagnostics(_: Mapping[str, Any]) -> dict[str, Any]:
        return collect_diagnostics()

    def workspace_list(arguments: Mapping[str, Any]) -> list[str]:
        relative_dir = arguments.get("relative_dir", ".")
        return workspace.list_files(relative_dir)

    def workspace_read(arguments: Mapping[str, Any]) -> dict[str, str]:
        relative_path = arguments["relative_path"]
        return {
            "path": str(relative_path),
            "content": workspace.read_text(relative_path),
        }

    registry.register(ToolSpec(
        name="system.diagnostics",
        risk=Risk.READ_ONLY,
        handler=system_diagnostics,
        allowed_arguments=frozenset(),
        description="Read basic OS, CPU, RAM and Python diagnostics.",
    ))
    registry.register(ToolSpec(
        name="workspace.list_files",
        risk=Risk.READ_ONLY,
        handler=workspace_list,
        allowed_arguments=frozenset({"relative_dir"}),
        description="List files only inside the configured workspace.",
    ))
    registry.register(ToolSpec(
        name="workspace.read_text",
        risk=Risk.READ_ONLY,
        handler=workspace_read,
        required_arguments=frozenset({"relative_path"}),
        allowed_arguments=frozenset({"relative_path"}),
        description="Read a bounded UTF-8 text file inside the configured workspace.",
    ))
    return registry
