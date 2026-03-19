"""Tests for Review Subgraph — Phase 2C.

Covers:
- Graph compilation and initial state (Phase 1 smoke tests updated)
- Structural validator unit tests
- Inspect node: structural path, LLM path (mocked), fallback behaviour
- Verdict node: all four verdict outcomes and review_notes content
- Assemble node: final_response content and approved_artifacts selection
- Full graph integration (LLM mocked for quality check)
"""

import json
from datetime import datetime
from typing import Optional
from unittest.mock import MagicMock, patch
import os

import pytest

from app.graphs.review import create_review_graph, create_review_input
from app.graphs.review.nodes.inspect import inspect_outputs
from app.graphs.review.nodes.verdict import generate_verdict
from app.graphs.review.nodes.assemble import assemble_response
from app.graphs.review.services.validator import (
    check_execution_result_present,
    check_execution_success,
    check_no_execution_errors,
    check_artifacts_present,
    check_artifact_fields_complete,
    check_logs_non_empty,
    run_structural_checks,
    task_expects_artifacts,
)
from app.graphs.review.state import ReviewIssue, ReviewState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_artifact(
    name: str = "output.txt",
    path: Optional[str] = "output.txt",
    type_: str = "file",
    value: Optional[str] = "hello",
) -> dict:
    return {
        "type": type_,
        "name": name,
        "path": path,
        "value": value,
        "source_subgraph": "execution",
        "created_at": datetime.utcnow().isoformat(),
        "metadata": {},
    }


def _make_exec_result(
    success: bool = True,
    errors: Optional[list] = None,
    actions_taken: Optional[list] = None,
    logs: Optional[list] = None,
) -> dict:
    return {
        "actions_taken": actions_taken or ["[step_1] file_write: Written: output.txt"],
        "artifacts": [],
        "logs": logs or ["Plan created", "Safety check passed", "Execution complete"],
        "success": success,
        "errors": errors or [],
    }


def _make_review_target(
    exec_result=None,
    artifacts=None,
) -> dict:
    return {
        "execution_result": exec_result if exec_result is not None else _make_exec_result(),
        "artifacts": artifacts if artifacts is not None else [_make_artifact()],
    }


def _make_state(**overrides) -> ReviewState:
    base = create_review_input(
        review_target=_make_review_target(),
        expected_outputs=["response"],
        task_id="test-id",
    )
    return {**base, **overrides}


def _make_issue(
    severity: str = "major",
    description: str = "test issue",
    issue_type: str = "test_rule",
) -> ReviewIssue:
    return ReviewIssue(
        issue_type=issue_type,
        severity=severity,
        description=description,
        location=None,
    )


def _mock_anthropic_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.content = [MagicMock()]
    mock.content[0].text = text
    return mock


# ---------------------------------------------------------------------------
# Phase 1 smoke tests (updated for Phase 2C)
# ---------------------------------------------------------------------------

def test_review_graph_creation():
    graph = create_review_graph()
    assert graph is not None
    assert graph.compile() is not None


def test_review_input_creation():
    state = create_review_input(
        review_target={"output": "test"},
        expected_outputs=["response"],
        task_id="test-123",
    )
    assert state["review_target"] == {"output": "test"}
    assert state["expected_outputs"] == ["response"]
    assert state["task_id"] == "test-123"
    assert state["verdict"] == "pass"
    assert state["issues"] == []
    assert state["validation_checks"] == []           # NEW field


def test_review_workflow():
    """Full graph run with healthy input completes and populates all fields."""
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(),
        expected_outputs=["response"],
        task_id="test-review",
    )
    result = compiled.invoke(state)

    assert result["verdict"] in ["pass", "revise", "fail"]
    assert result["final_response"] != ""
    assert result["review_notes"] != ""
    assert "issues" in result
    assert "approved_artifacts" in result
    assert "validation_checks" in result
    assert len(result["validation_checks"]) > 0    # NEW: checks are now run


def test_review_workflow_healthy_result_passes():
    """Healthy execution result produces a pass verdict."""
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True, errors=[]),
            artifacts=[_make_artifact()],
        ),
        expected_outputs=["response"],
        task_id="test-pass",
    )
    result = compiled.invoke(state)
    assert result["verdict"] == "pass"


