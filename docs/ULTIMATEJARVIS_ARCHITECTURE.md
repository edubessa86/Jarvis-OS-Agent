# UltimateJarvis — Architecture Proposal

## Goals

UltimateJarvis extends Jarvis-OS-Agent without replacing its current application in one risky rewrite. The target is Windows 10/11, 8 GB RAM, integrated graphics, local-first hybrid AI, and broad autonomy only inside a constrained sandbox.

## Repository strategy

- Keep `main` unchanged; development happens on `feature/ultimatejarvis-foundation`.
- Keep the current Jarvis-OS-Agent application operational while adding a separately namespaced `ultimatejarvis` package.
- Prefer adapters around existing components over copying or tightly coupling whole projects.
- Review licenses, dependencies, security, and actual test coverage before importing code from other repositories.

## Research findings

### Jarvis-OS-Agent (this repository)

The current tree already contains Windows UI automation, system tools, a command executor, provider adapters, memory/RAG, voice, proactive listeners, Telegram, a mobile bridge, and a Qt UI. Its `requirements.txt` includes large ML/audio/UI dependencies. The first enhancement should therefore avoid loading those dependencies in the new safety core and avoid importing legacy modules at startup.

### OpenJarvis

OpenJarvis is an Apache-2.0 local-first framework with a CLI, GUI/API, built-in on-demand/scheduled/continuous agents, skills, model engines, memory, evaluation, and Windows installation support. Its primitives and local-first design are strong references. Integrate it through an optional adapter or separate process only after a compatibility and license review; do not merge the entire codebase into this repository.

### Personal Jarvis

Useful product reference for desktop interaction, multiple coding agents, voice, routines, and a plugin/skill experience. Treat as a feature reference rather than a mandatory runtime dependency.

### Open Interpreter

Useful reference for natural-language computer use and code execution. Any execution path must be placed behind the same policy and sandbox boundary; never give an LLM unrestricted host shell access by default.

## Target architecture

1. **Interaction:** existing Qt desktop UI initially; later local web UI, voice, hotkey, Telegram and mobile clients.
2. **Orchestrator:** a small task lifecycle API that plans, selects tools, records results, and supports cancellation.
3. **Model gateway:** provider-neutral interface; local provider first (Ollama/llama.cpp where compatible), cloud fallback only when configured and appropriate.
4. **Tool registry:** explicit schemas, capability declarations, timeouts, output limits, and per-tool policy.
5. **Sandbox:** dedicated workspace, path confinement, subprocess/container isolation when code execution is introduced, resource/time limits, no host secrets, no unrestricted network.
6. **Memory:** start with SQLite and full-text search; add embeddings/vector retrieval only as an optional feature so 8 GB machines are not forced to load large models.
7. **Scheduler and events:** persistent jobs, retries with limits, idempotency keys, and a visible activity log.
8. **Observability:** structured logs, task traces, latency/cost metrics, health diagnostics.
9. **Security:** deny-by-default for unknown tools; ask before external side effects; sandbox autonomy cannot imply permission to send messages, publish content, delete user data, or spend money.

## Hardware profile

Target: Windows 10/11, 8 GB RAM, integrated graphics.

- Keep the core dependency-light and importable without Torch, Transformers, PyTorch audio, or a vector database.
- Do not assume local LLM inference is fast enough; use a small quantized model when available and a configurable cloud fallback.
- Load speech, OCR, embeddings, and desktop automation on demand.
- Keep model/context size and concurrent agents configurable; default to one active agent on this hardware.

## First increment: safety foundation

This branch introduces a dependency-free policy decision module and a workspace-only file API. It intentionally does not execute shell commands, launch apps, access arbitrary host paths, or make network calls. The module is independently testable with Python's standard library.

Acceptance criteria:
- All file operations are restricted to a configured workspace.
- Path traversal and symlink escapes are rejected.
- Unknown or externally impactful actions require approval by default.
- Tests run with `python -m unittest discover -s tests`.
- Existing application entry points remain untouched.

## Planned phases

1. **Foundation (this branch):** architecture record, action policy, workspace confinement, standard-library tests.
2. **Model gateway:** common request/response protocol, local provider adapter, optional cloud fallback, timeout and budget controls.
3. **Task orchestrator:** task state machine, tool registry, bounded retries, trace events, cancellation.
4. **Windows tools:** read-only diagnostics first; controlled app/file operations; shell and GUI interaction behind explicit capability grants.
5. **Memory and retrieval:** SQLite conversation/task memory, then optional local semantic index.
6. **Voice and remote clients:** load-on-demand STT/TTS, wake word, Telegram/mobile access with authentication.
7. **Autonomy:** scheduler and proactive agents with allowlists, per-task sandbox, approval gates, and audit log.
8. **Desktop integration:** integrate with the existing UI only after the core APIs and tests stabilize.

## Safety model

- **Read-only:** can inspect only explicitly allowed workspace resources.
- **Workspace write:** can create/edit files inside the workspace.
- **Sandbox execution:** future capability requiring an isolated runtime, strict time/resource limits, and no ambient host credentials.
- **External side effect:** email, messages, purchases, publishing, account changes, destructive file operations; approval required unless a narrowly scoped rule explicitly allows it.
- **Denied:** path escape, unknown capability, secret access, privilege escalation, or execution outside approved boundaries.

An LLM's plan is untrusted input. Tool authorization is enforced in code and is not delegated to the model.
