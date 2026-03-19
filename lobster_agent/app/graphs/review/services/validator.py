"""Validator service for Review Subgraph.

Phase 2C: Real structural validation rules for execution-focused review.
Each rule returns a ValidationCheck record (pass or fail) for auditability.
"""

from typing import Any

from ..state import ValidationCheck


def _attr(artifact: Any, field: str) -> Any:
    """Get a field from either a Pydantic model or a plain dict."""
    if isinstance(artifact, dict):
        return artifact.get(field)
    return getattr(artifact, field, None)


# ---------------------------------------------------------------------------
# Individual rule functions
# ---------------------------------------------------------------------------

def check_execution_result_present(execution_result: Any) -> ValidationCheck:
    """execution_result must be present (not None)."""
    passed = execution_result is not None
    return ValidationCheck(
        rule="execution_result_present",
        passed=passed,
        message=(
            "execution_result is present"
            if passed
            else "execution_result is None — no execution output was produced"
        ),
        severity="critical",
        location="execution_result",
    )


def check_execution_success(execution_result: dict) -> ValidationCheck:
    """execution_result.success must be True, or status must be a valid non-success state."""
    success = bool(execution_result.get("success"))
    status = execution_result.get("status", "failed")

    # no_op is a blocked state, not a success
    if status == "no_op":
        return ValidationCheck(
            rule="execution_success",
            passed=False,
            message="execution status is 'no_op' — no actions were executed",
            severity="critical",
            location="execution_result.status",
        )

    passed = success
    return ValidationCheck(
        rule="execution_success",
        passed=passed,
        message=(
            "execution completed successfully"
            if passed
            else f"execution_result.success is False (status: {status})"
        ),
        severity="critical",
        location="execution_result.success",
    )


def check_no_execution_errors(execution_result: dict) -> ValidationCheck:
    """execution_result.errors must be empty."""
    errors = execution_result.get("errors", [])
    passed = len(errors) == 0
    return ValidationCheck(
        rule="no_execution_errors",
        passed=passed,
        message=(
            "no execution errors recorded"
            if passed
            else f"{len(errors)} execution error(s) recorded in execution_result.errors"
        ),
        severity="critical",
        location="execution_result.errors",
    )


def check_artifacts_present(artifacts: list) -> ValidationCheck:
    """At least one artifact must have been produced.

    Only called when the task is known to expect artifact output.
    """
    passed = len(artifacts) > 0
    return ValidationCheck(
        rule="artifacts_present",
        passed=passed,
        message=(
            f"{len(artifacts)} artifact(s) produced"
            if passed
            else "no artifacts were produced for a task that expected file output"
        ),
        severity="major",
        location="artifacts",
    )


def check_artifact_fields_complete(artifacts: list) -> ValidationCheck:
    """Every artifact must have type, name, and path set."""
    incomplete = []
    for a in artifacts:
        if not (_attr(a, "type") and _attr(a, "name") and _attr(a, "path")):
            incomplete.append(_attr(a, "name") or "unknown")

    passed = len(incomplete) == 0
    return ValidationCheck(
        rule="artifact_fields_complete",
        passed=passed,
        message=(
            "all artifacts have required fields (type, name, path)"
            if passed
            else f"artifact(s) missing required fields: {incomplete}"
        ),
        severity="major",
        location="artifacts[*].type/name/path",
    )


def check_logs_non_empty(execution_result: dict) -> ValidationCheck:
    """execution_result.logs must be non-empty."""
    logs = execution_result.get("logs", [])
    passed = len(logs) > 0
    return ValidationCheck(
        rule="logs_non_empty",
        passed=passed,
        message=(
            f"{len(logs)} log entries recorded"
            if passed
            else "execution logs are empty — no observability data available"
        ),
        severity="minor",
        location="execution_result.logs",
    )


def check_actions_executed(execution_result: dict) -> ValidationCheck:
    """At least one real action must have been executed (not just no-ops).

    If execution_result has errors, this check is skipped (the errors will
    be caught by check_no_execution_errors instead).
    """
    errors = execution_result.get("errors", [])
    if errors:
        # Don't flag missing actions if there were errors - those are handled separately
        return ValidationCheck(
            rule="actions_executed",
            passed=True,
            message="actions_executed check skipped due to execution errors",
            severity="critical",
            location="execution_result.actions_taken",
        )

    actions = execution_result.get("actions_taken", [])
    # Filter out no-op actions
    real_actions = [a for a in actions if "No-op:" not in str(a)]
    passed = len(real_actions) > 0
    return ValidationCheck(
        rule="actions_executed",
        passed=passed,
        message=(
            f"{len(real_actions)} real action(s) executed"
            if passed
            else "no real actions were executed — only no-op steps ran"
        ),
        severity="critical",
        location="execution_result.actions_taken",
    )


# ---------------------------------------------------------------------------
# Composite helper
# ---------------------------------------------------------------------------

def task_expects_artifacts(review_target: dict, expected_outputs: list) -> bool:
    """Determine whether this task was expected to produce file artifacts.

    Checks:
    1. expected_outputs contains artifact-implying terms
    2. any action in execution_result.actions_taken references file_write
    """
    artifact_terms = {"file", "artifact", "code", "output", "create", "write", "generate"}
    for output in expected_outputs:
        if any(term in output.lower() for term in artifact_terms):
            return True

    exec_result = review_target.get("execution_result")
    if exec_result:
        for action in exec_result.get("actions_taken", []):
            if "file_write" in str(action).lower():
                return True

    return False


def run_structural_checks(
    review_target: dict,
    expected_outputs: list,
) -> list[ValidationCheck]:
    """Run all applicable structural checks and return the full audit list.

    Stops after execution_result_present if execution_result is None —
    subsequent checks would be meaningless without it.
    """
    checks: list[ValidationCheck] = []
    execution_result = review_target.get("execution_result")
    artifacts = review_target.get("artifacts", [])

    # Rule 1: execution_result present
    check = check_execution_result_present(execution_result)
    checks.append(check)

    if execution_result is None:
        # Cannot run further checks without an execution_result
        return checks

    # Rule 2: execution succeeded
    checks.append(check_execution_success(execution_result))

    # Rule 3: actions actually executed (not just no-ops)
    checks.append(check_actions_executed(execution_result))

    # Rule 4: no errors
    checks.append(check_no_execution_errors(execution_result))

    # Rule 5: artifacts present — only when task implies artifact output
    if task_expects_artifacts(review_target, expected_outputs):
        checks.append(check_artifacts_present(artifacts))

    # Rule 6: artifact fields complete — only when artifacts exist
    if artifacts:
        checks.append(check_artifact_fields_complete(artifacts))

    # Rule 7: logs non-empty
    checks.append(check_logs_non_empty(execution_result))

    return checks
