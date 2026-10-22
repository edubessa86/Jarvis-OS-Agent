"""Human-approved execution for read-only plans, with minimal audit records."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from .orchestrator import ToolRegistry
from .planner import ToolPlan
from .policy import Decision, Risk

@dataclass(frozen=True)
class StepExecution:
    index: int
    tool: str
    status: str
    reason: str
    value: Any = None

def execute_approved_plan(
    plan: ToolPlan,
    registry: ToolRegistry,
    *,
    approve_step: Callable[[int, str, dict[str, Any], str], bool],
    audit: Callable[[dict[str, Any]], None],
) -> list[StepExecution]:
    """Execute only currently registered read-only tools after explicit per-step approval.

    All steps are revalidated before the first prompt or side effect. Results are
    returned to the caller, but audit events intentionally omit tool output content.
    """
    if not isinstance(plan, ToolPlan):
        raise TypeError("plan must be a validated ToolPlan")
    if not plan.steps or len(plan.steps) > 5:
        raise ValueError("Plan must contain between 1 and 5 steps")
    for index, step in enumerate(plan.steps, start=1):
        valid, reason, risk = registry.validate_call(step.tool, step.arguments)
        if not valid or risk is not Risk.READ_ONLY or step.risk != Risk.READ_ONLY.value:
            raise ValueError(f"Step {index} failed execution-time validation: {reason}")
    results: list[StepExecution] = []
    for index, step in enumerate(plan.steps, start=1):
        approved = bool(approve_step(index, step.tool, dict(step.arguments), step.reason))
        if not approved:
            audit({"step": index, "tool": step.tool, "arguments": dict(step.arguments),
                   "status": "rejected_by_user", "summary": "User declined this step."})
            results.append(StepExecution(index, step.tool, "rejected_by_user", "User declined this step."))
            continue
        outcome = registry.invoke(step.tool, step.arguments, approved=True)
        if outcome.decision is not Decision.ALLOW:
            status = "denied"
            summary = outcome.reason[:300]
            value = None
        else:
            status = "succeeded"
            summary = f"Result type: {type(outcome.value).__name__}"
            if isinstance(outcome.value, (str, list, dict)):
                summary += f"; item count/characters: {len(outcome.value)}"
            value = outcome.value
        audit({"step": index, "tool": step.tool, "arguments": dict(step.arguments),
               "status": status, "summary": summary})
        results.append(StepExecution(index, step.tool, status, summary, value))
    return results
