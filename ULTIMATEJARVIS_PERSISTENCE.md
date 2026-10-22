# UltimateJarvis — Local Task Persistence

This increment adds an optional SQLite store for task records and an append-only state-event history. It uses Python's standard library and does not add a network service.

## Example

```python
from ultimatejarvis.orchestrator import TaskState
from ultimatejarvis.storage import SQLiteTaskStore

with SQLiteTaskStore(r"C:\Users\Public\UltimateJarvis\tasks.sqlite3") as store:
    store.create("task-001", "Summarize files in my approved workspace")
    store.transition("task-001", TaskState.PLANNING, "Plan prepared")
    store.transition("task-001", TaskState.RUNNING, "Execution started")
    store.transition("task-001", TaskState.SUCCEEDED, "Completed")
    print(store.events("task-001"))
```

Use a private directory owned by the current Windows user rather than a shared public directory in a real installation. Task prompts are persisted as plain text; do not place passwords, API keys, or other secrets in them. Database encryption and retention controls are not included in this increment.

The store validates task transitions, uses SQL parameters, enables foreign keys, and records each transition in the same transaction as the task-state update. It is not yet wired into the legacy desktop UI or the in-memory task manager.

## Tests

```powershell
py -3 -m unittest discover -s tests -p "test_ultimatejarvis_*.py" -v
```
