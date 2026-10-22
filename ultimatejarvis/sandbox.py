"""Fail-closed contract for a future OS-enforced execution sandbox.

This module does not create a sandbox or execute code. It describes the minimum
capabilities a real backend must report before a future executor may be enabled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IsolationProfile:
    """Resource and access limits requested for an isolated task."""

    network_enabled: bool = False
    workspace_read_only: bool = True
    memory_limit_mb: int = 512
    timeout_seconds: int = 15
    max_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if self.network_enabled:
            raise ValueError("Network access must remain disabled by default.")
        if not self.workspace_read_only:
            raise ValueError("The workspace must remain read-only in this profile.")
        if not 64 <= self.memory_limit_mb <= 4096:
            raise ValueError("memory_limit_mb must be between 64 and 4096.")
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300.")
        if not 1024 <= self.max_output_bytes <= 1_048_576:
            raise ValueError("max_output_bytes must be between 1024 and 1048576.")


@dataclass(frozen=True)
class IsolationCapabilities:
    """Capabilities positively reported by a trusted OS sandbox adapter."""

    process_isolation: bool = False
    network_blocked: bool = False
    filesystem_read_only: bool = False
    memory_limit_enforced: bool = False
    timeout_enforced: bool = False
    output_limit_enforced: bool = False


class IsolationUnavailableError(RuntimeError):
    """Raised when requested isolation cannot be proven to be enforced."""


def require_isolation(
    capabilities: IsolationCapabilities | None,
) -> IsolationCapabilities:
    """Fail closed unless every required isolation control is confirmed.

    A capability report is only meaningful when supplied by trusted adapter
    code that actually configures and verifies the operating-system controls.
    Caller/model-provided booleans must never be accepted as proof.
    """
    if capabilities is None:
        raise IsolationUnavailableError(
            "No OS-enforced sandbox backend is available; execution is disabled."
        )

    required = (
        "process_isolation",
        "network_blocked",
        "filesystem_read_only",
        "memory_limit_enforced",
        "timeout_enforced",
        "output_limit_enforced",
    )
    missing = [name for name in required if not getattr(capabilities, name, False)]
    if missing:
        raise IsolationUnavailableError(
            "Sandbox controls are not confirmed: " + ", ".join(missing)
        )
    return capabilities
