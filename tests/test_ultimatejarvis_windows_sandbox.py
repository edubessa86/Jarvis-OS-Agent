from __future__ import annotations

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from ultimatejarvis.windows_sandbox import (
    WindowsSandboxConfig,
    build_wsb_xml,
    probe_windows_sandbox,
)


class WindowsSandboxConfigTests(unittest.TestCase):
    def test_configuration_disables_network_and_gpu_and_mounts_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jarvis & workspace ") as directory:
            config = WindowsSandboxConfig(Path(directory), memory_limit_mb=2048)
            xml = build_wsb_xml(config)
            root = ET.fromstring(xml)
            self.assertEqual(root.findtext("Networking"), "Disable")
            self.assertEqual(root.findtext("VGpu"), "Disable")
            self.assertEqual(root.findtext("MemoryInMB"), "2048")
            mapped = root.find("./MappedFolders/MappedFolder")
            self.assertIsNotNone(mapped)
            self.assertEqual(mapped.findtext("HostFolder"), directory)
            self.assertEqual(mapped.findtext("SandboxFolder"), r"C:\UltimateJarvis\workspace")
            self.assertEqual(mapped.findtext("ReadOnly"), "true")

    def test_memory_bounds_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for memory in (0, 1024, 16385):
                with self.subTest(memory=memory):
                    with self.assertRaises(ValueError):
                        WindowsSandboxConfig(Path(directory), memory_limit_mb=memory)

    def test_workspace_must_be_absolute_existing_directory(self) -> None:
        with self.assertRaises(ValueError):
            WindowsSandboxConfig(Path("relative-folder"))
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaises(ValueError):
                WindowsSandboxConfig(missing)

    def test_probe_never_claims_candidate_available_off_windows(self) -> None:
        with patch("ultimatejarvis.windows_sandbox.sys.platform", "linux"):
            with patch.dict(os.environ, {"SystemRoot": ""}):
                result = probe_windows_sandbox()
        self.assertFalse(result.is_windows)
        self.assertFalse(result.sandbox_executable_found)
        self.assertFalse(result.candidate_available)


if __name__ == "__main__":
    unittest.main()
