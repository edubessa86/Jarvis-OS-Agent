# UltimateJarvis — Task Orchestrator and Tool Registry

This increment adds a small, synchronous task state machine and an explicit allowlist for tools. It does not execute shell commands, start subprocesses, or alter the existing desktop application's startup path.

## Task lifecycle

`created → planning → running → succeeded|failed|cancelled`

A task may move to `waiting_approval` before or during execution. Invalid transitions are rejected, and each transition adds a bounded human-readable event. The current task manager is in-memory only; persistent SQLite history is planned separately.

## Tool registry

- Only tools registered by application code are callable.
- Risk level is fixed in the registered `ToolSpec`, not provided by the model.
- Required and allowed argument names are checked before a handler is called.
- Unknown tools and unexpected arguments are denied.
- External side effects require an explicit approval flag supplied by trusted application code.
- The registry is not a complete sandbox: handlers must still validate values, enforce path boundaries, and avoid leaking secrets.

## Tests

```powershell
py -3 -m unittest discover -s tests -p "test_ultimatejarvis_*.py" -v
```

No new third-party packages are required.
