"""Strict, non-executing planner that maps a user goal to allowlisted tools."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from .models import ModelGateway
from .orchestrator import ToolRegistry
from .policy import Risk

class PlanValidationError(ValueError):
    """Raised when model output does not match the safe planning contract."""

@dataclass(frozen=True)
class PlannedStep:
    tool: str
    arguments: dict[str, Any]
    reason: str
    risk: str

@dataclass(frozen=True)
class ToolPlan:
    goal: str
    steps: tuple[PlannedStep, ...]
    provider: str
    model: str

def _extract_json(text: str) -> Any:
    # Deliberately require raw JSON: no markdown fences or free-form repair.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PlanValidationError("Model response must be a single valid JSON object.") from exc

def create_plan(goal: str, gateway: ModelGateway, registry: ToolRegistry, *, max_steps: int = 5) -> ToolPlan:
    """Generate and validate a proposed plan. Never invokes any tool handler."""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Goal must be a non-empty string.")
    if len(goal) > 8_000:
        raise ValueError("Goal exceeds the 8000 character limit.")
    if not 1 <= max_steps <= 5:
        raise ValueError("max_steps must be between 1 and 5.")
    catalog_json = json.dumps(registry.describe(), ensure_ascii=False, separators=(",", ":"))
    prompt = (
        "You are the planning component of UltimateJarvis. Produce ONLY one raw JSON object "
        "with keys goal and steps. Do not use markdown. Do not invent capabilities. "
        "Each step must have exactly tool, arguments, reason. Use only tools from this catalog: "
        f"{catalog_json}. The maximum number of steps is {max_steps}. "
        "Arguments must be JSON objects. Plan read-only inspection only; do not claim actions "
        "were performed. User goal: " + json.dumps(goal, ensure_ascii=False)
    )
    response = gateway.generate(prompt, max_output_tokens=700)
    data = _extract_json(response.text)
    if not isinstance(data, dict) or set(data) != {"goal", "steps"}:
        raise PlanValidationError("Plan must contain exactly 'goal' and 'steps'.")
    plan_goal, raw_steps = data["goal"], data["steps"]
    if not isinstance(plan_goal, str) or not plan_goal.strip() or len(plan_goal) > 1_000:
        raise PlanValidationError("Plan goal must be a non-empty string up to 1000 characters.")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= max_steps:
        raise PlanValidationError(f"Plan must contain between 1 and {max_steps} steps.")
    planned = []
    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict) or set(item) != {"tool", "arguments", "reason"}:
            raise PlanValidationError(f"Step {index + 1} must contain exactly tool, arguments, reason.")
        tool, arguments, reason = item["tool"], item["arguments"], item["reason"]
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            raise PlanValidationError(f"Step {index + 1} has invalid tool or arguments.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise PlanValidationError(f"Step {index + 1} reason must be 1-500 characters.")
        valid, validation_reason, risk = registry.validate_call(tool, arguments)
        if not valid or risk is None:
            raise PlanValidationError(f"Step {index + 1} rejected: {validation_reason}")
        if risk is not Risk.READ_ONLY:
            raise PlanValidationError("This planning increment accepts read-only tools only.")
        planned.append(PlannedStep(tool, dict(arguments), reason, risk.value))
    return ToolPlan(plan_goal, tuple(planned), response.provider, response.model)
