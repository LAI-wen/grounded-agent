"""Tests to verify schema consistency across the system.

Ensures that MainState uses shared schema types correctly.
"""

import pytest
from app.graphs.main.state import MainState
from app.graphs.main import create_initial_state
from app.schemas import Artifact, Error, SafetyFlag, TraceEntry
from datetime import datetime


def test_mainstate_uses_shared_artifact_schema():
    """Verify that MainState.artifacts uses Artifact schema."""
    state = create_initial_state("Test")

    # Add an Artifact
    artifact = Artifact(
        type="file",
        name="test.txt",
        path="/test/test.txt",
        value=None,
        source_subgraph="execution",
        created_at=datetime.utcnow(),
        metadata={}
    )

    state["artifacts"].append(artifact)

    # Verify it's the correct type
    assert len(state["artifacts"]) == 1
    assert isinstance(state["artifacts"][0], Artifact)
    assert state["artifacts"][0].name == "test.txt"


def test_mainstate_uses_shared_error_schema():
    """Verify that MainState.errors uses Error schema."""
    state = create_initial_state("Test")

    # Add an Error
    error = Error(
        code="TEST_ERROR",
        severity="error",
        message="Test error message",
        source="test_node",
        timestamp=datetime.utcnow(),
        stack_trace=None
    )

    state["errors"].append(error)

    # Verify it's the correct type
    assert len(state["errors"]) == 1
    assert isinstance(state["errors"][0], Error)
    assert state["errors"][0].code == "TEST_ERROR"


def test_mainstate_uses_shared_trace_entry_schema():
    """Verify that MainState.trace uses TraceEntry schema."""
    state = create_initial_state("Test")

    # Add a TraceEntry
    trace_entry = TraceEntry(
        step_id="trace_0",
        node_name="test_node",
        subgraph=None,
        timestamp=datetime.utcnow(),
        input_summary="test input",
        output_summary="test output",
        status="success"
    )

    state["trace"].append(trace_entry)

    # Verify it's the correct type
    assert len(state["trace"]) == 1
    assert isinstance(state["trace"][0], TraceEntry)
    assert state["trace"][0].node_name == "test_node"


def test_mainstate_uses_shared_safety_flag_schema():
    """Verify that MainState.safety_flags uses SafetyFlag schema."""
    state = create_initial_state("Test")

    # Add a SafetyFlag
    safety_flag = SafetyFlag(
        code="path_violation",
        severity="high",
        message="Path outside project boundary",
        source="execution.safety_check",
        context={"path": "/etc/passwd"}
    )

    state["safety_flags"].append(safety_flag)

    # Verify it's the correct type
    assert len(state["safety_flags"]) == 1
    assert isinstance(state["safety_flags"][0], SafetyFlag)
    assert state["safety_flags"][0].code == "path_violation"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
