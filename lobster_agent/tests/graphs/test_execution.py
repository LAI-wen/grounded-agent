"""Tests for Execution Subgraph — Phase 2B.

Covers:
- Graph compilation and initial state (Phase 1 smoke tests preserved)
- Safety check rule engine (unit)
- File operations with path validation (unit)
- Conditional safety abort routing (integration)
- Full workflow with file_write (integration, LLM mocked)
- ExecutionOutput schema correctness
- Artifact population
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.graphs.execution import create_execution_graph, create_execution_input
from app.graphs.execution.nodes.safety_check import perform_safety_check
from app.graphs.execution.nodes.execute import execute_actions
from app.graphs.execution.state import ExecutionState, ExecutionStep
from app.tools.file_ops import read_file, write_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> ExecutionState:
    """Build a minimal ExecutionState for unit tests."""
    base = create_execution_input(
        task_description="test task",
        context={},
        task_id="test-id",
    )
    return {**base, **overrides}


def _make_step(
    step_id: str = "step_1",
    tool: Optional[str] = "file_write",
    path: str = "output.txt",
    content: str = "hello",
    status: str = "pending",
) -> ExecutionStep:
    params = {"path": path}
    if tool == "file_write":
        params["content"] = content
    return {
        "step_id": step_id,
        "action": f"do {step_id}",
        "tool": tool,
        "params": params,
        "tool_input": {"path": path, "content": content} if tool == "file_write" else {"path": path},
        "status": status,
    }


def _mock_anthropic_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.content = [MagicMock()]
    mock.content[0].text = text
    return mock


# ---------------------------------------------------------------------------
# Phase 1 smoke tests (preserved)
# ---------------------------------------------------------------------------

def test_execution_graph_creation():
    graph = create_execution_graph()
    assert graph is not None
    compiled = graph.compile()
    assert compiled is not None


def test_execution_input_creation():
    state = create_execution_input(
        task_description="Test task",
        context={},
        task_id="test-123",
    )
    assert state["task_description"] == "Test task"
    assert state["task_id"] == "test-123"
    assert state["execution_plan"] == []
    assert state["success"] is False
    assert state["abort_reason"] is None


def test_execution_workflow():
    graph = create_execution_graph()
    compiled = graph.compile()

    state = create_execution_input(
        task_description="Create a test file",
        context={},
        task_id="test-exec",
    )
    result = compiled.invoke(state)

    assert len(result["execution_plan"]) > 0
    assert len(result["generated_actions"]) > 0
    assert result["simulation_result"] is not None
    assert result["safety_check"] is not None


def test_execution_logging():
    graph = create_execution_graph()
    compiled = graph.compile()

    state = create_execution_input(
        task_description="Test logging",
        context={},
        task_id="test-logs",
    )
    result = compiled.invoke(state)
    assert len(result["logs"]) > 0


# ---------------------------------------------------------------------------
# Safety check — unit tests (node called directly)
# ---------------------------------------------------------------------------

def test_safety_check_pass(tmp_path):
    """Steps with valid project-scoped paths should pass."""
    step = _make_step(tool="file_write", path="output.txt")
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is True
    assert result["safety_check"]["violations"] == []
    assert result["safety_check"]["flags"] == []


def test_safety_check_path_traversal(tmp_path):
    """Path traversal outside project root must be blocked."""
    step = _make_step(tool="file_write", path="../outside.txt")
    step["tool_input"] = {"path": "../outside.txt", "content": "bad"}
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is False
    assert len(result["safety_check"]["violations"]) > 0
    assert any(f.code == "path_violation" for f in result["safety_check"]["flags"])


def test_safety_check_absolute_path_outside_root(tmp_path):
    """Absolute path outside project root must be blocked."""
    step = _make_step(tool="file_write", path="/etc/shadow")
    step["tool_input"] = {"path": "/etc/shadow", "content": "bad"}
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is False
    assert any(f.code == "path_violation" for f in result["safety_check"]["flags"])


def test_safety_check_protected_file_env(tmp_path):
    """.env write must be blocked."""
    step = _make_step(tool="file_write", path=".env")
    step["tool_input"] = {"path": ".env", "content": "SECRET=bad"}
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is False
    assert any(f.code == "protected_file" for f in result["safety_check"]["flags"])


def test_safety_check_protected_file_pyproject(tmp_path):
    """pyproject.toml write must be blocked."""
    step = _make_step(tool="file_write", path="pyproject.toml")
    step["tool_input"] = {"path": "pyproject.toml", "content": "[project]"}
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is False
    assert any(f.code == "protected_file" for f in result["safety_check"]["flags"])


def test_safety_check_forbidden_tool(tmp_path):
    """Steps using a non-allowlist tool must be blocked."""
    step: ExecutionStep = {
        "step_id": "step_1",
        "action": "run shell",
        "tool": "shell",
        "params": {"cmd": "rm -rf /"},
        "tool_input": {"cmd": "rm -rf /"},
        "status": "pending",
    }
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is False
    assert any(f.code == "forbidden_tool" for f in result["safety_check"]["flags"])


def test_safety_check_no_tool_is_safe(tmp_path):
    """Steps with tool=None should pass without any flags."""
    step: ExecutionStep = {
        "step_id": "step_1",
        "action": "think",
        "tool": None,
        "params": {},
        "tool_input": None,
        "status": "pending",
    }
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
    )
    result = perform_safety_check(state)
    assert result["safety_check"]["passed"] is True


# ---------------------------------------------------------------------------
# File operations — unit tests
# ---------------------------------------------------------------------------

def test_file_ops_write_valid(tmp_path):
    """write_file creates a file inside project root."""
    write_file("hello.txt", "hello world", str(tmp_path))
    assert (tmp_path / "hello.txt").read_text() == "hello world"


def test_file_ops_write_creates_subdirs(tmp_path):
    """write_file creates intermediate directories as needed."""
    write_file("sub/dir/file.txt", "content", str(tmp_path))
    assert (tmp_path / "sub/dir/file.txt").exists()


def test_file_ops_write_outside_root(tmp_path):
    """write_file raises ValueError for path outside project root."""
    with pytest.raises(ValueError, match="outside project root"):
        write_file("../outside.txt", "bad", str(tmp_path))


def test_file_ops_write_absolute_outside_root(tmp_path):
    """write_file raises ValueError for absolute path outside project root."""
    with pytest.raises(ValueError):
        write_file("/etc/shadow", "bad", str(tmp_path))


def test_file_ops_write_protected_env(tmp_path):
    """write_file raises ValueError for .env files."""
    with pytest.raises(ValueError, match="protected"):
        write_file(".env", "SECRET=x", str(tmp_path))


def test_file_ops_write_protected_lock(tmp_path):
    """write_file raises ValueError for *.lock files."""
    with pytest.raises(ValueError, match="protected"):
        write_file("package.lock", "content", str(tmp_path))


def test_file_ops_read_valid(tmp_path):
    """read_file returns content for a file inside project root."""
    (tmp_path / "input.txt").write_text("read me")
    content = read_file("input.txt", str(tmp_path))
    assert content == "read me"


def test_file_ops_read_outside_root(tmp_path):
    """read_file raises ValueError for path outside project root."""
    with pytest.raises(ValueError, match="outside project root"):
        read_file("../outside.txt", str(tmp_path))


def test_file_ops_read_missing_file(tmp_path):
    """read_file raises FileNotFoundError for non-existent file."""
    with pytest.raises(FileNotFoundError):
        read_file("does_not_exist.txt", str(tmp_path))


# ---------------------------------------------------------------------------
# ExecutionOutput schema correctness
# ---------------------------------------------------------------------------

def test_execution_result_schema(tmp_path):
    """execute_actions produces a typed ExecutionOutput with all required fields."""
    step = _make_step(tool="file_write", path="out.txt", content="data")
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
        safety_check={"passed": True, "violations": [], "warnings": [], "flags": []},
    )
    result = execute_actions(state)

    er = result["execution_result"]
    assert er is not None
    assert "actions_taken" in er
    assert "artifacts" in er
    assert "logs" in er
    assert "success" in er
    assert "errors" in er


def test_artifacts_populated_on_write(tmp_path):
    """After a successful file_write, artifacts list contains one Artifact."""
    step = _make_step(tool="file_write", path="artifact.txt", content="content")
    state = _make_state(
        context={"project_root": str(tmp_path)},
        execution_plan=[step],
        safety_check={"passed": True, "violations": [], "warnings": [], "flags": []},
    )
    result = execute_actions(state)

    assert result["success"] is True
    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert artifact.path == "artifact.txt"
    assert artifact.source_subgraph == "execution"
    assert artifact.metadata["operation"] == "write"


# ---------------------------------------------------------------------------
# Safety abort routing — integration (no LLM)
# ---------------------------------------------------------------------------

def test_safety_check_failure_aborts_execute(tmp_path):
    """Full graph run with an unsafe step must not call execute."""
    graph = create_execution_graph()
    compiled = graph.compile()

    # Build state with a path-traversal step injected
    # We use a mock plan via patch so the plan node produces the bad step
    bad_step: ExecutionStep = {
        "step_id": "step_1",
        "action": "write outside project",
        "tool": "file_write",
        "params": {"path": "../bad.txt"},
        "tool_input": {"path": "../bad.txt", "content": "bad"},
        "status": "pending",
    }

    executed = []

    def mock_plan(state):
        return {**state, "execution_plan": [bad_step], "logs": [*state.get("logs", []), "mock plan"]}

    def mock_generate(state):
        return {**state, "generated_actions": ["bad action"], "logs": [*state.get("logs", []), "mock gen"]}

    def spy_execute(state):
        executed.append(True)
        return state

    with (
        patch("app.graphs.execution.nodes.plan.create_execution_plan", mock_plan),
        patch("app.graphs.execution.nodes.generate.generate_actions", mock_generate),
        patch("app.graphs.execution.nodes.execute.execute_actions", spy_execute),
    ):
        # Rebuild graph with patched nodes
        from langgraph.graph import StateGraph, END as _END
        from app.graphs.execution.nodes.simulate import simulate_execution
        from app.graphs.execution.nodes.safety_check import perform_safety_check as real_sc
        from app.graphs.execution.graph import _route_after_safety_check

        g = StateGraph(ExecutionState)
        g.add_node("plan", mock_plan)
        g.add_node("generate", mock_generate)
        g.add_node("simulate", simulate_execution)
        g.add_node("safety_check", real_sc)
        g.add_node("execute", spy_execute)
        g.set_entry_point("plan")
        g.add_edge("plan", "generate")
        g.add_edge("generate", "simulate")
        g.add_edge("simulate", "safety_check")
        g.add_conditional_edges("safety_check", _route_after_safety_check, {"execute": "execute", _END: _END})
        g.add_edge("execute", _END)

        init = create_execution_input(
            task_description="bad task",
            context={"project_root": str(tmp_path)},
            task_id="test-abort",
        )
        result = g.compile().invoke(init)

    assert result["safety_check"]["passed"] is False
    assert executed == [], "execute_actions must NOT be called when safety fails"


# ---------------------------------------------------------------------------
# Full workflow with mocked LLM — file_write integration
# ---------------------------------------------------------------------------

@patch("app.graphs.execution.nodes.generate.Anthropic")
@patch("app.graphs.execution.nodes.plan.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_full_workflow_file_write(mock_plan_cls, mock_gen_cls, tmp_path):
    """Full graph run with mocked LLM produces a file on disk."""
    plan_data = [
        {
            "step_id": "step_1",
            "action": "Write greeting file",
            "tool": "file_write",
            "params": {"path": "hello.txt", "content_purpose": "greeting"},
            "status": "pending",
        }
    ]
    gen_data = [
        {
            "step_id": "step_1",
            "tool_input": {"path": "hello.txt", "content": "Hello, Phase 2B!"},
        }
    ]

    mock_plan_client = MagicMock()
    mock_plan_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(plan_data)
    )
    mock_plan_cls.return_value = mock_plan_client

    mock_gen_client = MagicMock()
    mock_gen_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(gen_data)
    )
    mock_gen_cls.return_value = mock_gen_client

    graph = create_execution_graph()
    compiled = graph.compile()

    init = create_execution_input(
        task_description="Write a greeting file",
        context={"project_root": str(tmp_path)},
        task_id="test-write",
    )
    result = compiled.invoke(init)

    # File should be on disk
    output_file = tmp_path / "hello.txt"
    assert output_file.exists(), "hello.txt should have been created"
    assert output_file.read_text() == "Hello, Phase 2B!"

    # State assertions
    assert result["success"] is True
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0].path == "hello.txt"
    assert result["safety_check"]["passed"] is True


@patch("app.graphs.execution.nodes.generate.Anthropic")
@patch("app.graphs.execution.nodes.plan.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_full_workflow_safety_abort_via_llm(mock_plan_cls, mock_gen_cls, tmp_path):
    """Full graph run where LLM produces an unsafe step is aborted before execute."""
    plan_data = [
        {
            "step_id": "step_1",
            "action": "Write outside project",
            "tool": "file_write",
            "params": {"path": "../outside.txt"},
            "status": "pending",
        }
    ]
    gen_data = [
        {
            "step_id": "step_1",
            "tool_input": {"path": "../outside.txt", "content": "bad"},
        }
    ]

    mock_plan_client = MagicMock()
    mock_plan_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(plan_data)
    )
    mock_plan_cls.return_value = mock_plan_client

    mock_gen_client = MagicMock()
    mock_gen_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(gen_data)
    )
    mock_gen_cls.return_value = mock_gen_client

    graph = create_execution_graph()
    compiled = graph.compile()

    init = create_execution_input(
        task_description="Write outside project",
        context={"project_root": str(tmp_path)},
        task_id="test-abort-llm",
    )
    result = compiled.invoke(init)

    assert result["safety_check"]["passed"] is False
    # execute_result is None because execute node was never called
    assert result["execution_result"] is None
    # No files should have been written
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# V2 M2: workspace_context injection into planner
# ---------------------------------------------------------------------------

@patch("app.graphs.execution.nodes.plan.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_plan_workspace_context_appears_in_prompt(mock_plan_cls):
    """workspace_context from context dict is formatted into the planner prompt."""
    from app.graphs.execution.nodes.plan import create_execution_plan

    workspace_ctx = (
        "\nWorkspace history (recent tasks):\n"
        "- [2026-03-20] execution: \"Create notes.txt\" → success, artifacts: [notes.txt]\n"
    )

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps([{
            "step_id": "step_1", "action": "read notes", "tool": "file_read",
            "params": {"path": "notes.txt"}, "status": "pending",
        }, {
            "step_id": "step_2", "action": "write notes", "tool": "file_write",
            "params": {"path": "notes.txt", "content_purpose": "updated"}, "status": "pending",
        }])
    )
    mock_plan_cls.return_value = mock_client

    state = _make_state(
        task_description="Add a new section to notes.txt",
        context={"project_root": "/tmp/proj", "workspace_context": workspace_ctx},
    )
    create_execution_plan(state)

    call_kwargs = mock_client.messages.create.call_args[1]
    prompt = call_kwargs["messages"][0]["content"]
    assert "Known existing artifacts from workspace context" in prompt
    assert "notes.txt" in prompt


@patch("app.graphs.execution.nodes.plan.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_plan_no_workspace_context_omits_artifacts_block(mock_plan_cls):
    """When workspace_context is absent the artifacts block is not injected."""
    from app.graphs.execution.nodes.plan import create_execution_plan

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps([{
            "step_id": "step_1", "action": "write file", "tool": "file_write",
            "params": {"path": "out.txt", "content_purpose": "content"}, "status": "pending",
        }])
    )
    mock_plan_cls.return_value = mock_client

    state = _make_state(
        task_description="Create out.txt",
        context={"project_root": "/tmp/proj"},
    )
    create_execution_plan(state)

    call_kwargs = mock_client.messages.create.call_args[1]
    prompt = call_kwargs["messages"][0]["content"]
    assert "Known existing artifacts from workspace context" not in prompt


@patch("app.graphs.execution.nodes.generate.Anthropic")
@patch("app.graphs.execution.nodes.plan.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_full_workflow_read_before_write_for_known_artifact(mock_plan_cls, mock_gen_cls, tmp_path):
    """Full graph run: LLM returns file_read + file_write plan for a known artifact;
    both steps execute and the final file incorporates both read and written content."""
    original_content = "Original line.\n"
    notes_path = tmp_path / "notes.txt"
    notes_path.write_text(original_content)

    workspace_ctx = (
        "\nWorkspace history (recent tasks):\n"
        "- [2026-03-20] execution: \"Create notes.txt\" → success, artifacts: [notes.txt]\n"
    )
    # Write action contains "append" keyword — triggers execute's combine logic.
    # Generator produces ONLY the new content; execute prepends the file_read result.
    plan_data = [
        {
            "step_id": "step_1",
            "action": "Read existing notes.txt",
            "tool": "file_read",
            "params": {"path": "notes.txt"},
            "status": "pending",
        },
        {
            "step_id": "step_2",
            "action": "Append new section to existing notes.txt contents",
            "tool": "file_write",
            "params": {"path": "notes.txt", "content_purpose": "new section only"},
            "status": "pending",
        },
    ]
    # Generator produces only the new content (APPEND mode)
    gen_data = [
        {"step_id": "step_1", "tool_input": {"path": "notes.txt"}},
        {
            "step_id": "step_2",
            "tool_input": {
                "path": "notes.txt",
                "content": "New section.\n",  # new content only — execute combines
            },
        },
    ]

    mock_plan_client = MagicMock()
    mock_plan_client.messages.create.return_value = _mock_anthropic_response(json.dumps(plan_data))
    mock_plan_cls.return_value = mock_plan_client

    mock_gen_client = MagicMock()
    mock_gen_client.messages.create.return_value = _mock_anthropic_response(json.dumps(gen_data))
    mock_gen_cls.return_value = mock_gen_client

    graph = create_execution_graph()
    compiled = graph.compile()

    init = create_execution_input(
        task_description="Add a new section to notes.txt",
        context={"project_root": str(tmp_path), "workspace_context": workspace_ctx},
        task_id="test-read-before-write",
    )
    result = compiled.invoke(init)

    assert result["success"] is True
    final_content = notes_path.read_text()
    # Original content preserved AND new content added
    assert "Original line." in final_content, "Original content must be preserved"
    assert "New section." in final_content, "New content must be present"
    assert result["execution_result"]["status"] == "success"
    assert len([a for a in result["execution_result"]["actions_taken"] if "No-op" not in a]) == 2


def test_execute_combines_read_result_for_append_action(tmp_path):
    """execute_actions prepends file_read content for write steps with append keyword."""
    original = "Existing line.\n"
    notes = tmp_path / "notes.txt"
    notes.write_text(original)

    safety = {"passed": True, "violations": [], "warnings": [], "flags": []}
    plan = [
        {
            "step_id": "step_1", "action": "Read notes.txt", "tool": "file_read",
            "params": {"path": "notes.txt"}, "tool_input": {"path": "notes.txt"},
            "status": "pending",
        },
        {
            "step_id": "step_2",
            "action": "Append new entry to existing notes.txt contents",
            "tool": "file_write",
            "params": {"path": "notes.txt"},
            "tool_input": {"path": "notes.txt", "content": "New entry.\n"},
            "status": "pending",
        },
    ]
    state = _make_state(
        execution_plan=plan,
        safety_check=safety,
        context={"project_root": str(tmp_path)},
    )
    result = execute_actions(state)

    assert result["success"] is True
    final = notes.read_text()
    assert "Existing line." in final, "Original content must be preserved"
    assert "New entry." in final, "New content must be appended"
    assert final.index("Existing line.") < final.index("New entry."), "Original must precede new"


def test_execute_does_not_combine_without_append_keyword(tmp_path):
    """execute_actions does NOT combine when write action lacks append keyword — plain replace."""
    original = "Original content.\n"
    notes = tmp_path / "notes.txt"
    notes.write_text(original)

    safety = {"passed": True, "violations": [], "warnings": [], "flags": []}
    plan = [
        {
            "step_id": "step_1", "action": "Read notes.txt", "tool": "file_read",
            "params": {"path": "notes.txt"}, "tool_input": {"path": "notes.txt"},
            "status": "pending",
        },
        {
            "step_id": "step_2",
            "action": "Write notes.txt with new content",  # no append keyword
            "tool": "file_write",
            "params": {"path": "notes.txt"},
            "tool_input": {"path": "notes.txt", "content": "Replacement content.\n"},
            "status": "pending",
        },
    ]
    state = _make_state(
        execution_plan=plan,
        safety_check=safety,
        context={"project_root": str(tmp_path)},
    )
    result = execute_actions(state)

    assert result["success"] is True
    final = notes.read_text()
    # Replace semantics: original should NOT be in the file
    assert "Original content." not in final
    assert "Replacement content." in final


def test_plan_prompt_contains_preservation_instructions():
    """PLAN_EXECUTION_SYSTEM_PROMPT includes content-preservation rules for update steps."""
    from app.graphs.execution.prompts.templates import PLAN_EXECUTION_SYSTEM_PROMPT
    lowered = PLAN_EXECUTION_SYSTEM_PROMPT.lower()
    assert "preserve" in lowered
    assert "append" in lowered
    assert "replace" in lowered or "discard" in lowered


def test_generate_prompt_contains_append_mode_instructions():
    """GENERATE_ACTIONS_SYSTEM_PROMPT distinguishes APPEND mode from REPLACE mode."""
    from app.graphs.execution.prompts.templates import GENERATE_ACTIONS_SYSTEM_PROMPT
    assert "APPEND mode" in GENERATE_ACTIONS_SYSTEM_PROMPT
    assert "REPLACE mode" in GENERATE_ACTIONS_SYSTEM_PROMPT
    assert "file_read result" in GENERATE_ACTIONS_SYSTEM_PROMPT.lower() or \
           "file_read" in GENERATE_ACTIONS_SYSTEM_PROMPT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
