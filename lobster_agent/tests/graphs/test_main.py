"""Smoke tests for Parent Graph.

These tests verify basic functionality of the Main Graph orchestration.
"""

import pytest
from app.graphs.main import create_main_graph, create_initial_state


def test_main_graph_creation():
    """Test that main graph can be created successfully."""
    graph = create_main_graph()
    assert graph is not None
    compiled = graph.compile()
    assert compiled is not None


def test_initial_state_creation():
    """Test that initial state is created with correct structure."""
    user_request = "Test request"
    state = create_initial_state(user_request)

    assert state["user_request"] == user_request
    assert state["status"] == "pending"
    assert state["task_id"] is not None
    assert state["required_subgraphs"] == []
    assert state["artifacts"] == []
    assert state["errors"] == []
    assert state["trace"] == []


def test_research_only_workflow():
    """Test workflow with research-only task type."""
    graph = create_main_graph()
    compiled = graph.compile()

    initial_state = create_initial_state("Research AI developments")
    result = compiled.invoke(initial_state)

    assert result["status"] in ["success", "partial"]
    assert result["task_type"] == "research"
    assert "research" in result["required_subgraphs"]
    assert result["research_result"] is not None
    assert len(result["trace"]) > 0


def test_execution_workflow():
    """Test workflow with execution task type."""
    graph = create_main_graph()
    compiled = graph.compile()

    initial_state = create_initial_state("Create a new file")
    result = compiled.invoke(initial_state)

    assert result["status"] in ["success", "partial", "blocked", "no_op"]
    assert result["task_type"] == "execution"
    assert "execution" in result["required_subgraphs"]
    assert "review" in result["required_subgraphs"]
    assert result["execution_result"] is not None
    assert result["review_result"] is not None


def test_hybrid_workflow():
    """Test workflow with hybrid task type."""
    graph = create_main_graph()
    compiled = graph.compile()

    initial_state = create_initial_state("Research and implement a solution")
    result = compiled.invoke(initial_state)

    assert result["status"] in ["success", "partial", "blocked", "no_op"]
    assert result["task_type"] == "hybrid"
    assert "research" in result["required_subgraphs"]
    assert "execution" in result["required_subgraphs"]
    assert "review" in result["required_subgraphs"]
    assert result["research_result"] is not None
    assert result["execution_result"] is not None
    assert result["review_result"] is not None


def test_trace_recording():
    """Test that workflow traces are recorded."""
    graph = create_main_graph()
    compiled = graph.compile()

    initial_state = create_initial_state("Test trace recording")
    result = compiled.invoke(initial_state)

    assert len(result["trace"]) > 0
    # Should have at least: normalize_task, subgraph invocations, finalize
    assert any(t.node_name == "normalize_task" for t in result["trace"])
    assert any(t.node_name == "finalize_response" for t in result["trace"])


def test_router_progresses_through_required_subgraphs():
    """Test that router correctly progresses through required_subgraphs list."""
    from app.graphs.main.router import route_next_step

    # Test progression through hybrid workflow
    state = create_initial_state("Research and implement")
    state["task_type"] = "hybrid"
    state["required_subgraphs"] = ["research", "execution", "review"]
    state["status"] = "running"

    # Start: no current_subgraph
    next_step = route_next_step(state)
    assert next_step == "research"

    # After research
    state["current_subgraph"] = "research"
    next_step = route_next_step(state)
    assert next_step == "execution"

    # After execution
    state["current_subgraph"] = "execution"
    next_step = route_next_step(state)
    assert next_step == "review"

    # After review (last subgraph)
    state["current_subgraph"] = "review"
    next_step = route_next_step(state)
    assert next_step == "finalize"


def test_error_node_sets_failed_status_and_keeps_trace():
    """Test that error handling preserves trace and sets failed status."""
    from app.graphs.main.nodes import handle_error
    from app.schemas import TraceEntry, Error
    from datetime import datetime

    # Create state with some trace entries and errors
    state = create_initial_state("Test error handling")
    state["trace"] = [
        TraceEntry(
            step_id="trace_0",
            node_name="normalize_task",
            subgraph=None,
            timestamp=datetime.utcnow(),
            input_summary="test input",
            output_summary="test output",
            status="success"
        )
    ]
    state["errors"] = [
        Error(
            code="TEST_ERROR",
            severity="error",
            message="Test error occurred",
            source="test_node",
            timestamp=datetime.utcnow()
        )
    ]

    # Handle error
    result = handle_error(state)

    # Verify status is failed
    assert result["status"] == "failed"

    # Verify trace was preserved and extended
    assert len(result["trace"]) > len(state["trace"])
    assert result["trace"][0].node_name == "normalize_task"  # Original trace preserved

    # Verify errors were preserved
    assert len(result["errors"]) >= 1
    assert result["errors"][0].code == "TEST_ERROR"


