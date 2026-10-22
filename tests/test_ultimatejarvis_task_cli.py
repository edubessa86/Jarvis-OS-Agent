import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ultimatejarvis.__main__ import main


class TaskCliTests(unittest.TestCase):
    def run_cli(self, argv):
        output = io.StringIO()
        with patch.object(sys, "argv", ["ultimatejarvis", *argv]):
            with contextlib.redirect_stdout(output):
                code = main()
        return code, json.loads(output.getvalue()) if output.getvalue().strip() else None

    def test_task_create_transition_show_and_list(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "tasks.sqlite3")
            prefix = ["tasks", "--db", db]
            code, task = self.run_cli(prefix + ["create", "task-1", "Organize my approved workspace"])
            self.assertEqual(code, 0)
            self.assertEqual(task["state"], "created")

            code, task = self.run_cli(prefix + ["transition", "task-1", "planning", "Plan prepared"])
            self.assertEqual(code, 0)
            self.assertEqual(task["state"], "planning")

            code, detail = self.run_cli(prefix + ["show", "task-1"])
            self.assertEqual(code, 0)
            self.assertEqual(detail["task"]["state"], "planning")
            self.assertEqual(len(detail["events"]), 2)

            code, tasks = self.run_cli(prefix + ["list"])
            self.assertEqual(code, 0)
            self.assertEqual([item["task_id"] for item in tasks], ["task-1"])

    def test_task_cli_rejects_invalid_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "tasks.sqlite3")
            prefix = ["tasks", "--db", db]
            self.run_cli(prefix + ["create", "task-2", "Do not execute this automatically"])
            with self.assertRaises(ValueError):
                self.run_cli(prefix + ["transition", "task-2", "succeeded", "Skip states"])


if __name__ == "__main__":
    unittest.main()
