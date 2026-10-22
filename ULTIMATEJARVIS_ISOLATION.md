# UltimateJarvis — Isolation Policy (Increment 3)

This increment defines a **fail-closed contract**, not an operating-system sandbox.
No code execution is enabled by this module.

## Default profile

- Network access: disabled.
- Workspace: read-only.
- Memory: 512 MB default (allowed profile range 64–4096 MB).
- Wall-clock timeout: 15 seconds default (allowed range 1–300 seconds).
- Captured output: 64 KiB default (allowed range 1 KiB–1 MiB).

The profile rejects requests to enable networking or make the workspace writable.
Resource values are bounded to avoid accidental unbounded execution.

## Gate before any future executor

A trusted platform adapter must configure and verify all of these OS-enforced controls:

1. Separate process/security boundary.
2. Network blocked.
3. Filesystem mounted or ACL-restricted read-only.
4. Memory limit enforced.
5. Wall-clock timeout enforced.
6. Output capture limit enforced.

`require_isolation(None)` and any incomplete capability report raise
`IsolationUnavailableError`. A capability report must come from trusted adapter
code; model output, CLI flags, or user-provided booleans are not proof that a
control is active.

## Important limitation

The current implementation does **not** create a Windows Job Object, AppContainer,
restricted token, container, or VM. It does not launch a child process. This is
intentional: a Python policy object cannot itself guarantee OS isolation. A later
increment must implement and test a real Windows backend (including process
termination and network-denial tests) before any sandboxed execution can be
enabled. Existing allowlisted read-only tools remain the only executable tools.
