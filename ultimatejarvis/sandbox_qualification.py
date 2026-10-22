"""Run a fixed, non-user-code Windows Sandbox qualification probe.

This diagnostic does not prove every property of Windows Sandbox. It checks a
read-only mapped folder, a writable result folder, and one outbound TCP probe;
the configured memory value is reported as requested, not independently measured.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import tempfile
from pathlib import Path
from typing import Any

from .windows_sandbox import build_qualification_wsb_xml, probe_windows_sandbox

_QUALIFICATION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
  schema = 1
  workspace_write_blocked = $false
  output_write_succeeded = $false
  external_tcp_probe_blocked = $false
  memory_limit_requested_mb = __MEMORY_MB__
  probe_type = 'fixed-qualification-only'
}
try {
  $probePath = Join-Path 'C:\UltimateJarvis\workspace' 'uj-qualification-write-probe.tmp'
  try {
    [System.IO.File]::WriteAllText($probePath, 'unexpected-write')
    $result.workspace_write_blocked = $false
    Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
  } catch {
    $result.workspace_write_blocked = $true
  }
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect('1.1.1.1', 443, $null, $null)
    $connected = $async.AsyncWaitHandle.WaitOne(1500, $false) -and $client.Connected
    $client.Close()
    $result.external_tcp_probe_blocked = -not $connected
  } catch {
    $result.external_tcp_probe_blocked = $true
  }
  $result.output_write_succeeded = $true
} catch {
  $result.error_type = $_.Exception.GetType().Name
}
$json = $result | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText('C:\UltimateJarvis\output\result.json', $json, [System.Text.Encoding]::UTF8)
"""

class SandboxQualificationError(RuntimeError):
    """Raised when qualification cannot be safely run or complete."""

def _powershell_script(memory_limit_mb: int) -> str:
    if not 2048 <= memory_limit_mb <= 16384:
        raise ValueError("memory_limit_mb must be between 2048 and 16384.")
    return _QUALIFICATION_SCRIPT.replace("__MEMORY_MB__", str(memory_limit_mb))

def _stop_sandbox(process: Any) -> None:
    """Stop the sandbox UI process and its children on timeout/exit."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

def run_sandbox_qualification(
    *, timeout_seconds: int = 45, memory_limit_mb: int = 2048
) -> dict[str, Any]:
    """Run a fixed probe in Windows Sandbox; never execute caller-provided code."""
    if not 10 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be between 10 and 120.")
    if not 2048 <= memory_limit_mb <= 16384:
        raise ValueError("memory_limit_mb must be between 2048 and 16384.")
    host = probe_windows_sandbox()
    if not host.candidate_available:
        raise SandboxQualificationError(
            "Windows Sandbox is not available at the expected host path; no process was launched."
        )
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise SandboxQualificationError("SystemRoot is not configured.")
    executable = Path(system_root) / "System32" / "WindowsSandbox.exe"
    with tempfile.TemporaryDirectory(prefix="ultimatejarvis-qualification-") as temp:
        root = Path(temp)
        workspace, control, output = (root / "workspace", root / "control", root / "output")
        for folder in (workspace, control, output):
            folder.mkdir()
        (workspace / "qualification-marker.txt").write_text("read-only probe", encoding="utf-8")
        (control / "qualify.ps1").write_text(
            _powershell_script(memory_limit_mb), encoding="utf-8-sig"
        )
        wsb = root / "qualification.wsb"
        wsb.write_text(
            build_qualification_wsb_xml(workspace, control, output, memory_limit_mb=memory_limit_mb),
            encoding="utf-8",
        )
        try:
            process = subprocess.Popen(
                [str(executable), str(wsb)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            raise SandboxQualificationError(
                f"Windows Sandbox could not be started ({type(exc).__name__})."
            ) from exc
        started = time.monotonic()
        result_path = output / "result.json"
        try:
            while time.monotonic() - started < timeout_seconds:
                if result_path.is_file():
                    raw = result_path.read_bytes()
                    if len(raw) > 4096:
                        raise SandboxQualificationError("Qualification result exceeded 4096 bytes.")
                    try:
                        result = json.loads(raw.decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise SandboxQualificationError("Qualification result was invalid.") from exc
                    required = (
                        "workspace_write_blocked", "output_write_succeeded",
                        "external_tcp_probe_blocked",
                    )
                    if not isinstance(result, dict) or any(type(result.get(k)) is not bool for k in required):
                        raise SandboxQualificationError("Qualification result omitted required checks.")
                    result["duration_seconds"] = round(time.monotonic() - started, 2)
                    result["sandbox_process_observed"] = True
                    result["all_probes_passed"] = all(result[k] for k in required)
                    result["isolation_attestation"] = False
                    result["note"] = (
                        "This is a smoke test, not a complete security attestation. "
                        "Memory is requested in the .wsb configuration, not independently measured."
                    )
                    return result
                if process.poll() is not None:
                    raise SandboxQualificationError(
                        f"Windows Sandbox exited before returning results (exit={process.returncode})."
                    )
                time.sleep(0.25)
            raise SandboxQualificationError(f"Sandbox qualification exceeded {timeout_seconds} seconds.")
        finally:
            _stop_sandbox(process)
