import tempfile
import unittest
from pathlib import Path

from ultimatejarvis.orchestrator import TaskState
from ultimatejarvis.storage import SQLiteTaskStore


class SQLiteTaskStoreTests(unittest.TestCase):
    def test_task_and_events_persist_across_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "tasks.sqlite3"
            with SQLiteTaskStore(db) as store:
                task = store.create("persist-1", "organize notes")
                self.assertEqual(task["state"], TaskState.CREATED.value)
                store.transition("persist-1", TaskState.PLANNING, "Planning")
                store.transition("persist-1", TaskState.RUNNING, "Running")
                store.transition("persist-1", TaskState.SUCCEEDED, "Done")
                self.assertEqual(len(store.events("persist-1")), 4)
            with SQLiteTaskStore(db) as reopened:
                task = reopened.get("persist-1")
                self.assertEqual(task["state"], TaskState.SUCCEEDED.value)
                self.assertEqual(len(reopened.events("persist-1")), 4)

    def test_duplicate_id_and_invalid_transition_are_rejected(self):
        with SQLiteTaskStore(":memory:") as store:
            store.create("task", "prompt")
            with self.assertRaises(ValueError):
                store.create("task", "duplicate")
            with self.assertRaises(ValueError):
                store.transition("task", TaskState.SUCCEEDED, "Skip required states")

    def test_failed_state_records_error(self):
        with SQLiteTaskStore(":memory:") as store:
            store.create("task", "prompt")
            store.transition("task", TaskState.PLANNING, "Planning")
            task = store.transition("task", TaskState.FAILED, "Failed", error="provider unavailable")
            self.assertEqual(task["error"], "provider unavailable")
            self.assertEqual(store.events("task")[-1]["message"], "Failed")

    def test_prompt_limit_and_list_limit(self):
        with SQLiteTaskStore(":memory:") as store:
            with self.assertRaises(ValueError):
                store.create("large", "12345", max_prompt_chars=4)
            store.create("ok", "prompt")
            with self.assertRaises(ValueError):
                store.list_tasks(limit=0)
            self.assertEqual(len(store.list_tasks(limit=10)), 1)


if __name__ == "__main__":
    unittest.main()