def test_review_workflow_failed_execution_fails():
    """Failed execution_result produces a fail verdict."""
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(
            exec_result=_make_exec_result(
                success=False,
                errors=[{"code": "TOOL_ERROR", "severity": "error",
                          "message": "tool failed", "source": "execute",
                          "timestamp": datetime.utcnow().isoformat()}],
            ),
        ),
        expected_outputs=["response"],
        task_id="test-fail",
    )
    result = compiled.invoke(state)
    assert result["verdict"] == "fail"


def test_review_workflow_absent_execution_result_blocked():
    """Missing execution_result (safety-aborted run) produces a blocked verdict."""
    graph = create_review_graph()
    compiled = graph.compile()

    # No execution_result key → maps to empty dict → safety-abort case
    state = create_review_input(
        review_target={"output": "test output"},
        expected_outputs=["output"],
        task_id="test-missing",
    )
    result = compiled.invoke(state)
    assert result["verdict"] == "blocked"


def test_review_logging():
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(),
        expected_outputs=["response"],
        task_id="test-logs",
    )
    result = compiled.invoke(state)
    assert len(result["logs"]) > 0


# ---------------------------------------------------------------------------
# Structural validator — unit tests
# ---------------------------------------------------------------------------

def test_structural_check_passes_complete_result():
    checks = run_structural_checks(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True, errors=[]),
            artifacts=[_make_artifact()],
        ),
        expected_outputs=["response"],
    )
    failures = [c for c in checks if not c["passed"]]
    assert failures == []


def test_structural_check_fails_null_result():
    checks = run_structural_checks(
        review_target={"execution_result": None, "artifacts": []},
        expected_outputs=["response"],
    )
    assert len(checks) == 1
    assert checks[0]["rule"] == "execution_result_present"
    assert checks[0]["passed"] is False
    assert checks[0]["severity"] == "critical"


def test_structural_check_fails_on_execution_error():
    checks = run_structural_checks(
        review_target=_make_review_target(
            exec_result=_make_exec_result(
                success=False,
                errors=[{"code": "ERR", "severity": "error", "message": "x",
                          "source": "s", "timestamp": "t"}],
            ),
        ),
        expected_outputs=["response"],
    )
    rules = {c["rule"]: c for c in checks}
    assert rules["execution_success"]["passed"] is False
    assert rules["no_execution_errors"]["passed"] is False


def test_structural_check_flags_empty_artifacts_when_expected():
    """artifacts_present fires when file_write actions indicate artifact output was expected."""
    checks = run_structural_checks(
        review_target=_make_review_target(
            exec_result=_make_exec_result(
                actions_taken=["[step_1] file_write: Written: output.txt"]
            ),
            artifacts=[],
        ),
        expected_outputs=["response"],
    )
    rules = {c["rule"]: c for c in checks}
    assert "artifacts_present" in rules
    assert rules["artifacts_present"]["passed"] is False
    assert rules["artifacts_present"]["severity"] == "major"


def test_structural_check_skips_artifacts_present_when_not_expected():
    """artifacts_present is NOT run when no file_write actions and no artifact-implying expected_outputs."""
    checks = run_structural_checks(
        review_target=_make_review_target(
            exec_result=_make_exec_result(
                actions_taken=["[step_1] No-op: think"],
            ),
            artifacts=[],
        ),
        expected_outputs=["response"],
    )
    rules = {c["rule"] for c in checks}
    assert "artifacts_present" not in rules


def test_structural_check_flags_incomplete_artifact():
    incomplete = {"type": "file", "name": "out.txt", "path": None, "value": None,
                  "source_subgraph": "execution", "created_at": datetime.utcnow(), "metadata": {}}
    check = check_artifact_fields_complete([incomplete])
    assert check["passed"] is False
    assert check["severity"] == "major"


def test_structural_check_flags_empty_logs():
    check = check_logs_non_empty({"logs": [], "success": True, "errors": []})
    assert check["passed"] is False
    assert check["severity"] == "minor"


