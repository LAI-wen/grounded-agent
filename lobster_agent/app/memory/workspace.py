"""Workspace memory store for Lobster Agent.

Persists a compact record of completed tasks per project directory.
Store location: <project_root>/.lobster/workspace_memory.jsonl

Format: append-only JSON lines. Two record types:
  - task_summary: one per successfully completed task
  - artifact:     one per file written during that task

Design constraints:
  - Only written when status == "success" or review verdict == "pass"
  - Artifact paths are normalized relative to project_root;
    paths resolving outside the root are silently skipped
  - read_context() output is capped at 1500 chars (prompt token guard)
  - All I/O errors are silently swallowed — memory is never load-bearing
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STORE_DIR = ".lobster"
STORE_FILE = "workspace_memory.jsonl"

MAX_TASK_SUMMARIES = 50
MAX_ARTIFACT_RECORDS = 100
MAX_CONTEXT_CHARS = 1500


class WorkspaceStore:
    """File-backed workspace memory store, scoped to a single project root.

    All public methods are safe to call unconditionally: they return empty
    values or no-op silently when the store is absent or on any I/O error.
    """

    def __init__(self, project_root: str):
        self._root = Path(project_root).resolve()
        self._store_path = self._root / STORE_DIR / STORE_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_context(self, max_tasks: int = 5) -> str:
        """Return a compact workspace context block for prompt injection.

        Returns "" when the store is absent, empty, or on any read error.
        Output is capped at MAX_CONTEXT_CHARS to avoid dominating the prompt.
        """
        try:
            records = self._load()
        except Exception:
            return ""

        summaries = [r for r in records if r.get("type") == "task_summary"]
        recent = summaries[-max_tasks:]

        if not recent:
            return ""

        lines = []
        for r in recent:
            ts = r.get("timestamp", "")[:10]          # date only
            task_type = r.get("task_type", "?")
            objective = r.get("objective", "")[:80]
            status = r.get("status", "?")
            artifacts = r.get("artifacts", [])
            artifact_str = (
                f", artifacts: [{', '.join(artifacts)}]" if artifacts else ""
            )
            lines.append(
                f"- [{ts}] {task_type}: \"{objective}\" → {status}{artifact_str}"
            )

        block = "\nWorkspace history (recent tasks):\n" + "\n".join(lines)

        if len(block) > MAX_CONTEXT_CHARS:
            block = block[:MAX_CONTEXT_CHARS - 3] + "..."

        return block + "\n"

    def write_task_summary(self, result: dict) -> None:
        """Append task summary and artifact records for a completed task.

        Only persists when status is "success" or review verdict is "pass".
        Silently no-ops on any error — the main workflow is never affected.
        """
        try:
            if not self._should_persist(result):
                return

            task_id = result.get("task_id", "")
            task_type = result.get("task_type", "")
            normalized = result.get("normalized_task") or {}
            objective = str(
                normalized.get("objective", result.get("user_request", ""))
            )[:120]
            status = result.get("status", "")

            review = result.get("review_result")
            verdict = None
            if review:
                verdict = (
                    review.get("verdict")
                    if isinstance(review, dict)
                    else getattr(review, "verdict", None)
                )

            artifact_paths = self._collect_artifact_paths(result)
            timestamp = datetime.now(timezone.utc).isoformat()

            summary_record = {
                "type": "task_summary",
                "task_id": task_id,
                "timestamp": timestamp,
                "task_type": task_type,
                "objective": objective,
                "status": status,
                "artifacts": artifact_paths,
                "verdict": verdict,
            }

            artifact_records = [
                {
                    "type": "artifact",
                    "timestamp": timestamp,
                    "path": path,
                    "operation": "write",
                    "task_id": task_id,
                }
                for path in artifact_paths
            ]

            self._append([summary_record] + artifact_records)
            self._trim()

        except Exception:
            pass  # Memory is optional — never fail the main workflow

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _should_persist(self, result: dict) -> bool:
        """Return True only for tasks that completed successfully."""
        if result.get("status") == "success":
            return True
        review = result.get("review_result")
        if review:
            verdict = (
                review.get("verdict")
                if isinstance(review, dict)
                else getattr(review, "verdict", None)
            )
            if verdict == "pass":
                return True
        return False

    def _collect_artifact_paths(self, result: dict) -> list[str]:
        """Collect write-operation artifact paths, normalized to project_root."""
        paths = []
        for a in result.get("artifacts", []):
            path_str = (
                a.get("path") if isinstance(a, dict) else getattr(a, "path", None)
            )
            metadata = (
                a.get("metadata", {})
                if isinstance(a, dict)
                else getattr(a, "metadata", {})
            )
            operation = (
                metadata.get("operation") if isinstance(metadata, dict) else None
            )

            if operation != "write" or not path_str:
                continue

            normalized = self._normalize_path(path_str)
            if normalized is not None:
                paths.append(normalized)

        return paths

    def _normalize_path(self, path_str: str) -> Optional[str]:
        """Return path relative to project_root, or None if outside root."""
        try:
            candidate = Path(path_str)
            if candidate.is_absolute():
                resolved = candidate.resolve()
            else:
                resolved = (self._root / candidate).resolve()
            rel = resolved.relative_to(self._root)
            return str(rel)
        except (ValueError, Exception):
            return None

    def _load(self) -> list[dict]:
        """Load all records from the store. Returns [] if file absent."""
        if not self._store_path.exists():
            return []
        records = []
        with open(self._store_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # Skip malformed lines
        return records

    def _append(self, records: list[dict]) -> None:
        """Append records, creating the .lobster/ directory if needed."""
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._store_path, "a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")

    def _trim(self) -> None:
        """Enforce retention limits, rewriting the store only when over limit."""
        try:
            records = self._load()
            summaries = [r for r in records if r.get("type") == "task_summary"]
            artifacts = [r for r in records if r.get("type") == "artifact"]
            other = [
                r for r in records
                if r.get("type") not in ("task_summary", "artifact")
            ]

            if (
                len(summaries) <= MAX_TASK_SUMMARIES
                and len(artifacts) <= MAX_ARTIFACT_RECORDS
            ):
                return

            kept = other + summaries[-MAX_TASK_SUMMARIES:] + artifacts[-MAX_ARTIFACT_RECORDS:]
            with open(self._store_path, "w", encoding="utf-8") as f:
                for record in kept:
                    f.write(json.dumps(record) + "\n")
        except Exception:
            pass
