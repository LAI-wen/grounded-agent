"""Executor service for Execution Subgraph.

Phase 2B: Real tool dispatch — routes each step to the correct
project-scoped file tool based on the step's `tool` field.

Only file_read and file_write are supported in V1 per spec §6.
"""

from pathlib import Path
from typing import Any

from app.tools.file_ops import read_file, write_file


def execute_safe_action(
    tool: str,
    tool_input: dict[str, Any],
    project_root: str,
) -> dict[str, Any]:
    """Dispatch a single execution step to the appropriate safe tool.

    Args:
        tool: Tool identifier — "file_read" or "file_write".
        tool_input: Validated input dict for the tool.
        project_root: Project root directory for path boundary enforcement.

    Returns:
        Result dict with keys: status, output, tool, path.

    Raises:
        ValueError: If tool is unknown or inputs are invalid.
    """
    if tool == "file_read":
        path = tool_input.get("path", "")
        content = read_file(path, project_root)
        return {
            "status": "success",
            "output": content,
            "tool": tool,
            "path": path,
        }

    if tool == "file_write":
        path = tool_input.get("path", "")
        content = tool_input.get("content", "")
        write_file(path, content, project_root)
        return {
            "status": "success",
            "output": f"Written: {path}",
            "tool": tool,
            "path": path,
        }

    raise ValueError(
        f"Unknown or forbidden tool: '{tool}'. "
        "Allowed tools in V1: file_read, file_write."
    )


def validate_path_safety(path: str, project_root: str) -> bool:
    """Check that a path resolves within the project root.

    Args:
        path: Path to validate (relative or absolute).
        project_root: Project root directory.

    Returns:
        True if the path is within project_root, False otherwise.
    """
    try:
        root = Path(project_root).resolve()
        candidate = Path(path)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        resolved.relative_to(root)
        return True
    except (ValueError, Exception):
        return False
