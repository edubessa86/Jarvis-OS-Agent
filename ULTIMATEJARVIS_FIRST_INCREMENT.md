# UltimateJarvis — First Increment

This increment is intentionally additive and does not change `main` or the existing `main.py` startup path.

## Included

- `ultimatejarvis.policy`: a small deny-by-default authorization decision function.
- `ultimatejarvis.workspace`: text file operations confined to one workspace, with path traversal, symlink escape, and file-size checks.
- Standard-library tests. No extra packages are needed for these modules.

## Run tests on Windows

From the repository root in PowerShell:

```powershell
py -3 -m unittest discover -s tests -p "test_ultimatejarvis_*.py" -v
```

## Important limitation

This is a foundation, not a complete operating-system sandbox. It deliberately does not run shell commands or Python code. Before adding execution, implement and test a separate isolated runner with timeouts, CPU/memory limits, restricted filesystem mounts, network disabled by default, no inherited secrets, and auditable approval grants.

## Next increment

Add a model gateway interface and a minimal local health/diagnostics command. The model adapter should be optional and lazy-loaded so an 8 GB Windows computer can run the core without loading large ML dependencies.
