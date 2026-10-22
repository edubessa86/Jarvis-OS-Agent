import tempfile
import unittest
from pathlib import Path

from ultimatejarvis.workspace import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.workspace = Workspace(self.root, max_file_bytes=64)

    def test_write_read_and_list_file(self):
        self.workspace.write_text("notes/hello.txt", "Olá Jarvis")
        self.assertEqual(self.workspace.read_text("notes/hello.txt"), "Olá Jarvis")
        self.assertEqual(self.workspace.list_files(), ["notes/hello.txt"])

    def test_reject_parent_traversal(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.write_text("../outside.txt", "no")

    def test_reject_absolute_path(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.read_text(self.root / "file.txt")

    def test_reject_oversized_content(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.write_text("big.txt", "x" * 65)

    def test_reject_symlink_escape(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable in this environment")
        with self.assertRaises(WorkspaceError):
            self.workspace.write_text("escape/pwned.txt", "no")


if __name__ == "__main__":
    unittest.main()