def test_task_expects_artifacts_via_action():
    target = _make_review_target(
        exec_result=_make_exec_result(
            actions_taken=["[step_1] file_write: Written: hello.txt"]
        )
    )
    assert task_expects_artifacts(target, ["response"]) is True


def test_task_expects_artifacts_via_expected_outputs():
    target = _make_review_target(exec_result=_make_exec_result(actions_taken=[]))
    assert task_expects_artifacts(target, ["file", "response"]) is True


def test_task_does_not_expect_artifacts_for_noop():
    target = _make_review_target(
        exec_result=_make_exec_result(actions_taken=["[step_1] No-op: think"])
    )
    assert task_expects_artifacts(target, ["response"]) is False


# ---------------------------------------------------------------------------
# Inspect node — unit tests (no LLM)
# ---------------------------------------------------------------------------

def test_inspect_healthy_execution_no_issues():
    state = _make_state(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True, errors=[]),
            artifacts=[_make_artifact()],
        ),
    )
    result = inspect_outputs(state)
    assert result["issues"] == []
    assert len(result["validation_checks"]) > 0
    assert all(c["passed"] for c in result["validation_checks"])


def test_inspect_failed_execution_produces_critical_issues():
    state = _make_state(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=False, errors=[{"x": 1}]),
        ),
    )
    result = inspect_outputs(state)
    critical = [i for i in result["issues"] if i["severity"] == "critical"]
    assert len(critical) > 0


def test_inspect_missing_execution_result_produces_critical():
    state = _make_state(
        review_target={"execution_result": None, "artifacts": []},
    )
    result = inspect_outputs(state)
    assert any(i["severity"] == "critical" for i in result["issues"])


def test_inspect_empty_artifacts_when_expected_produces_major():
    state = _make_state(
        review_target=_make_review_target(
            exec_result=_make_exec_result(
                actions_taken=["[step_1] file_write: Written: out.txt"]
            ),
            artifacts=[],
        ),
    )
    result = inspect_outputs(state)
    assert any(
        i["severity"] == "major" and i["issue_type"] == "artifacts_present"
        for i in result["issues"]
    )


def test_inspect_validation_checks_recorded():
    state = _make_state()
    result = inspect_outputs(state)
    assert isinstance(result["validation_checks"], list)
    assert len(result["validation_checks"]) > 0
    for c in result["validation_checks"]:
        assert "rule" in c
        assert "passed" in c
        assert "message" in c


def test_inspect_no_llm_call_without_api_key():
    """Without API key, only structural checks run — no exception."""
    state = _make_state()
    # Ensure key is absent
    env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        result = inspect_outputs(state)
        assert any("skipping llm" in log.lower() for log in result["logs"])
    finally:
        if env_backup:
            os.environ["ANTHROPIC_API_KEY"] = env_backup


def test_inspect_skips_llm_on_critical_structural_failure():
    """LLM quality check is skipped when there is a critical structural failure."""
    state = _make_state(
        review_target={"execution_result": None, "artifacts": []},
    )
    result = inspect_outputs(state)
    assert any("skipping llm" in log.lower() for log in result["logs"])


# ---------------------------------------------------------------------------
# Inspect node — LLM mocked
# ---------------------------------------------------------------------------

@patch("app.graphs.review.nodes.inspect.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_inspect_llm_quality_check_adds_issues(mock_anthropic_cls):
    quality_issues = [
        {
            "rule": "quality_empty_content",
            "severity": "major",
            "description": "File content appears to be a placeholder",
            "location": "output.txt",
        }
    ]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(quality_issues)
    )
    mock_anthropic_cls.return_value = mock_client

    state = _make_state(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True),
            artifacts=[_make_artifact()],
        ),
    )
    result = inspect_outputs(state)

    assert any(i["issue_type"] == "quality_empty_content" for i in result["issues"])
    assert any("LLM quality check" in log for log in result["logs"])


@patch("app.graphs.review.nodes.inspect.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_inspect_llm_returns_empty_means_no_quality_issues(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response("[]")
    mock_anthropic_cls.return_value = mock_client

    state = _make_state(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True),
            artifacts=[_make_artifact()],
        ),
    )
    result = inspect_outputs(state)

    assert result["issues"] == []
    assert any("0 issue(s)" in log for log in result["logs"])


