"""Command-line entry point for diagnostics, local model queries and explicit memory."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .diagnostics import collect_diagnostics


def _default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "UltimateJarvis" if base else Path.home() / "AppData" / "Local" / "UltimateJarvis"
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "ultimatejarvis" if base else Path.home() / ".local" / "share" / "ultimatejarvis"


def _default_memory_db() -> Path:
    return _default_data_dir() / "memory.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(prog="ultimatejarvis")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnostics_parser = subparsers.add_parser(
        "diagnostics", help="Show basic OS, Python, CPU, RAM and optional disk facts"
    )
    diagnostics_parser.add_argument("--workspace", help="Existing directory to report free disk space for")
    diagnostics_parser.add_argument(
        "--ollama-model",
        help="Optionally test a local Ollama model, e.g. qwen2.5:3b (requires Ollama running)",
    )

    ask_parser = subparsers.add_parser("ask", help="Ask a prompt to a local Ollama model")
    ask_parser.add_argument("--model", default="qwen2.5:3b", help="Model already installed in Ollama")
    ask_parser.add_argument("prompt", help="Prompt text; not automatically combined with memory")

    memory_parser = subparsers.add_parser("memory", help="Manage explicit local memory")
    memory_parser.add_argument("--db", type=Path, default=_default_memory_db(), help="SQLite database path")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    add_parser = memory_sub.add_parser("add", help="Save a note")
    add_parser.add_argument("content")
    add_parser.add_argument("--category", default="general")
    add_parser.add_argument("--tag", action="append", default=[])
    search_parser = memory_sub.add_parser("search", help="Search saved notes")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    list_parser = memory_sub.add_parser("list", help="List recent notes")
    list_parser.add_argument("--limit", type=int, default=20)
    delete_parser = memory_sub.add_parser("delete", help="Delete a note by ID")
    delete_parser.add_argument("memory_id")

    tasks_parser = subparsers.add_parser("tasks", help="Manage persistent local task history")
    tasks_parser.add_argument("--db", type=Path, default=_default_data_dir() / "tasks.sqlite3",
                              help="SQLite task database path")
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_command", required=True)
    task_create = tasks_sub.add_parser("create", help="Create a task record (does not execute it)")
    task_create.add_argument("task_id")
    task_create.add_argument("prompt")
    task_list = tasks_sub.add_parser("list", help="List recent task records")
    task_list.add_argument("--limit", type=int, default=20)
    task_show = tasks_sub.add_parser("show", help="Show one task and its event history")
    task_show.add_argument("task_id")
    task_transition = tasks_sub.add_parser("transition", help="Record an allowed task-state transition")
    task_transition.add_argument("task_id")
    task_transition.add_argument("state", choices=["planning", "waiting_approval", "running", "succeeded", "failed", "cancelled"])
    task_transition.add_argument("message")
    task_transition.add_argument("--error", help="Optional error detail, stored only when state is failed")

    inspect_parser = subparsers.add_parser("inspect", help="Run approved read-only inspection tools")
    inspect_parser.add_argument("--workspace", type=Path,
                                default=Path.home() / "UltimateJarvisWorkspace",
                                help="Workspace root for workspace-only tools")
    inspect_sub = inspect_parser.add_subparsers(dest="inspect_command", required=True)
    inspect_sub.add_parser("tools", help="List available read-only tools")
    inspect_system = inspect_sub.add_parser("system", help="Read basic system diagnostics")
    inspect_list = inspect_sub.add_parser("list-files", help="List files within the configured workspace")
    inspect_list.add_argument("--dir", default=".", help="Relative directory within the workspace")
    inspect_read = inspect_sub.add_parser("read-text", help="Read a UTF-8 text file in the workspace")
    inspect_read.add_argument("path", help="Relative file path within the workspace")

    args = parser.parse_args()

    if args.command == "diagnostics":
        report = collect_diagnostics(args.workspace)
        if args.ollama_model:
            from .models import ModelGateway
            from .ollama_provider import OllamaProvider

            try:
                response = ModelGateway([OllamaProvider(args.ollama_model)]).generate(
                    "Reply with the single word READY.", max_output_tokens=8
                )
                report["ollama"] = {
                    "available": True,
                    "model": response.model,
                    "sample_response": response.text[:120],
                }
            except Exception as exc:
                report["ollama"] = {"available": False, "error": type(exc).__name__}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "ask":
        from .models import ModelGateway
        from .ollama_provider import OllamaProvider

        response = ModelGateway([OllamaProvider(args.model)]).generate(args.prompt)
        print(response.text)
        return 0

    if args.command == "memory":
        from .memory import LocalMemory

        with LocalMemory(args.db) as memory:
            if args.memory_command == "add":
                result = memory.remember(args.content, category=args.category, tags=args.tag)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.memory_command == "search":
                print(json.dumps(memory.search(args.query, limit=args.limit), ensure_ascii=False, indent=2))
            elif args.memory_command == "list":
                print(json.dumps(memory.recent(limit=args.limit), ensure_ascii=False, indent=2))
            elif args.memory_command == "delete":
                deleted = memory.delete(args.memory_id)
                print(json.dumps({"deleted": deleted, "memory_id": args.memory_id}, ensure_ascii=False))
                return 0 if deleted else 1
        return 0

    if args.command == "inspect":
        from .windows_tools import build_read_only_registry

        registry = build_read_only_registry(args.workspace)
        if args.inspect_command == "tools":
            result = registry.describe()
        elif args.inspect_command == "system":
            result = registry.invoke("system.diagnostics", {}).value
        elif args.inspect_command == "list-files":
            result = registry.invoke("workspace.list_files", {"relative_dir": args.dir}).value
        elif args.inspect_command == "read-text":
            tool_result = registry.invoke("workspace.read_text", {"relative_path": args.path})
            if tool_result.decision.value != "allow":
                print(json.dumps({"error": tool_result.reason}, ensure_ascii=False, indent=2))
                return 1
            result = tool_result.value
        else:
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "tasks":
        from .orchestrator import TaskState
        from .storage import SQLiteTaskStore

        with SQLiteTaskStore(args.db) as store:
            if args.tasks_command == "create":
                result = store.create(args.task_id, args.prompt)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.tasks_command == "list":
                print(json.dumps(store.list_tasks(limit=args.limit), ensure_ascii=False, indent=2))
            elif args.tasks_command == "show":
                print(json.dumps({"task": store.get(args.task_id), "events": store.events(args.task_id)},
                                 ensure_ascii=False, indent=2))
            elif args.tasks_command == "transition":
                result = store.transition(args.task_id, TaskState(args.state), args.message, error=args.error)
                print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
