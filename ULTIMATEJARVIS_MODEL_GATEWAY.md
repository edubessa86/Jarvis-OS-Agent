# UltimateJarvis — Model Gateway and Diagnostics

This increment adds a dependency-light model interface and a local Ollama adapter. It does not change the legacy application startup or add any shell/code execution capability.

## Run diagnostics on Windows

From the repository root in PowerShell:

```powershell
py -3 -m ultimatejarvis diagnostics
```

To include available disk space for an existing workspace directory:

```powershell
py -3 -m ultimatejarvis diagnostics --workspace "$env:USERPROFILE\UltimateJarvisWorkspace"
```

The report includes OS, architecture, Python version, logical CPU count and RAM where available. GPU detection is intentionally not attempted yet; the report says `not probed` instead of guessing.

## Optional local Ollama smoke test

Install and start Ollama separately, then pull a model suitable for your memory budget. For a machine with 8 GB RAM, start with a small quantized model and avoid loading several models concurrently.

```powershell
py -3 -m ultimatejarvis diagnostics --ollama-model "qwen2.5:3b"
```

This only sends a short prompt to the loopback Ollama endpoint at `127.0.0.1:11434`. The adapter rejects non-loopback URLs. No remote provider is enabled by default.

## Gateway contract

- Local providers are tried before providers marked remote.
- Prompt length, output-token count and provider timeout are bounded.
- Provider failures are summarized without crashing the orchestrator.
- Remote fallback can be added later through an explicitly configured provider adapter; it must never silently transmit private workspace files or secrets.
- The gateway generates text only. It does not execute model output.

## Tests

```powershell
py -3 -m unittest discover -s tests -p "test_ultimatejarvis_*.py" -v
```

The diagnostics are informational and lightweight, not a security boundary or a complete hardware benchmark. The workspace API is application-level path confinement, not OS-level sandboxing.
