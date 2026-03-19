"""Interactive CLI for Lobster Agent.

Usage:
    python3 -m app.cli [--project-root PATH] [--thread-id ID]

Options:
    --project-root PATH   Working directory for file operations (default: cwd)
    --thread-id ID        Resume a named conversation thread (default: new UUID)

Type 'exit' or 'quit' to end the session. Ctrl-C also exits cleanly.
"""

import argparse
import os
import sys
import uuid

from app.main import run_lobster_agent
from app.memory.checkpointer import create_checkpointer


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="lobster-agent",
        description="Lobster Agent — interactive task runner",
    )
    parser.add_argument(
        "--project-root",
        metavar="PATH",
        default=None,
        help="Working directory for file operations (default: current directory)",
    )
    parser.add_argument(
        "--thread-id",
        metavar="ID",
        default=None,
        help="Thread ID to resume a previous session (default: new session)",
    )
    return parser.parse_args(argv)


def _print_response(result):
    """Print the assistant response and a one-line status footer."""
    messages = result.get("messages", [])
    assistant = next(
        (m for m in reversed(messages) if m.get("role") == "assistant"), None
    )
    if assistant:
        print(f"\nAssistant: {assistant['content']}")
    else:
        print("\nAssistant: (no response generated)")

    status = result.get("status", "unknown")
    task_type = result.get("task_type", "unknown")
    n_artifacts = len(result.get("artifacts", []))
    n_trace = len(result.get("trace", []))

    footer_parts = [f"status: {status}", f"type: {task_type}", f"steps: {n_trace}"]
    if n_artifacts:
        footer_parts.append(f"artifacts: {n_artifacts}")
    print(f"\n\u2500\u2500\u2500 {' | '.join(footer_parts)} \u2500\u2500\u2500")

    errors = result.get("errors", [])
    if errors:
        print("\nErrors:")
        for e in errors:
            msg = getattr(e, "message", None) or (e.get("message") if isinstance(e, dict) else str(e))
            sev = getattr(e, "severity", None) or (e.get("severity") if isinstance(e, dict) else "error")
            print(f"  [{sev}] {msg}")


def main(argv=None):
    args = _parse_args(argv)

    # Apply --project-root by changing the working directory for this process.
    # Execution nodes use os.getcwd() as the project root boundary, so this
    # ensures all file operations are scoped to the requested directory.
    if args.project_root:
        project_root = os.path.abspath(args.project_root)
        if not os.path.isdir(project_root):
            print(f"Error: --project-root '{args.project_root}' is not a directory.", file=sys.stderr)
            sys.exit(1)
        os.chdir(project_root)

    thread_id = args.thread_id or str(uuid.uuid4())
    is_new_thread = args.thread_id is None

    # Create a single checkpointer for the session so get_state finds prior turns
    checkpointer = create_checkpointer()

    print("Lobster Agent")
    print("=" * 60)
    print(f"  project root : {os.getcwd()}")
    print(f"  thread       : {thread_id}{' (new)' if is_new_thread else ' (resumed)'}")
    print("  type 'exit' or 'quit' to end the session")
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("Exiting.")
            break

        print("\nProcessing...")
        try:
            result = run_lobster_agent(user_input, thread_id=thread_id, checkpointer=checkpointer)
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            continue

        _print_response(result)


if __name__ == "__main__":
    main()
