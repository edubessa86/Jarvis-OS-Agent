from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from ultimatejarvis.sandbox_qualification import (
    SandboxQualificationError,
    _powershell_script,
    run_sandbox_qualification,
)
from ultimatejarvis.windows_sandbox import build_qualification_wsb_xml


class SandboxQualificationTests(unittest.TestCase):
    def test_wsb_disables_network_gpu_and_common_redirections(self):
        with tempfile.TemporaryDirectory() as root:
            paths = [Path(root) / name for name in ("workspace", "control", "output")]
            for path in paths:
                path.mkdir()
            xml = build_qualification_wsb_xml(*paths, memory_limit_mb=2048)
            node = ET.fromstring(xml)
            self.assertEqual(node.findtext("Networking"), "Disable")
            self.assertEqual(node.findtext("VGpu"), "Disable")
            self.assertEqual(node.findtext("ClipboardRedirection"), "Disable")
            self.assertEqual(node.findtext("PrinterRedirection"), "Disable")
            self.assertEqual(node.findtext("MemoryInMB"), "2048")
            mapped = node.findall("./MappedFolders/MappedFolder")
            self.assertEqual(len(mapped), 3)
            self.assertEqual([m.findtext("ReadOnly") for m in mapped], ["true", "true", "false"])
            self.assertIn("qualify.ps1", node.findtext("./LogonCommand/Command"))
            self.assertEqual(node.findtext("./MappedFolders/MappedFolder[1]/SandboxFolder"),
                             r"C:\UltimateJarvis\workspace")

    def test_mapped_paths_must_be_distinct_absolute_existing_directories(self):
        with tempfile.TemporaryDirectory() as root:
            a, b = Path(root) / "a", Path(root) / "b"
            a.mkdir()
            b.mkdir()
            with self.assertRaises(ValueError):
                build_qualification_wsb_xml(a, a, b)
            with self.assertRaises(ValueError):
                build_qualification_wsb_xml(a, b, Path(root) / "missing")

    def test_probe_script_is_fixed_and_reports_only_bounded_checks(self):
        script = _powershell_script(2048)
        self.assertIn("workspace_write_blocked", script)
        self.assertIn("external_tcp_probe_blocked", script)
        self.assertIn("2048", script)
        with self.assertRaises(ValueError):
            _powershell_script(1024)

    def test_qualification_fails_closed_when_sandbox_is_unavailable(self):
        with patch("ultimatejarvis.sandbox_qualification.probe_windows_sandbox") as probe:
            probe.return_value.candidate_available = False
            with self.assertRaises(SandboxQualificationError):
                run_sandbox_qualification()

    def test_timeout_and_memory_bounds(self):
        with self.assertRaises(ValueError):
            run_sandbox_qualification(timeout_seconds=1)
        with self.assertRaises(ValueError):
            run_sandbox_qualification(memory_limit_mb=1024)


if __name__ == "__main__":
    unittest.main()
