# UltimateJarvis — Windows Sandbox configuration (Increment 4)

This increment adds a restrictive Windows Sandbox configuration builder. It does
**not** launch Windows Sandbox, execute commands, or enable arbitrary code execution.

## Configuration generated

- Networking disabled.
- Virtual GPU disabled.
- The configured workspace is mapped into the sandbox as read-only.
- Sandbox memory is requested at a bounded value (2,048–16,384 MB; default 2,048 MB).
- XML is generated with the standard library and correctly escapes host paths.

Example (Python):

```python
from pathlib import Path
from ultimatejarvis.windows_sandbox import WindowsSandboxConfig, build_wsb_xml

config = WindowsSandboxConfig(Path(r"C:\Users\me\UltimateJarvisWorkspace"))
print(build_wsb_xml(config))
```

Save the output as a `.wsb` file only after reviewing it. This stage deliberately
does not create a file or launch `WindowsSandbox.exe`.

## Important limits

- A generated configuration is not proof that Windows Sandbox is installed,
  enabled, or enforcing the requested settings.
- The host probe checks only whether Windows and the expected executable path
  are present; it is not a security attestation.
- Windows Sandbox availability depends on Windows edition, optional-feature
  configuration, virtualization, and host policy. The probe does not verify all
  prerequisites.
- Wall-clock timeout and output-byte limits are not provided by this configuration
  builder. A future supervised runner must enforce them and test process cleanup.
- No sandbox execution is wired into the existing `run` command. Existing
  allowlisted read-only tools and per-step approvals remain unchanged.

Before enabling execution, a later increment must implement the supervised
launch/cleanup path and validate network denial, read-only host mapping, resource
limits, timeout, output truncation, and failure behavior on supported Windows
hosts. If any required control cannot be verified, execution must remain disabled.
