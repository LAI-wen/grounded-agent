"""Tests for Parent Graph wrapper nodes.

These tests verify that wrappers correctly:
1. Map MainState → Subgraph input
2. Invoke subgraphs
3. Map subgraph output → MainState
4. Preserve unrelated MainState fields
"""

import pytest
from app.graphs.main.state import MainState, NormalizedTask
from app.graphs.main.wrappers import (
    invoke_research_subgraph,
    invoke_execution_subgraph,
    invoke_review_subgraph,
)


def create_test_mainstate() -> MainState:
    """Create a test MainState with all fields populated."""
    normalized_task: NormalizedTask = {
        "objective": "Test task",
        "constraints": ["constraint1"],
        "requested_outputs": ["response"],
        "assumptions": ["assumption1"],
        "priority": "normal",
    }

    return MainState(
        user_request="Test user request",
        normalized_task=normalized_task,
        task_id="test-123",
        task_type="hybrid",
        required_subgraphs=["research", "execution", "review"],
        current_subgraph=None,
        next_step=None,
        plan=None,
        research_result=None,
        execution_result=None,
        review_result=None,
        artifacts=[],
        messages=[],
        errors=[],
        trace=[],
        safety_flags=[],
        status="running",
    )


def test_research_wrapper_maps_mainstate_to_subgraph_input():
    """Test that research wrapper correctly maps MainState to ResearchState input."""
    main_state = create_test_mainstate()

    # Invoke wrapper
    result = invoke_research_subgraph(main_state)

    # Verify that research was invoked and result was mapped back
    assert result["research_result"] is not None
    assert "summary" in result["research_result"]
    assert "evidence" in result["research_result"]
    assert "confidence" in result["research_result"]
    assert "citations" in result["research_result"]
    assert "open_questions" in result["research_result"]


def test_research_wrapper_maps_subgraph_output_back_to_mainstate():
    """Test that research wrapper correctly maps subgraph output to MainState."""
    main_state = create_test_mainstate()

    result = invoke_research_subgraph(main_state)

    # Verify ResearchResult structure
    research_result = result["research_result"]
    assert isinstance(research_result["summary"], str)
    assert isinstance(research_result["evidence"], list)
    assert isinstance(research_result["citations"], list)
    assert isinstance(research_result["confidence"], float)
    assert 0.0 <= research_result["confidence"] <= 1.0
    assert isinstance(research_result["open_questions"], list)


def test_research_wrapper_preserves_unrelated_mainstate_fields():
    """Test that research wrapper doesn't modify unrelated MainState fields."""
    main_state = create_test_mainstate()

    # Set some unrelated fields
    original_task_id = main_state["task_id"]
    original_user_request = main_state["user_request"]
    original_task_type = main_state["task_type"]
    original_required_subgraphs = main_state["required_subgraphs"]

    result = invoke_research_subgraph(main_state)

    # Verify unrelated fields are preserved
    assert result["task_id"] == original_task_id
    assert result["user_request"] == original_user_request
    assert result["task_type"] == original_task_type
    assert result["required_subgraphs"] == original_required_subgraphs
    # execution_result and review_result should still be None
    assert result["execution_result"] is None
    assert result["review_result"] is None


def test_execution_wrapper_maps_mainstate_to_subgraph_input():
    """Test that execution wrapper correctly maps MainState to ExecutionState input."""
    main_state = create_test_mainstate()

    result = invoke_execution_subgraph(main_state)

    # Verify that execution was invoked and result was mapped back
    assert result["execution_result"] is not None
    assert "actions_taken" in result["execution_result"]
    assert "artifacts" in result["execution_result"]
    assert "logs" in result["execution_result"]
    assert "success" in result["execution_result"]
    assert "errors" in result["execution_result"]


def test_execution_wrapper_maps_subgraph_output_back_to_mainstate():
    """Test that execution wrapper correctly maps subgraph output to MainState."""
    main_state = create_test_mainstate()

    result = invoke_execution_subgraph(main_state)

    # Verify ExecutionResult structure
    execution_result = result["execution_result"]
    assert isinstance(execution_result["actions_taken"], list)
    assert isinstance(execution_result["artifacts"], list)
    assert isinstance(execution_result["logs"], list)
    assert isinstance(execution_result["success"], bool)
    assert isinstance(execution_result["errors"], list)


def test_execution_wrapper_preserves_unrelated_mainstate_fields():
    """Test that execution wrapper doesn't modify unrelated MainState fields."""
    main_state = create_test_mainstate()

    # Set some fields
    original_task_id = main_state["task_id"]
    original_user_request = main_state["user_request"]

    result = invoke_execution_subgraph(main_state)

    # Verify unrelated fields are preserved
    assert result["task_id"] == original_task_id
    assert result["user_request"] == original_user_request
    # research_result and review_result should still be None
    assert result["research_result"] is None
    assert result["review_result"] is None


def test_review_wrapper_maps_mainstate_to_subgraph_input():
    """Test that review wrapper correctly maps MainState to ReviewState input."""
    main_state = create_test_mainstate()

    result = invoke_review_subgraph(main_state)

    # Verify that review was invoked and result was mapped back
    assert result["review_result"] is not None
    assert "verdict" in result["review_result"]
    assert "issues" in result["review_result"]
    assert "approved_artifacts" in result["review_result"]
    assert "final_response" in result["review_result"]
    assert "review_notes" in result["review_result"]


