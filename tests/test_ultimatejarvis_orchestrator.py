import unittest

from ultimatejarvis.orchestrator import (
    TaskManager, TaskState, TaskStateError, ToolRegistry, ToolSpec
)
from ultimatejarvis.policy import Decision, Risk


class OrchestratorTests(unittest.TestCase):
    def test_task_lifecycle_and_terminal_state(self):
        manager = TaskManager()
        task = manager.create("task-1", "summarize notes")
        self.assertEqual(task.state, TaskState.CREATED)
        manager.transition("task-1", TaskState.PLANNING, "Planning")
        manager.transition("task-1", TaskState.RUNNING, "Running")
        manager.transition("task-1", TaskState.SUCCEEDED, "Done")
        self.assertEqual(len(task.events), 4)
        with self.assertRaises(TaskStateError):
            manager.transition("task-1", TaskState.RUNNING, "Cannot restart terminal task")

    def test_cancel_created_task(self):
        manager = TaskManager()
        task = manager.create("task-2", "do work")
        manager.cancel(task.task_id)
        self.assertEqual(task.state, TaskState.CANCELLED)

    def test_reject_duplicate_or_invalid_task_id(self):
        manager = TaskManager()
        manager.create("same", "first")
        with self.assertRaises(ValueError):
            manager.create("same", "second")
        with self.assertRaises(ValueError):
            manager.create("../bad", "bad")

    def test_unknown_tool_is_denied(self):
        result = ToolRegistry().invoke("shell.run", {})
        self.assertEqual(result.decision, Decision.DENY)

    def test_tool_arguments_are_allowlisted(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="notes.count",
            risk=Risk.READ_ONLY,
            handler=lambda args: len(args["text"]),
            required_arguments=frozenset({"text"}),
            allowed_arguments=frozenset({"text"}),
            description="Count characters in supplied text",
        ))
        good = registry.invoke("notes.count", {"text": "hello"})
        self.assertEqual(good.decision, Decision.ALLOW)
        self.assertEqual(good.value, 5)
        bad = registry.invoke("notes.count", {"text": "hello", "path": "C:/secret"})
        self.assertEqual(bad.decision, Decision.DENY)

    def test_external_tool_requires_approval_before_handler_runs(self):
        calls = []
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="message.send",
            risk=Risk.EXTERNAL_SIDE_EFFECT,
            handler=lambda args: calls.append(args) or "sent",
            required_arguments=frozenset({"text"}),
            allowed_arguments=frozenset({"text"}),
        ))
        result = registry.invoke("message.send", {"text": "hello"})
        self.assertEqual(result.decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(calls, [])
        approved = registry.invoke("message.send", {"text": "hello"}, approved=True)
        self.assertEqual(approved.decision, Decision.ALLOW)
        self.assertEqual(calls, [{"text": "hello"}])


if __name__ == "__main__":
    unittest.main()
