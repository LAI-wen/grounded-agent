"""Local file retrieval adapter.

Searches the project directory (os.getcwd()) for files whose content
contains query terms. Returns bounded excerpts as source records.
"""

import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

from .base import RetrievalAdapter


# Directories that are never useful for research queries
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
    "*.egg-info",
}

# File extensions treated as binary / non-text
_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".db", ".sqlite", ".sqlite3",
    ".lock",
}

_MAX_FILE_BYTES = 512 * 1024   # skip files larger than 512 KB
_EXCERPT_CONTEXT_LINES = 5     # lines before/after the first match


def _tokenize(query: str) -> list[str]:
    """Split query into lowercase search terms, dropping single-char tokens."""
    tokens = re.findall(r"[a-z0-9_]+", query.lower())
    return [t for t in tokens if len(t) > 1]


def _count_matches(content_lower: str, terms: list[str]) -> int:
    """Count total occurrences of all terms in content."""
    return sum(content_lower.count(t) for t in terms)


def _extract_excerpt(lines: list[str], first_match_line: int) -> str:
    """Return up to 2*_EXCERPT_CONTEXT_LINES+1 lines centred on first_match_line."""
    start = max(0, first_match_line - _EXCERPT_CONTEXT_LINES)
    end = min(len(lines), first_match_line + _EXCERPT_CONTEXT_LINES + 1)
    excerpt_lines = lines[start:end]
    # Truncate any single line that is very long (e.g. minified JS)
    truncated = [ln[:200] for ln in excerpt_lines]
    return "\n".join(truncated)


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.endswith(".egg-info")


class LocalFileRetrievalAdapter(RetrievalAdapter):
    """Retrieval adapter that searches files in the project directory.

    Performs keyword search across text files under os.getcwd().
    No external services required — always available.
    """

    def __init__(self, root: Optional[str] = None, max_depth: int = 4):
        """
        Args:
            root: Directory to search (defaults to os.getcwd() at call time).
            max_depth: Maximum directory recursion depth (default: 4).
        """
        self._root = root
        self.max_depth = max_depth

    def is_available(self) -> bool:
        """Local file adapter is always available."""
        return True

    def search(
        self,
        query: str,
        strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search project files for query terms.

        Args:
            query: Natural-language search query; tokenized into terms.
            strategy: Stored in metadata; search behaviour is always keyword-based.
            max_results: Maximum number of source records to return.

        Returns:
            List of source records, sorted by match count (descending).
            Each record:
                url              : None (local file)
                title            : relative path from project root
                content          : ~10-line excerpt around first match
                reliability_score: 0.85
                metadata         : adapter, file_path, match_count, strategy
        """
        root = Path(self._root or os.getcwd()).resolve()
        terms = _tokenize(query)
        if not terms:
            return []

        candidates: list[dict[str, Any]] = []

        for file_path in self._walk(root):
            rel = file_path.relative_to(root)

            # Skip oversized files
            try:
                if file_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue

            # Read and search
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            content_lower = text.lower()
            match_count = _count_matches(content_lower, terms)
            if match_count == 0:
                continue

            # Find first matching line for excerpt
            lines = text.splitlines()
            first_match_line = 0
            for i, line in enumerate(lines):
                if any(t in line.lower() for t in terms):
                    first_match_line = i
                    break

            excerpt = _extract_excerpt(lines, first_match_line)

            candidates.append({
                "url": None,
                "title": str(rel),
                "content": excerpt,
                "reliability_score": 0.85,
                "metadata": {
                    "adapter": "local_file",
                    "file_path": str(file_path),
                    "match_count": match_count,
                    "strategy": strategy,
                    "is_mock": False,
                },
            })

        # Rank by match count descending, then by path for stability
        candidates.sort(key=lambda s: (-s["metadata"]["match_count"], s["title"]))
        return candidates[:max_results]

    def _walk(self, root: Path, depth: int = 0):
        """Yield all readable text files under root up to max_depth."""
        if depth > self.max_depth:
            return
        try:
            entries = sorted(root.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if not _should_skip_dir(entry.name):
                    yield from self._walk(entry, depth + 1)
            elif entry.is_file():
                if entry.suffix not in _SKIP_EXTENSIONS:
                    yield entry
