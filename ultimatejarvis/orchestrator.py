"""Small task lifecycle and explicit tool registry for UltimateJarvis.

This module does not ask an LLM to choose its own permissions. Each tool's risk
classification is fixed at registration time by application code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Callable, Mapping

from .policy import Decision, Risk, ToolRequest, authorize


class TaskState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
_TRANSITIONS = {
    TaskState.CREATED: {TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.PLANNING: {TaskState.WAITING_APPROVAL, TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.WAITING_APPROVAL: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.RUNNING: {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.WAITING_APPROVAL},
    TaskState.SUCCEEDED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


@dataclass(frozen=True)
class TaskEvent:
    at: str
    state: TaskState
    message: str


@dataclass
class TaskRecord:
    task_id: str
    prompt: str
    state: TaskState = TaskState.CREATED
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    events: list[TaskEvent] = field(default_factory=list)
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStateError(ValueError):
    pass


class TaskManager:
    """In-memory state machine; persistence is a later, separate increment."""

    def __init__(self, *, max_prompt_chars: int = 20_000) -> None:
        if max_prompt_chars < 1:
            raise ValueError("max_prompt_chars must be positive")
        self.max_prompt_chars = max_prompt_chars
        self._tasks: dict[str, TaskRecord] = {}

    def create(self, task_id: str, prompt: str) -> TaskRecord:
        if not task_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task_id):
            raise ValueError("task_id must contain 1-80 letters, digits, '_' or '-'")
        if task_id in self._tasks:
            raise ValueError("task_id already exists")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if len(prompt) > self.max_prompt_chars:
            raise ValueError("prompt exceeds configured size limit")
        record = TaskRecord(task_id=task_id, prompt=prompt)
        record.events.append(TaskEvent(record.created_at, TaskState.CREATED, "Task created"))
        self._tasks[task_id] = record
        return record

    def get(self, task_id: str) -> TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task: {task_id}") from exc

    def transition(self, task_id: str, new_state: TaskState, message: str, *, error: str | None = None) -> TaskRecord:
        record = self.get(task_id)
        if new_state not in _TRANSITIONS[record.state]:
            raise TaskStateError(f"Invalid task transition: {record.state.value} -> {new_state.value}")
        record.state = new_state
        record.updated_at = _now()
        record.error = error if new_state is TaskState.FAILED else None
        record.events.append(TaskEvent(record.updated_at, new_state, message[:500]))
        return record

    def cancel(self, task_id: str, reason: str = "Cancelled by user") -> TaskRecord:
        return self.transition(task_id, TaskState.CANCELLED, reason)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: Risk
    handler: Callable[[Mapping[str, Any]], Any]
    required_arguments: frozenset[str] = frozenset()
    allowed_arguments: frozenset[str] = frozenset()
    description: str = ""


@dataclass(frozen=True)
class ToolResult:
    decision: Decision
    value: Any = None
    reason: str = ""


class ToolRegistry:
    """Explicit allowlist; risk labels are registered by trusted application code."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", spec.name):
            raise ValueError("Tool name must be a normalized capability identifier")
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        if not callable(spec.handler):
            raise ValueError("Tool handler must be callable")
        if not spec.required_arguments.issubset(spec.allowed_arguments):
            raise ValueError("Required arguments must be included in allowed arguments")
        self._tools[spec.name] = spec

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": spec.name, "risk": spec.risk.value, "description": spec.description}
            for spec in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        in_sandbox: bool = False,
        approved: bool = False,
    ) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(Decision.DENY, reason="Unknown tool; not in the allowlist.")
        if not isinstance(arguments, Mapping):
            return ToolResult(Decision.DENY, reason="Arguments must be a mapping.")
        keys = set(arguments)
        if not spec.required_arguments.issubset(keys):
            return ToolResult(Decision.DENY, reason="Required arguments are missing.")
        if not keys.issubset(spec.allowed_arguments):
            return ToolResult(Decision.DENY, reason="Unexpected arguments are not allowed.")

        result = authorize(ToolRequest(
            tool_name=spec.name,
            risk=spec.risk,
            in_sandbox=in_sandbox,
            approved=approved,
        ))
        if result.decision is not Decision.ALLOW:
            return ToolResult(result.decision, reason=result.reason)
        try:
            value = spec.handler(dict(arguments))
        except Exception as exc:
            return ToolResult(Decision.DENY, reason=f"Tool failed: {type(exc).__name__}")
        return ToolResult(Decision.ALLOW, value=value, reason="Tool completed.")