def test_invalid_required_subgraphs_routes_to_error():
    """Test that invalid routing scenarios route to error node."""
    from app.graphs.main.router import route_next_step

    # Test case: current_subgraph not in required_subgraphs (corrupted state)
    state = create_initial_state("Test invalid routing")
    state["required_subgraphs"] = ["research", "execution"]
    state["current_subgraph"] = "review"  # Not in required list!
    state["status"] = "running"

    next_step = route_next_step(state)
    # Should route to error because current not in required
    assert next_step == "error"


def test_failed_status_routes_to_error():
    """Test that failed status immediately routes to error."""
    from app.graphs.main.router import route_next_step

    state = create_initial_state("Test failed routing")
    state["status"] = "failed"
    state["required_subgraphs"] = ["research"]

    next_step = route_next_step(state)
    assert next_step == "error"


# ---------------------------------------------------------------------------
# Phase 3 — Integration hardening: end-to-end tests
# ---------------------------------------------------------------------------

def test_e2e_messages_populated_on_execution_workflow(tmp_path):
    """End-to-end: messages list contains user + assistant entries after an
    execution-only task with a mocked LLM and a real file write.

    This verifies:
    - normalize_task appends a user Message
    - finalize_response appends an assistant Message
    - execution_result.success is True
    - review_result.verdict is 'pass' (no structural issues)
    - the written file exists on disk
    """
    import json
    import os
    from unittest.mock import MagicMock, patch

    target_path = str(tmp_path / "hello.txt")
    file_content = "Hello from Lobster Agent"

    # Plan: one file_write step
    plan_msg = MagicMock()
    plan_msg.content = [MagicMock(text=json.dumps([
        {
            "step_id": "s1",
            "action": "Write hello.txt",
            "tool": "file_write",
            "params": {"path": target_path, "content": file_content},
            "status": "pending",
        }
    ]))]

    # Generate: fills tool_input for the step
    gen_msg = MagicMock()
    gen_msg.content = [MagicMock(text=json.dumps([
        {"step_id": "s1", "tool_input": {"path": target_path, "content": file_content}}
    ]))]

    # Review inspect: no quality issues
    inspect_msg = MagicMock()
    inspect_msg.content = [MagicMock(text="[]")]

    plan_client = MagicMock()
    plan_client.messages.create.return_value = plan_msg

    gen_client = MagicMock()
    gen_client.messages.create.return_value = gen_msg

    inspect_client = MagicMock()
    inspect_client.messages.create.return_value = inspect_msg

    # Make tmp_path the effective project root so safety_check and execute
    # accept the absolute tmp_path target without a path-boundary violation.
    with (
        patch("app.graphs.execution.nodes.plan.Anthropic", return_value=plan_client),
        patch("app.graphs.execution.nodes.generate.Anthropic", return_value=gen_client),
        patch("app.graphs.review.nodes.inspect.Anthropic", return_value=inspect_client),
        patch("app.graphs.execution.nodes.safety_check.os.getcwd", return_value=str(tmp_path)),
        patch("app.graphs.execution.nodes.execute.os.getcwd", return_value=str(tmp_path)),
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        graph = create_main_graph()
        compiled = graph.compile()
        state = create_initial_state("Create a file called hello.txt")
        result = compiled.invoke(state)

    # messages: user turn + assistant turn
    messages = result.get("messages", [])
    assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Create a file called hello.txt"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"]  # non-empty

    # Execution succeeded
    assert result["execution_result"] is not None
    assert result["execution_result"]["success"] is True

    # Review passed
    assert result["review_result"] is not None
    assert result["review_result"]["verdict"] == "pass"

    # Overall status
    assert result["status"] == "success"

    # File was actually written to disk
    assert os.path.exists(target_path)
    with open(target_path) as f:
        assert f.read() == file_content