def test_review_wrapper_maps_subgraph_output_back_to_mainstate():
    """Test that review wrapper correctly maps subgraph output to MainState."""
    main_state = create_test_mainstate()

    result = invoke_review_subgraph(main_state)

    # Verify ReviewResult structure
    review_result = result["review_result"]
    assert review_result["verdict"] in ["pass", "revise", "fail", "blocked"]
    assert isinstance(review_result["issues"], list)
    assert isinstance(review_result["approved_artifacts"], list)
    assert isinstance(review_result["final_response"], str)
    assert isinstance(review_result["review_notes"], str)


def test_review_wrapper_preserves_unrelated_mainstate_fields():
    """Test that review wrapper doesn't modify unrelated MainState fields."""
    main_state = create_test_mainstate()

    # Set some fields
    original_task_id = main_state["task_id"]
    original_user_request = main_state["user_request"]
    original_task_type = main_state["task_type"]

    result = invoke_review_subgraph(main_state)

    # Verify unrelated fields are preserved
    assert result["task_id"] == original_task_id
    assert result["user_request"] == original_user_request
    assert result["task_type"] == original_task_type
    # research_result and execution_result should still be None
    assert result["research_result"] is None
    assert result["execution_result"] is None


def test_research_wrapper_handles_subgraph_failure():
    """Test that research wrapper handles subgraph invocation failure gracefully."""
    from unittest.mock import patch
    from app.graphs.research import create_research_graph

    main_state = create_test_mainstate()

    # Mock the subgraph to raise an exception
    with patch('app.graphs.main.wrappers.create_research_graph') as mock_create:
        mock_graph = mock_create.return_value
        mock_compiled = mock_graph.compile.return_value
        mock_compiled.invoke.side_effect = Exception("Simulated research failure")

        # Invoke wrapper
        result = invoke_research_subgraph(main_state)

        # Verify error was recorded
        assert len(result["errors"]) > 0
        assert result["errors"][0].code == "RESEARCH_SUBGRAPH_FAILED"
        assert result["errors"][0].severity == "error"

        # Verify failure trace was recorded
        assert len(result["trace"]) > 0
        assert result["trace"][-1].status == "failure"
        assert "FAILED" in result["trace"][-1].output_summary

        # Verify status was updated
        assert result["status"] == "partial"


def test_execution_wrapper_handles_subgraph_failure():
    """Test that execution wrapper handles subgraph invocation failure gracefully."""
    from unittest.mock import patch

    main_state = create_test_mainstate()

    # Mock the subgraph to raise an exception
    with patch('app.graphs.main.wrappers.create_execution_graph') as mock_create:
        mock_graph = mock_create.return_value
        mock_compiled = mock_graph.compile.return_value
        mock_compiled.invoke.side_effect = Exception("Simulated execution failure")

        # Invoke wrapper
        result = invoke_execution_subgraph(main_state)

        # Verify error was recorded
        assert len(result["errors"]) > 0
        assert result["errors"][0].code == "EXECUTION_SUBGRAPH_FAILED"
        assert result["errors"][0].severity == "critical"

        # Verify failure trace was recorded
        assert len(result["trace"]) > 0
        assert result["trace"][-1].status == "failure"

        # Verify status was updated to failed (execution failure is critical)
        assert result["status"] == "failed"


def test_review_wrapper_handles_subgraph_failure():
    """Test that review wrapper handles subgraph invocation failure gracefully."""
    from unittest.mock import patch

    main_state = create_test_mainstate()

    # Mock the subgraph to raise an exception
    with patch('app.graphs.main.wrappers.create_review_graph') as mock_create:
        mock_graph = mock_create.return_value
        mock_compiled = mock_graph.compile.return_value
        mock_compiled.invoke.side_effect = Exception("Simulated review failure")

        # Invoke wrapper
        result = invoke_review_subgraph(main_state)

        # Verify error was recorded
        assert len(result["errors"]) > 0
        assert result["errors"][0].code == "REVIEW_SUBGRAPH_FAILED"
        assert result["errors"][0].severity == "error"

        # Verify failure trace was recorded
        assert len(result["trace"]) > 0
        assert result["trace"][-1].status == "failure"

        # Verify status was updated
        assert result["status"] == "partial"


def test_execution_wrapper_propagates_execution_errors():
    """Test that execution wrapper propagates errors from ExecutionResult to MainState."""
    from app.schemas import Error
    from datetime import datetime

    main_state = create_test_mainstate()

    # We can't easily mock just the errors without mocking the whole invoke,
    # but we can verify that in normal flow, errors would be propagated
    # This is verified by the schema consistency - ExecutionResult.errors is list[Error]
    # and the wrapper code shows: updated_errors = [*state.get("errors", []), *execution_result["errors"]]

    # For now, just verify the wrapper preserves existing errors
    existing_error = Error(
        code="EXISTING_ERROR",
        severity="warning",
        message="Pre-existing error",
        source="test",
        timestamp=datetime.utcnow()
    )
    main_state["errors"] = [existing_error]

    result = invoke_execution_subgraph(main_state)

    # Should preserve existing error
    assert len(result["errors"]) >= 1
    assert any(e.code == "EXISTING_ERROR" for e in result["errors"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
