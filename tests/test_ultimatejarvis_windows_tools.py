import tempfile
import unittest
from pathlib import Path

from ultimatejarvis.policy import Decision
from ultimatejarvis.windows_tools import build_read_only_registry


class ReadOnlyWindowsToolsTests(unittest.TestCase):
    def test_registry_exposes_only_expected_read_only_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = build_read_only_registry(temp)
            descriptions = registry.describe()
            self.assertEqual(
                {item["name"] for item in descriptions},
                {"system.diagnostics", "workspace.list_files", "workspace.read_text"},
            )
            self.assertTrue(all(item["risk"] == "read_only" for item in descriptions))

    def test_workspace_list_and_read_stay_inside_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            (root / "notes.txt").write_text("hello Jarvis", encoding="utf-8")
            registry = build_read_only_registry(root)

            listed = registry.invoke("workspace.list_files", {})
            self.assertEqual(listed.decision, Decision.ALLOW)
            self.assertEqual(listed.value, ["notes.txt"])

            read = registry.invoke("workspace.read_text", {"relative_path": "notes.txt"})
            self.assertEqual(read.decision, Decision.ALLOW)
            self.assertEqual(read.value["content"], "hello Jarvis")

    def test_unknown_tool_and_path_escape_are_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            outside = Path(temp) / "secret.txt"
            outside.write_text("not for Jarvis", encoding="utf-8")
            registry = build_read_only_registry(root)

            unknown = registry.invoke("system.run_shell", {"command": "whoami"})
            self.assertEqual(unknown.decision, Decision.DENY)

            escaped = registry.invoke("workspace.read_text", {"relative_path": "../secret.txt"})
            self.assertEqual(escaped.decision, Decision.DENY)
            self.assertNotIn("not for Jarvis", str(escaped.value))

    def test_unexpected_arguments_are_rejected_before_handler(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = build_read_only_registry(temp)
            result = registry.invoke("system.diagnostics", {"path": "C:/Windows"})
            self.assertEqual(result.decision, Decision.DENY)


if __name__ == "__main__":
    unittest.main()