@patch("app.graphs.review.nodes.inspect.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_inspect_llm_failure_falls_back_gracefully(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API timeout")
    mock_anthropic_cls.return_value = mock_client

    state = _make_state(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True),
            artifacts=[_make_artifact()],
        ),
    )
    result = inspect_outputs(state)

    # Structural checks still ran and no unhandled exception
    assert len(result["validation_checks"]) > 0
    assert any("failed" in log.lower() for log in result["logs"])


# ---------------------------------------------------------------------------
# Verdict node — unit tests
# ---------------------------------------------------------------------------

def test_verdict_pass_on_no_issues():
    state = _make_state(issues=[], validation_checks=[
        {"rule": "r", "passed": True, "message": "ok", "severity": "minor", "location": None}
    ])
    result = generate_verdict(state)
    assert result["verdict"] == "pass"
    assert "passed" in result["review_notes"].lower()


def test_verdict_fail_on_critical():
    state = _make_state(issues=[_make_issue("critical", "execution failed")])
    result = generate_verdict(state)
    assert result["verdict"] == "fail"
    assert "execution failed" in result["review_notes"]


def test_verdict_revise_on_major():
    state = _make_state(issues=[_make_issue("major", "content is incomplete")])
    result = generate_verdict(state)
    assert result["verdict"] == "revise"
    assert "content is incomplete" in result["review_notes"]


def test_verdict_pass_on_minor_only():
    state = _make_state(issues=[_make_issue("minor", "small formatting gap")])
    result = generate_verdict(state)
    assert result["verdict"] == "pass"
    assert "small formatting gap" in result["review_notes"]


def test_verdict_notes_enumerate_issue_descriptions():
    issues = [
        _make_issue("critical", "execution returned None"),
        _make_issue("major", "artifact missing path"),
    ]
    state = _make_state(issues=issues)
    result = generate_verdict(state)
    assert "execution returned None" in result["review_notes"]


def test_verdict_fail_takes_precedence_over_major():
    issues = [
        _make_issue("critical", "execution failed"),
        _make_issue("major", "artifact missing"),
    ]
    state = _make_state(issues=issues)
    result = generate_verdict(state)
    assert result["verdict"] == "fail"


# ---------------------------------------------------------------------------
# Assemble node — unit tests
# ---------------------------------------------------------------------------

def test_assemble_pass_lists_artifacts():
    artifact = _make_artifact("report.txt", "outputs/report.txt")
    state = _make_state(
        verdict="pass",
        review_target=_make_review_target(artifacts=[artifact]),
        issues=[],
    )
    result = assemble_response(state)
    assert "report.txt" in result["final_response"]
    assert "outputs/report.txt" in result["final_response"]


def test_assemble_pass_approved_artifacts_is_all():
    artifacts = [_make_artifact("a.txt", "a.txt"), _make_artifact("b.txt", "b.txt")]
    state = _make_state(
        verdict="pass",
        review_target=_make_review_target(artifacts=artifacts),
        issues=[],
    )
    result = assemble_response(state)
    assert len(result["approved_artifacts"]) == 2


def test_assemble_revise_lists_issues():
    state = _make_state(
        verdict="revise",
        review_target=_make_review_target(artifacts=[_make_artifact()]),
        issues=[_make_issue("major", "content is a stub")],
    )
    result = assemble_response(state)
    assert "content is a stub" in result["final_response"]
    assert "issues requiring review" in result["final_response"].lower()


def test_assemble_revise_approved_artifacts_structurally_acceptable_only():
    """On revise: only artifacts with type+name+path are approved."""
    complete = _make_artifact("good.txt", "good.txt")
    incomplete = {"type": "file", "name": "bad.txt", "path": None, "value": None,
                  "source_subgraph": "execution", "created_at": datetime.utcnow(), "metadata": {}}
    state = _make_state(
        verdict="revise",
        review_target=_make_review_target(artifacts=[complete, incomplete]),
        issues=[_make_issue("major", "minor gap")],
    )
    result = assemble_response(state)
    assert len(result["approved_artifacts"]) == 1
    assert _attr(result["approved_artifacts"][0], "name") == "good.txt"


