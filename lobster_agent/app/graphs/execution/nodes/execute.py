"""Execute node for Execution Subgraph.

Phase 2B: Real tool dispatch via executor service.
Produces a typed ExecutionOutput and records Artifact records for
every file operation performed.

This node is only reached when safety_check.passed is True
(enforced by the conditional edge in graph.py).
"""

import os
from datetime import datetime

from app.schemas import Artifact, Error

from ..services.executor import execute_safe_action
from ..state import ExecutionOutput, ExecutionState


def execute_actions(state: ExecutionState) -> ExecutionState:
    """Execute approved steps using safe project-scoped tools.

    Iterates over execution_plan, dispatches each step through
    execute_safe_action, and collects results into a typed ExecutionOutput.

    If any step fails, its error is captured and execution continues
    so that partial results are not lost.
    """
    safety_check = state.get("safety_check")

    # Defensive guard — should not be reached due to conditional edge in graph,
    # but protects against direct node invocation in tests.
    if not safety_check or not safety_check.get("passed"):
        abort_error = Error(
            code="SAFETY_CHECK_FAILED",
            severity="critical",
            message="Execution aborted: safety check did not pass",
            source="execution.nodes.execute",
            timestamp=datetime.utcnow(),
        )
        result: ExecutionOutput = {
            "actions_taken": [],
            "artifacts": [],
            "logs": [*state.get("logs", []), "Execution aborted: safety check failed"],
            "success": False,
            "status": "failed",
            "errors": [abort_error],
        }
        return {
            **state,
            "execution_result": result,
            "success": False,
            "errors": [*state.get("errors", []), abort_error],
            "abort_reason": "safety_check_failed",
            "logs": [*state.get("logs", []), "Execution aborted due to safety check failure"],
        }

    execution_plan = state.get("execution_plan", [])
    context = state.get("context", {})
    project_root = context.get("project_root") or os.getcwd()

    actions_taken: list[str] = []
    artifacts: list[Artifact] = []
    errors: list[Error] = []
    exec_logs: list[str] = list(state.get("logs", []))
    real_actions_ran: int = 0
    # Cache file_read results keyed by path for content-preserving writes
    read_cache: dict[str, str] = {}

    # Keywords in a write step's action that signal append / preserve semantics
    _APPEND_KEYWORDS = frozenset({
        "append", "preserve existing", "add to existing", "add new",
    })

    for step in execution_plan:
        tool = step.get("tool")
        tool_input = step.get("tool_input") or {}
        step_id = step.get("step_id", "unknown")
        action = step.get("action", "").lower()

        if tool is None:
            actions_taken.append(f"[{step_id}] No-op: {action}")
            exec_logs.append(f"[{step_id}] Skipped no-tool step")
            continue

        # For file_write targeting a previously-read file: combine existing
        # content with the new content when action signals preservation intent.
        if tool == "file_write":
            path_str = tool_input.get("path", "")
            if path_str in read_cache and any(kw in action for kw in _APPEND_KEYWORDS):
                existing = read_cache[path_str]
                new_content = tool_input.get("content", "")
                combined = existing.rstrip("\n") + "\n" + new_content
                tool_input = {**tool_input, "content": combined}
                exec_logs.append(
                    f"[{step_id}] file_write: combining file_read result with new content"
                )

        try:
            result_data = execute_safe_action(
                tool=tool,
                tool_input=tool_input,
                project_root=project_root,
            )

            real_actions_ran += 1
            output_summary = str(result_data.get("output", ""))[:120]
            actions_taken.append(f"[{step_id}] {tool}: {output_summary}")
            exec_logs.append(f"[{step_id}] {tool} succeeded")

            # Cache file_read output for use by subsequent write steps
            if tool == "file_read":
                read_cache[tool_input.get("path", "")] = result_data.get("output", "")

            # Record artifact
            path_str = tool_input.get("path", "")
            artifact = _make_artifact(tool, path_str, tool_input, step_id)
            artifacts.append(artifact)

        except Exception as e:
            err = Error(
                code="TOOL_EXECUTION_ERROR",
                severity="error",
                message=f"[{step_id}] {tool} failed: {e}",
                source="execution.nodes.execute",
                timestamp=datetime.utcnow(),
            )
            errors.append(err)
            exec_logs.append(f"[{step_id}] {tool} FAILED: {e}")

    # If no real tool calls ran and there were no errors, the plan was a no-op.
    # This happens when the fallback plan creates tool=None steps (no API key) or
    # when the LLM produces a plan with no file I/O steps.  Either way, it is
    # not a success — nothing was actually executed.
    no_real_actions = real_actions_ran == 0 and len(errors) == 0
    if no_real_actions:
        exec_logs.append(
            "No executable steps ran — plan had no file I/O steps "
            "(fallback plan or empty LLM plan)"
        )

    # Determine status
    if no_real_actions:
        status = "no_op"
        success = False
    elif len(errors) > 0:
        if real_actions_ran > 0:
            status = "partial"
        else:
            status = "failed"
        success = False
    else:
        status = "success"
        success = True

    exec_logs.append(
        f"Execution complete: {real_actions_ran} real action(s), "
        f"{len(artifacts)} artifact(s), {len(errors)} error(s), status={status}"
    )

    execution_result: ExecutionOutput = {
        "actions_taken": actions_taken,
        "artifacts": artifacts,
        "logs": exec_logs,
        "success": success,
        "status": status,
        "errors": errors,
    }

    return {
        **state,
        "execution_result": execution_result,
        "artifacts": [*state.get("artifacts", []), *artifacts],
        "success": success,
        "errors": [*state.get("errors", []), *errors],
        "logs": exec_logs,
    }


def _make_artifact(
    tool: str,
    path_str: str,
    tool_input: dict,
    step_id: str,
) -> Artifact:
    """Build an Artifact record for a completed file operation."""
    filename = path_str.split("/")[-1] if path_str else "unknown"

    if tool == "file_write":
        content = tool_input.get("content", "")
        return Artifact(
            type="file",
            name=filename,
            path=path_str,
            # Inline value only for small files; large files referenced by path
            value=content if len(content) <= 4096 else None,
            source_subgraph="execution",
            created_at=datetime.utcnow(),
            metadata={
                "step_id": step_id,
                "operation": "write",
                "size_bytes": len(content.encode("utf-8")),
            },
        )

    # file_read
    return Artifact(
        type="file",
        name=filename,
        path=path_str,
        value=None,  # content available on disk; not cached in state
        source_subgraph="execution",
        created_at=datetime.utcnow(),
        metadata={
            "step_id": step_id,
            "operation": "read",
        },
    )
