"""ReviewState schema for Review Subgraph.

This state is internal to the Review Subgraph and not directly
exposed to the Parent Graph.
"""

from typing import Any, Literal, Optional, TypedDict

from app.schemas import Artifact


class ReviewIssue(TypedDict):
    """Individual review issue found during inspection."""
    issue_type: str
    severity: Literal["critical", "major", "minor"]
    description: str
    location: Optional[str]


class ValidationCheck(TypedDict):
    """Result of a single deterministic validation rule.

    Records both passing and failing checks so the full audit trail
    is available in state for debugging. Only failing checks are
    converted to ReviewIssue records that influence the verdict.
    """
    rule: str                                    # rule identifier
    passed: bool
    message: str                                 # human-readable result
    severity: Literal["critical", "major", "minor"]  # only meaningful when passed=False
    location: Optional[str]                      # which field or artifact failed


class ReviewState(TypedDict):
    """Review Subgraph internal state.

    The Review Subgraph owns this state.
    Outputs are returned as ReviewResult to the Parent Graph.

    Scope (Phase 2C): execution-focused review only.
    review_target must contain execution_result and artifacts.
    Research result review is deferred to a later phase.
    """

    # Input from parent
    review_target: dict[str, Any]
    expected_outputs: list[str]
    task_id: str

    # Inspection results
    issues: list[ReviewIssue]
    improvements: list[str]
    validation_checks: list[ValidationCheck]     # full audit of all structural rules run

    # Verdict
    verdict: Literal["pass", "blocked", "partial", "revise", "fail"]
    review_notes: str

    # Final assembly
    approved_artifacts: list[Artifact]
    final_response: str

    # Internal logs
    logs: list[str]
