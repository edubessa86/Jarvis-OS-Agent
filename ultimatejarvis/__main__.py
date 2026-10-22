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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