def test_e2e_safety_failure_produces_assistant_message(tmp_path):
    """End-to-end: when LLM produces an unsafe plan (path outside project root),
    safety check aborts execution, review fails, and the assistant message
    describes the failure.

    This verifies:
    - workflow completes without raising an exception
    - messages has user + assistant entries
    - assistant message is non-empty
    - execution_result.success is False (safety aborted execution)
    - review_result.verdict is 'blocked' (safety aborted, no execution attempted)
    - status is 'blocked'
    """
    import json
    import os
    from unittest.mock import MagicMock, patch

    # Unsafe: absolute path outside any project root
    unsafe_path = "/etc/passwd"

    plan_msg = MagicMock()
    plan_msg.content = [MagicMock(text=json.dumps([
        {
            "step_id": "s1",
            "action": "Write to system file",
            "tool": "file_write",
            "params": {"path": unsafe_path, "content": "injected"},
            "status": "pending",
        }
    ]))]

    gen_msg = MagicMock()
    gen_msg.content = [MagicMock(text=json.dumps([
        {"step_id": "s1", "tool_input": {"path": unsafe_path, "content": "injected"}}
    ]))]

    plan_client = MagicMock()
    plan_client.messages.create.return_value = plan_msg

    gen_client = MagicMock()
    gen_client.messages.create.return_value = gen_msg

    with (
        patch("app.graphs.execution.nodes.plan.Anthropic", return_value=plan_client),
        patch("app.graphs.execution.nodes.generate.Anthropic", return_value=gen_client),
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        graph = create_main_graph()
        compiled = graph.compile()
        state = create_initial_state("Create a file in /etc")
        result = compiled.invoke(state)

    # Workflow completed without exception
    assert result is not None

    # messages: user turn + assistant turn
    messages = result.get("messages", [])
    assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"]  # non-empty

    # Safety blocked execution — success must be False
    assert result["execution_result"] is not None
    assert result["execution_result"]["success"] is False

    # Safety aborted execution — verdict must be blocked
    assert result["review_result"] is not None
    assert result["review_result"]["verdict"] == "blocked"

    # Overall status reflects blocked execution
    assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# Priority 2 — normalize_task LLM upgrade tests
# ---------------------------------------------------------------------------

class TestNormalizeTaskLLMPath:
    """Tests for the LLM-backed normalize_task path."""

    def _make_llm_response(self, payload: dict):
        """Build a mock Anthropic response returning JSON payload."""
        import json
        from unittest.mock import MagicMock
        msg = MagicMock()
        msg.content = [MagicMock(text=json.dumps(payload))]
        client = MagicMock()
        client.messages.create.return_value = msg
        return client

    def test_llm_path_populates_all_normalized_task_fields(self):
        """LLM response is parsed into all NormalizedTask fields."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "execution",
            "objective": "Create hello.txt with greeting content",
            "constraints": ["file must be in project root"],
            "requested_outputs": ["file"],
            "assumptions": ["project root is writable"],
            "priority": "low",
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state(
                "Create a file called hello.txt with the content 'Hello'"
            ))

        nt = result["normalized_task"]
        assert result["task_type"] == "execution"
        assert nt["objective"] == "Create hello.txt with greeting content"
        assert nt["constraints"] == ["file must be in project root"]
        assert nt["requested_outputs"] == ["file"]
        assert nt["assumptions"] == ["project root is writable"]
        assert nt["priority"] == "low"

    def test_llm_path_task_type_research(self):
        """LLM-classified research task routes correctly."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "research",
            "objective": "Summarise recent AI developments",
            "constraints": [],
            "requested_outputs": ["response"],
            "assumptions": [],
            "priority": "medium",
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state(
                "Research the latest developments in AI agents"
            ))

        assert result["task_type"] == "research"
        assert result["required_subgraphs"] == ["research"]

    def test_llm_path_task_type_hybrid(self):
        """LLM-classified hybrid task includes all three subgraphs."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "hybrid",
            "objective": "Research LangGraph usage and write a summary file",
            "constraints": [],
            "requested_outputs": ["file"],
            "assumptions": [],
            "priority": "medium",
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state(
                "Research LangGraph and write a summary to docs/summary.md"
            ))

        assert result["task_type"] == "hybrid"
        assert result["required_subgraphs"] == ["research", "execution", "review"]

    def test_llm_path_requested_outputs_contains_file_for_write_request(self):
        """When LLM signals file output, requested_outputs includes 'file',
        which ensures task_expects_artifacts() fires in review."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "execution",
            "objective": "Create script.py",
            "constraints": [],
            "requested_outputs": ["file"],
            "assumptions": [],
            "priority": "medium",
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state("Create a Python script"))

        assert "file" in result["normalized_task"]["requested_outputs"]

    def test_llm_path_priority_values(self):
        """All three valid priority values are accepted as-is."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        for priority in ("low", "medium", "high"):
            payload = {
                "task_type": "execution",
                "objective": "Do something",
                "constraints": [],
                "requested_outputs": ["response"],
                "assumptions": [],
                "priority": priority,
            }
            mock_client = self._make_llm_response(payload)
            with (
                patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                result = normalize_task(create_initial_state("Do something"))
            assert result["normalized_task"]["priority"] == priority

    def test_llm_path_invalid_priority_defaults_to_medium(self):
        """Unrecognised priority string from LLM is coerced to 'medium'."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "execution",
            "objective": "Do something",
            "constraints": [],
            "requested_outputs": ["response"],
            "assumptions": [],
            "priority": "urgent",          # not a valid Literal
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state("Do something"))

        assert result["normalized_task"]["priority"] == "medium"

    def test_llm_path_invalid_task_type_falls_back_to_heuristic(self):
        """Unrecognised task_type from LLM triggers fallback — no exception raised."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "unknown_type",   # invalid
            "objective": "Do something",
            "constraints": [],
            "requested_outputs": ["response"],
            "assumptions": [],
            "priority": "medium",
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state("Research something"))

        # Falls back to heuristic: "research" keyword → task_type="research"
        assert result["task_type"] == "research"
        assert "fallback" in result["trace"][-1].output_summary.lower()

    def test_llm_path_malformed_json_falls_back_to_heuristic(self):
        """Malformed JSON from LLM triggers fallback — no exception raised."""
        import os
        from unittest.mock import MagicMock, patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        bad_msg = MagicMock()
        bad_msg.content = [MagicMock(text="This is not JSON at all.")]
        bad_client = MagicMock()
        bad_client.messages.create.return_value = bad_msg

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=bad_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state("Create a file"))

        # Heuristic fires: "create" → execution
        assert result["task_type"] == "execution"
        assert "fallback" in result["trace"][-1].output_summary.lower()

    def test_llm_path_api_exception_falls_back_to_heuristic(self):
        """API exception triggers fallback — no exception propagates."""
        import os
        from unittest.mock import MagicMock, patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        err_client = MagicMock()
        err_client.messages.create.side_effect = RuntimeError("API unavailable")

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=err_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state("Find information"))

        # Heuristic fires: "find" → research
        assert result["task_type"] == "research"
        assert "fallback" in result["trace"][-1].output_summary.lower()

    def test_llm_path_user_message_written_to_messages(self):
        """User message is written to state.messages regardless of LLM path."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        payload = {
            "task_type": "execution",
            "objective": "Write a file",
            "constraints": [],
            "requested_outputs": ["file"],
            "assumptions": [],
            "priority": "medium",
        }
        mock_client = self._make_llm_response(payload)

        with (
            patch("app.graphs.main.nodes.Anthropic", return_value=mock_client),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            result = normalize_task(create_initial_state("Write a file"))

        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Write a file"


class TestNormalizeTaskFallbackPath:
    """Tests that the keyword heuristic fallback behaves exactly as before."""

    def test_fallback_on_no_api_key(self):
        """No API key → heuristic runs, no exception, fields populated."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = normalize_task(create_initial_state("Research AI developments"))

        assert result["task_type"] == "research"
        assert result["normalized_task"]["objective"] == "Research AI developments"
        assert result["normalized_task"]["requested_outputs"] == ["response"]
        assert result["normalized_task"]["priority"] == "medium"
        assert "fallback" in result["trace"][-1].output_summary.lower()

    def test_fallback_execution_keyword(self):
        """'create' keyword → execution via heuristic."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = normalize_task(create_initial_state("Create a new file"))

        assert result["task_type"] == "execution"

    def test_fallback_hybrid_when_both_keywords(self):
        """Both research + execution keywords → hybrid via heuristic."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = normalize_task(
                create_initial_state("Research LangGraph and implement a solution")
            )

        assert result["task_type"] == "hybrid"

    def test_fallback_default_hybrid_on_unknown_request(self):
        """Request with no keywords → default hybrid via heuristic."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = normalize_task(create_initial_state("Help me with this"))

        assert result["task_type"] == "hybrid"

    def test_fallback_user_message_written(self):
        """User message is written to state.messages in fallback path."""
        import os
        from unittest.mock import patch
        from app.graphs.main.nodes import normalize_task
        from app.graphs.main import create_initial_state

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = normalize_task(create_initial_state("Find something"))

        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
