import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ultimatejarvis.__main__ import main
from ultimatejarvis.sandbox_qualification import SandboxQualificationError


class CliTests(unittest.TestCase):
    def test_sandbox_qualification_cli_reports_unavailable_host(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["ultimatejarvis", "sandbox-qualify"]):
            with patch("ultimatejarvis.sandbox_qualification.run_sandbox_qualification",
                       side_effect=SandboxQualificationError("Windows Sandbox unavailable")):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"], "SandboxQualificationError")

    def test_sandbox_qualification_cli_returns_success_for_passing_smoke_test(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["ultimatejarvis", "sandbox-qualify"]):
            with patch("ultimatejarvis.sandbox_qualification.run_sandbox_qualification",
                       return_value={"all_probes_passed": True, "isolation_attestation": False}):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["ok"])
        self.assertFalse(report["result"]["isolation_attestation"])

    def test_memory_add_and_search_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "memory.sqlite3")
            output = io.StringIO()
            with patch.object(sys, "argv", ["ultimatejarvis", "memory", "--db", db, "add",
                                            "Use local models", "--category", "preference", "--tag", "privacy"]):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 0)
            saved = json.loads(output.getvalue())
            self.assertEqual(saved["content"], "Use local models")
            output = io.StringIO()
            with patch.object(sys, "argv", ["ultimatejarvis", "memory", "--db", db, "search", "local"]):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 0)
            found = json.loads(output.getvalue())
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["memory_id"], saved["memory_id"])


if __name__ == "__main__":
    unittest.main()
