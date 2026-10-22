# UltimateJarvis — Windows Sandbox qualification probe

This increment adds a **fixed diagnostic smoke test**, not a general code runner.
It never accepts a script or command from the user or model. The only script it
runs is a repository-owned qualification probe.

## Run on the target Windows PC

From the repository root, with Python 3.11+:

\`\`\`powershell
python -m ultimatejarvis sandbox-qualify
\`\`\`

Optional bounds:

\`\`\`powershell
python -m ultimatejarvis sandbox-qualify --timeout-seconds 45 --memory-limit-mb 2048
\`\`\`

Windows Sandbox must be installed/enabled, virtualization supported, and
\`WindowsSandbox.exe\` available. The command fails closed if the executable is
not found. It launches a disposable sandbox with networking, vGPU, clipboard,
printer, audio and video redirection disabled. Only a temporary workspace and
fixed control script are mounted read-only; only a temporary results folder is
writable. The temporary directories are removed after the sandbox is stopped.

## What the result means

- \`workspace_write_blocked\`: guest could not write to the read-only mapped folder.
- \`output_write_succeeded\`: guest could write its small fixed result file.
- \`external_tcp_probe_blocked\`: one outbound TCP probe to 1.1.1.1:443 did not connect.
- \`memory_limit_requested_mb\`: memory value requested in the WSB config, **not independently measured**.
- \`all_probes_passed\`: all three smoke checks returned true.
- \`isolation_attestation\`: always false. This is intentionally not a security attestation.

A blocked TCP probe does not prove that every possible network path is blocked.
The host timeout supervises the qualification process; it does not yet provide a
general-purpose, per-task execution limit. This command does not enable arbitrary
code execution and is not wired into \`run\`. The existing allowlisted read-only
tools and explicit approval path remain the only task execution path until a
separate executor can enforce and verify every required isolation capability.
