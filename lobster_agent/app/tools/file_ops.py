"""File operations tool — project-scoped safe file I/O.

Phase 2B: Real implementation with path boundary enforcement and
protected-file checks. Only reads and writes within project_root
are allowed.
"""

import fnmatch
from pathlib import Path


# Filename patterns that must never be written by the agent.
_PROTECTED_WRITE_PATTERNS = [
    ".env",
    ".env.*",
    "pyproject.toml",
    "*.lock",
    "*.cfg",
    "*.ini",
]


def _resolve_and_validate(path: str, project_root: str) -> Path:
    """Resolve a path and verify it stays within project_root.

    Args:
        path: Relative or absolute file path.
        project_root: Absolute path to the project root directory.

    Returns:
        Resolved absolute Path.

    Raises:
        ValueError: If the resolved path is outside project_root.
    """
    root = Path(project_root).resolve()
    candidate = Path(path)

    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()

    try:
        resolved.relative_to(root)  # raises ValueError if not under root
    except ValueError:
        raise ValueError(
            f"Path '{path}' resolves to '{resolved}' which is outside "
            f"project root '{root}'"
        )

    return resolved


def _is_protected(path: Path) -> bool:
    """Return True if the filename matches a protected write pattern."""
    name = path.name
    for pattern in _PROTECTED_WRITE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def read_file(path: str, project_root: str) -> str:
    """Read a file within the project directory.

    Args:
        path: Relative (or absolute) file path.
        project_root: Project root directory — file must be under this.

    Returns:
        File content as a string.

    Raises:
        ValueError: If path is outside project_root.
        FileNotFoundError: If the file does not exist.
    """
    resolved = _resolve_and_validate(path, project_root)
    return resolved.read_text(encoding="utf-8")


def write_file(path: str, content: str, project_root: str) -> bool:
    """Write content to a file within the project directory.

    Creates parent directories as needed. Overwrites existing files.

    Args:
        path: Relative (or absolute) file path.
        content: Content to write.
        project_root: Project root directory — file must be under this.

    Returns:
        True on success.

    Raises:
        ValueError: If path is outside project_root or is a protected file.
    """
    resolved = _resolve_and_validate(path, project_root)

    if _is_protected(resolved):
        raise ValueError(
            f"Cannot write to protected file: '{resolved.name}'. "
            "Protected patterns: " + ", ".join(_PROTECTED_WRITE_PATTERNS)
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return True
