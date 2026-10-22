"""Small, dependency-free authorization policy for UltimateJarvis tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Risk(str, Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    SANDBOX_EXECUTION = "sandbox_execution"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ToolRequest:
    """A proposed tool action; tool names and risk labels are not model authority."""

    tool_name: str
    risk: Risk
    in_sandbox: bool = False
    approved: bool = False


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str


def authorize(request: ToolRequest) -> PolicyResult:
    """Return a policy decision without executing the requested action.

    Only explicitly recognized low-risk capabilities are allowed by default.
    Sandboxed execution requires an isolated sandbox flag. External side effects
    and destructive operations require explicit approval. This function is a
    policy primitive, not a replacement for operating-system isolation.
    """
    if not request.tool_name or not request.tool_name.strip():
        return PolicyResult(Decision.DENY, "Tool name is required.")

    if request.risk is Risk.READ_ONLY:
        return PolicyResult(Decision.ALLOW, "Read-only capability requested.")

    if request.risk is Risk.WORKSPACE_WRITE:
        return PolicyResult(
            Decision.ALLOW,
            "Workspace writes must still pass workspace path validation.",
        )

    if request.risk is Risk.SANDBOX_EXECUTION:
        if not request.in_sandbox:
            return PolicyResult(
                Decision.DENY,
                "Execution is denied unless an isolated sandbox is active.",
            )
        return PolicyResult(
            Decision.REQUIRE_APPROVAL,
            "Sandbox execution requires an explicit execution grant.",
        ) if not request.approved else PolicyResult(
            Decision.ALLOW, "Approved sandbox execution."
        )

    if request.risk in (Risk.EXTERNAL_SIDE_EFFECT, Risk.DESTRUCTIVE):
        if request.approved:
            return PolicyResult(Decision.ALLOW, "Explicit approval provided.")
        return PolicyResult(
            Decision.REQUIRE_APPROVAL,
            "External or destructive action requires explicit approval.",
        )

    return PolicyResult(Decision.DENY, "Unknown risk category; deny by default.")