def test_assemble_fail_no_approved_artifacts():
    state = _make_state(
        verdict="fail",
        review_target=_make_review_target(artifacts=[_make_artifact()]),
        issues=[_make_issue("critical", "execution returned None")],
    )
    result = assemble_response(state)
    assert result["approved_artifacts"] == []


def test_assemble_fail_response_names_reason():
    state = _make_state(
        verdict="fail",
        review_target=_make_review_target(),
        issues=[_make_issue("critical", "execution returned None")],
    )
    result = assemble_response(state)
    assert "execution returned None" in result["final_response"]
    assert "not approved" in result["final_response"].lower()


def test_assemble_fail_lists_partial_artifacts_for_inspection():
    state = _make_state(
        verdict="fail",
        review_target=_make_review_target(artifacts=[_make_artifact("partial.txt", "partial.txt")]),
        issues=[_make_issue("critical", "execution failed")],
    )
    result = assemble_response(state)
    assert "partial.txt" in result["final_response"]
    assert "preserved" in result["final_response"].lower()


# ---------------------------------------------------------------------------
# Integration tests — full graph (LLM mocked)
# ---------------------------------------------------------------------------

def test_full_review_healthy_workflow():
    """Full graph: healthy execution produces pass + populated final_response."""
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True, errors=[]),
            artifacts=[_make_artifact("output.txt", "output.txt")],
        ),
        expected_outputs=["response"],
        task_id="test-integration-pass",
    )
    result = compiled.invoke(state)

    assert result["verdict"] == "pass"
    assert "output.txt" in result["final_response"]
    assert len(result["approved_artifacts"]) == 1
    assert len(result["validation_checks"]) > 0


def test_full_review_failed_execution():
    """Full graph: failed execution produces fail + empty approved_artifacts."""
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(
            exec_result=_make_exec_result(
                success=False,
                errors=[{"code": "E", "severity": "error", "message": "failed",
                          "source": "s", "timestamp": "t"}],
                actions_taken=[],
            ),
            artifacts=[],
        ),
        expected_outputs=["response"],
        task_id="test-integration-fail",
    )
    result = compiled.invoke(state)

    assert result["verdict"] == "fail"
    assert result["approved_artifacts"] == []
    assert "not approved" in result["final_response"].lower()


@patch("app.graphs.review.nodes.inspect.Anthropic")
@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
def test_full_review_llm_quality_issue_produces_revise(mock_anthropic_cls):
    """Full graph: LLM flags major quality issue → revise verdict."""
    quality_issues = [{
        "rule": "quality_stub_content",
        "severity": "major",
        "description": "File content appears to be a stub placeholder",
        "location": "output.txt",
    }]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(quality_issues)
    )
    mock_anthropic_cls.return_value = mock_client

    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(
            exec_result=_make_exec_result(success=True, errors=[]),
            artifacts=[_make_artifact()],
        ),
        expected_outputs=["response"],
        task_id="test-integration-revise",
    )
    result = compiled.invoke(state)

    assert result["verdict"] == "revise"
    assert "stub placeholder" in result["final_response"]


def test_full_review_logging():
    """All three nodes contribute to logs."""
    graph = create_review_graph()
    compiled = graph.compile()

    state = create_review_input(
        review_target=_make_review_target(),
        expected_outputs=["response"],
        task_id="test-logs",
    )
    result = compiled.invoke(state)

    logs_text = " ".join(result["logs"])
    # inspect, verdict, and assemble each add at least one log entry
    assert "structural" in logs_text.lower() or "check" in logs_text.lower()
    assert "verdict" in logs_text.lower()
    assert "assembled" in logs_text.lower()


# ---------------------------------------------------------------------------
# Helper used in assemble tests
# ---------------------------------------------------------------------------

def _attr(artifact, field: str):
    if isinstance(artifact, dict):
        return artifact.get(field)
    return getattr(artifact, field, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
