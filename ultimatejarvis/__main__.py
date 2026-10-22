"""Command-line entry point for safe, read-only diagnostics."""
from __future__ import annotations

import argparse
import json

from .diagnostics import collect_diagnostics


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
    args = parser.parse_args()

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
            report["ollama"] = {
                "available": False,
                "error": type(exc).__name__,
            }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
