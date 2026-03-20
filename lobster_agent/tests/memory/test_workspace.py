"""Tests for WorkspaceStore.

All tests use tmp_path for isolation. No API key required.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.memory.workspace import (
    MAX_ARTIFACT_RECORDS,
    MAX_CONTEXT_CHARS,
    MAX_TASK_SUMMARIES,
    WorkspaceStore,
)
from app.schemas import Artifact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_artifact(path: str, operation: str = "write") -> Artifact:
    return Artifact(
        type="file",
        name=Path(path).name,
        path=path,
        source_subgraph="execution",
        created_at=datetime.utcnow(),
        metadata={"operation": operation, "step_id": "step_1", "size_bytes": 11},
    )


def _make_result(
    task_id: str = "test-123",
    task_type: str = "execution",
    objective: str = "Create hello.txt",
    status: str = "success",
    verdict: str = "pass",
    artifact_paths: list[str] = None,
) -> dict:
    if artifact_paths is None:
        artifact_paths = ["hello.txt"]
    return {
        "task_id": task_id,
        "task_type": task_type,
        "normalized_task": {"objective": objective},
        "user_request": objective,
        "status": status,
        "artifacts": [_make_artifact(p) for p in artifact_paths],
        "review_result": {"verdict": verdict, "issues": [], "final_response": ""},
    }


# ---------------------------------------------------------------------------
# read_context — absence and empty cases
# ---------------------------------------------------------------------------

def test_read_context_returns_empty_when_no_store(tmp_path):
    """read_context() returns '' when .lobster/ doesn't exist."""
    store = WorkspaceStore(str(tmp_path))
    assert store.read_context() == ""


def test_read_context_returns_empty_when_file_is_empty(tmp_path):
    """read_context() returns '' when the store file exists but is empty."""
    store = WorkspaceStore(str(tmp_path))
    store_path = tmp_path / ".lobster" / "workspace_memory.jsonl"
    store_path.parent.mkdir()
    store_path.write_text("")
    assert store.read_context() == ""


# ---------------------------------------------------------------------------
# write_task_summary — basic write behaviour
# ---------------------------------------------------------------------------

def test_write_creates_directory_and_file(tmp_path):
    """First write creates .lobster/workspace_memory.jsonl."""
    store = WorkspaceStore(str(tmp_path))
    store_path = tmp_path / ".lobster" / "workspace_memory.jsonl"

    assert not store_path.exists()
    store.write_task_summary(_make_result())
    assert store_path.exists()


def test_write_and_read_round_trip(tmp_path):
    """A written task summary appears in read_context() output."""
    store = WorkspaceStore(str(tmp_path))
    store.write_task_summary(_make_result(objective="Create hello.txt"))

    context = store.read_context()
    assert context != ""
    assert "hello.txt" in context
    assert "execution" in context


def test_artifact_records_written_per_file(tmp_path):
    """One artifact record is written per file in execution_result.artifacts."""
    store = WorkspaceStore(str(tmp_path))
    store.write_task_summary(
        _make_result(artifact_paths=["notes.txt", "summary.md"])
    )

    records = store._load()
    artifact_records = [r for r in records if r["type"] == "artifact"]
    paths = {r["path"] for r in artifact_records}
    assert "notes.txt" in paths
    assert "summary.md" in paths


# ---------------------------------------------------------------------------
# write_task_summary — only-on-success constraint
# ---------------------------------------------------------------------------

def test_write_skips_failed_tasks(tmp_path):
    """Failed tasks (status='failed') are not persisted."""
    store = WorkspaceStore(str(tmp_path))
    store.write_task_summary(_make_result(status="failed", verdict="fail"))
    assert store.read_context() == ""


def test_write_skips_blocked_tasks(tmp_path):
    """Blocked tasks (status='blocked') are not persisted."""
    store = WorkspaceStore(str(tmp_path))
    store.write_task_summary(_make_result(status="blocked", verdict="blocked"))
    assert store.read_context() == ""


def test_write_persists_when_verdict_is_pass_regardless_of_status(tmp_path):
    """A task with review verdict='pass' is persisted even if status differs."""
    store = WorkspaceStore(str(tmp_path))
    # partial status but verdict=pass (e.g. hybrid with minor issues)
    store.write_task_summary(_make_result(status="partial", verdict="pass"))
    assert store.read_context() != ""


# ---------------------------------------------------------------------------
# Artifact path normalization and safety
# ---------------------------------------------------------------------------

def test_artifact_path_stored_relative_to_root(tmp_path):
    """Absolute artifact paths are normalized to relative paths in the store."""
    store = WorkspaceStore(str(tmp_path))
    abs_path = str(tmp_path / "output.txt")
    store.write_task_summary(_make_result(artifact_paths=[abs_path]))

    records = store._load()
    artifact = next(r for r in records if r["type"] == "artifact")
    assert artifact["path"] == "output.txt"   # relative, not absolute


def test_artifact_path_outside_root_is_skipped(tmp_path):
    """Paths resolving outside project_root are silently dropped."""
    store = WorkspaceStore(str(tmp_path))
    store.write_task_summary(_make_result(artifact_paths=["../../.env"]))

    records = store._load()
    artifact_records = [r for r in records if r["type"] == "artifact"]
    assert artifact_records == []


def test_read_artifacts_skips_read_operations(tmp_path):
    """file_read artifacts (operation='read') are not recorded in the store."""
    store = WorkspaceStore(str(tmp_path))
    result = _make_result(artifact_paths=[])
    result["artifacts"] = [_make_artifact("config.py", operation="read")]
    store.write_task_summary(result)

    records = store._load()
    artifact_records = [r for r in records if r["type"] == "artifact"]
    assert artifact_records == []


# ---------------------------------------------------------------------------
# Retention limits
# ---------------------------------------------------------------------------

def test_retention_trims_task_summaries(tmp_path):
    """After MAX_TASK_SUMMARIES + 5 writes, store has <= MAX_TASK_SUMMARIES summaries."""
    store = WorkspaceStore(str(tmp_path))
    for i in range(MAX_TASK_SUMMARIES + 5):
        store.write_task_summary(_make_result(task_id=f"task-{i}"))

    records = store._load()
    summaries = [r for r in records if r["type"] == "task_summary"]
    assert len(summaries) <= MAX_TASK_SUMMARIES


# ---------------------------------------------------------------------------
# Token guard
# ---------------------------------------------------------------------------

def test_read_context_capped_at_max_chars(tmp_path):
    """read_context() output never exceeds MAX_CONTEXT_CHARS characters."""
    store = WorkspaceStore(str(tmp_path))
    # Write tasks with very long objectives to force truncation
    for i in range(10):
        store.write_task_summary(
            _make_result(
                task_id=f"t{i}",
                objective="A" * 200,
            )
        )

    context = store.read_context(max_tasks=10)
    assert len(context) <= MAX_CONTEXT_CHARS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
