"""Build a restrictive Windows Sandbox (.wsb) configuration without launching it.

A generated configuration is a request to Windows Sandbox, not proof that the
controls are active. This module intentionally does not start processes or run
commands; a future runner must separately verify host support and runtime behavior.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WindowsSandboxConfig:
    """Requested Windows Sandbox settings for a read-only workspace mount."""

    workspace: Path
    memory_limit_mb: int = 2048

    def __post_init__(self) -> None:
        if not 2048 <= self.memory_limit_mb <= 16384:
            raise ValueError("memory_limit_mb must be between 2048 and 16384.")
        workspace = Path(self.workspace)
        if not workspace.is_absolute():
            raise ValueError("workspace must be an absolute path.")
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError("workspace must be an existing directory.")


@dataclass(frozen=True)
class WindowsSandboxHost:
    """Conservative host probe; availability does not verify runtime isolation."""

    is_windows: bool
    sandbox_executable_found: bool

    @property
    def candidate_available(self) -> bool:
        return self.is_windows and self.sandbox_executable_found


def probe_windows_sandbox() -> WindowsSandboxHost:
    """Check only for the Windows host and executable; never claim enforcement."""
    is_windows = sys.platform == "win32"
    root = os.environ.get("SystemRoot")
    executable_found = bool(
        is_windows and root and (Path(root) / "System32" / "WindowsSandbox.exe").is_file()
    )
    return WindowsSandboxHost(
        is_windows=is_windows,
        sandbox_executable_found=executable_found,
    )


def build_wsb_xml(config: WindowsSandboxConfig) -> str:
    """Return restrictive .wsb XML; this function never writes or launches it."""
    root = ET.Element("Configuration")
    ET.SubElement(root, "VGpu").text = "Disable"
    ET.SubElement(root, "Networking").text = "Disable"
    ET.SubElement(root, "MemoryInMB").text = str(config.memory_limit_mb)

    folders = ET.SubElement(root, "MappedFolders")
    mapped = ET.SubElement(folders, "MappedFolder")
    ET.SubElement(mapped, "HostFolder").text = str(config.workspace)
    ET.SubElement(mapped, "SandboxFolder").text = r"C:\UltimateJarvis\workspace"
    ET.SubElement(mapped, "ReadOnly").text = "true"

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    ) + "\n"
