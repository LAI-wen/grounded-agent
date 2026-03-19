"""Simulate node for Execution Subgraph.

Phase 2B: Real dry-run that resolves paths, checks file existence,
and estimates write sizes before committing to execution.
"""

import os
from pathlib import Path

from ..state import ExecutionState


def simulate_execution(state: ExecutionState) -> ExecutionState:
    """Perform a dry run of the execution plan.

    For each step:
    - file_read: checks whether the target file currently exists
    - file_write: reports whether it would create or overwrite, and
      estimates the write size in bytes
    - no-tool: records as no-op

    Does not touch the filesystem — read-only inspection only.
    """
    execution_plan = state.get("execution_plan", [])
    context = state.get("context", {})
    project_root = context.get("project_root") or os.getcwd()
    root = Path(project_root).resolve()

    dry_run_outputs: list[str] = []
    warnings: list[str] = []

    for step in execution_plan:
        tool = step.get("tool")
        tool_input = step.get("tool_input") or {}
        step_id = step.get("step_id", "unknown")

        if tool == "file_read":
            path_str = tool_input.get("path", "")
            if not path_str:
                warnings.append(f"[{step_id}] file_read step has no path")
                dry_run_outputs.append(f"[{step_id}] Would read: (no path provided)")
                continue

            try:
                candidate = Path(path_str)
                resolved = (
                    candidate.resolve()
                    if candidate.is_absolute()
                    else (root / candidate).resolve()
                )
                if resolved.exists():
                    size = resolved.stat().st_size
                    dry_run_outputs.append(
                        f"[{step_id}] Would read: {path_str} ({size} bytes)"
                    )
                else:
                    warnings.append(f"[{step_id}] File does not exist: {path_str}")
                    dry_run_outputs.append(
                        f"[{step_id}] Would read: {path_str} (file not found)"
                    )
            except Exception as e:
                warnings.append(f"[{step_id}] Cannot simulate read for '{path_str}': {e}")
                dry_run_outputs.append(
                    f"[{step_id}] Would read: {path_str} (simulation error)"
                )

        elif tool == "file_write":
            path_str = tool_input.get("path", "")
            content = tool_input.get("content", "")

            if not path_str:
                warnings.append(f"[{step_id}] file_write step has no path")
                dry_run_outputs.append(f"[{step_id}] Would write: (no path provided)")
                continue

            try:
                candidate = Path(path_str)
                resolved = (
                    candidate.resolve()
                    if candidate.is_absolute()
                    else (root / candidate).resolve()
                )
                size_estimate = len(content.encode("utf-8"))
                action = "overwrite" if resolved.exists() else "create"
                dry_run_outputs.append(
                    f"[{step_id}] Would {action}: {path_str} (~{size_estimate} bytes)"
                )
                if resolved.exists():
                    warnings.append(
                        f"[{step_id}] Existing file will be overwritten: {path_str}"
                    )
            except Exception as e:
                warnings.append(f"[{step_id}] Cannot simulate write for '{path_str}': {e}")
                dry_run_outputs.append(
                    f"[{step_id}] Would write: {path_str} (simulation error)"
                )

        elif tool is None:
            dry_run_outputs.append(
                f"[{step_id}] No-op step: {step.get('action', '')}"
            )

        else:
            # Unknown tool — safety_check will catch this later
            dry_run_outputs.append(
                f"[{step_id}] Unknown tool (will be caught by safety check): {tool}"
            )

    simulation_result = {
        "success": True,
        "dry_run_outputs": dry_run_outputs,
        "warnings": warnings,
    }

    log_parts = [f"Simulation complete: {len(dry_run_outputs)} step(s)"]
    if warnings:
        log_parts.append(f"{len(warnings)} warning(s)")

    return {
        **state,
        "simulation_result": simulation_result,
        "logs": [*state.get("logs", []), ", ".join(log_parts)],
    }
