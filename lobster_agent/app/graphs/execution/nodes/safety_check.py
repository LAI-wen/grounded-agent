"""Safety check node for Execution Subgraph.

Phase 2B: Real rule engine that validates paths and detects forbidden
operations before any tool is invoked.

Rules enforced:
- Tool must be in the V1 allowlist (file_read, file_write)
- Paths must resolve within project_root
- file_write is blocked for protected filenames (.env, pyproject.toml, etc.)
"""

import fnmatch
import os
from pathlib import Path

from app.schemas import SafetyFlag

from ..state import ExecutionState, SafetyCheckResult


_ALLOWED_TOOLS = {"file_read", "file_write"}

_PROTECTED_WRITE_PATTERNS = [
    ".env",
    ".env.*",
    "pyproject.toml",
    "*.lock",
    "*.cfg",
    "*.ini",
]


def _is_protected(filename: str) -> bool:
    for pattern in _PROTECTED_WRITE_PATTERNS:
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False


def perform_safety_check(state: ExecutionState) -> ExecutionState:
    """Validate each execution step before dispatching to tools.

    Checks:
    1. Tool is in the V1 allowlist
    2. Path resolves within project_root (no traversal)
    3. file_write is not targeting a protected file

    Sets safety_check.passed = True only when zero violations are found.
    """
    execution_plan = state.get("execution_plan", [])
    context = state.get("context", {})
    project_root = context.get("project_root") or os.getcwd()
    root = Path(project_root).resolve()

    violations: list[str] = []
    warnings: list[str] = []
    flags: list[SafetyFlag] = []

    for step in execution_plan:
        tool = step.get("tool")
        params = step.get("params", {})
        tool_input = step.get("tool_input") or {}
        step_id = step.get("step_id", "unknown")

        # No tool — safe no-op, skip all checks
        if tool is None:
            continue

        # Tool allowlist check
        if tool not in _ALLOWED_TOOLS:
            msg = f"[{step_id}] Tool '{tool}' is not in the V1 allowlist"
            violations.append(msg)
            flags.append(SafetyFlag(
                code="forbidden_tool",
                severity="high",
                message=msg,
                source="execution.nodes.safety_check",
                context={"step_id": step_id, "tool": tool},
            ))
            continue  # skip path checks for unknown tools

        # Path resolution and boundary check
        # Use tool_input first (set by generate node), fall back to params
        path_str = tool_input.get("path") or params.get("path", "")

        if not path_str:
            msg = f"[{step_id}] {tool} step has no path"
            violations.append(msg)
            flags.append(SafetyFlag(
                code="missing_path",
                severity="high",
                message=msg,
                source="execution.nodes.safety_check",
                context={"step_id": step_id, "tool": tool},
            ))
            continue

        try:
            candidate = Path(path_str)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (root / candidate).resolve()
            )
            try:
                resolved.relative_to(root)
            except ValueError:
                msg = (
                    f"[{step_id}] Path '{path_str}' resolves to '{resolved}' "
                    f"which is outside project root '{root}'"
                )
                violations.append(msg)
                flags.append(SafetyFlag(
                    code="path_violation",
                    severity="high",
                    message=msg,
                    source="execution.nodes.safety_check",
                    context={
                        "step_id": step_id,
                        "path": path_str,
                        "resolved": str(resolved),
                        "project_root": str(root),
                    },
                ))
                continue

        except Exception as e:
            msg = f"[{step_id}] Cannot resolve path '{path_str}': {e}"
            violations.append(msg)
            flags.append(SafetyFlag(
                code="path_error",
                severity="high",
                message=msg,
                source="execution.nodes.safety_check",
                context={"step_id": step_id, "path": path_str},
            ))
            continue

        # Protected file check (file_write only)
        if tool == "file_write" and _is_protected(resolved.name):
            msg = (
                f"[{step_id}] Write to protected file blocked: '{resolved.name}'"
            )
            violations.append(msg)
            flags.append(SafetyFlag(
                code="protected_file",
                severity="high",
                message=msg,
                source="execution.nodes.safety_check",
                context={
                    "step_id": step_id,
                    "path": path_str,
                    "filename": resolved.name,
                },
            ))

    passed = len(violations) == 0

    safety_check: SafetyCheckResult = {
        "passed": passed,
        "violations": violations,
        "warnings": warnings,
        "flags": flags,
    }

    if passed:
        log_msg = "Safety check passed"
    else:
        log_msg = f"Safety check FAILED: {len(violations)} violation(s)"

    return {
        **state,
        "safety_check": safety_check,
        "logs": [*state.get("logs", []), log_msg],
    }
