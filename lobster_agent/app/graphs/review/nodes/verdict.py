"""Verdict node for Review Subgraph.

Phase 2C: Fully deterministic verdict derived from issues list.
The verdict logic is unchanged from Phase 1 — it was already correct.
What changed: inspect now produces real issues, so verdict is meaningful.

review_notes enumerates actual issue descriptions rather than a generic count.
"""

from ..state import ReviewState


def generate_verdict(state: ReviewState) -> ReviewState:
    """Derive verdict from issues and produce informative review_notes.

    Mapping:
    - Any critical issue related to no_op/no actions AND no errors → blocked
    - Other critical issues (including errors) → fail
    - Major issues only → revise
    - Minor issues only → pass (with notes)
    - No issues → pass
    """
    issues = state.get("issues", [])
    validation_checks = state.get("validation_checks", [])

    critical = [i for i in issues if i["severity"] == "critical"]
    major = [i for i in issues if i["severity"] == "major"]
    minor = [i for i in issues if i["severity"] == "minor"]

    n_checks = len(validation_checks)

    # Check if this is a blocked state (no_op or no actions) vs a real failure
    # Blocked: no real work attempted (no_op status or missing actions with no errors)
    # Fail: execution was attempted but failed (errors present)
    review_target = state.get("review_target", {})
    exec_result = review_target.get("execution_result") or {}
    exec_status = exec_result.get("status", "")
    has_errors = len(exec_result.get("errors", [])) > 0

    # If there are errors, it's a failure not a block
    if critical:
        # Blocked: no work was attempted — either no_op or safety-aborted
        # (safety abort leaves execution_result=None, which maps to an empty dict here)
        safety_aborted = not exec_result
        if (exec_status == "no_op" or safety_aborted) and not has_errors:
            verdict = "blocked"
            reason = "; ".join(i["description"] for i in critical)
            review_notes = f"Execution blocked: {reason}."
        else:
            verdict = "fail"
            reason = "; ".join(i["description"] for i in critical)
            review_notes = f"Critical validation failure: {reason}."
            if major:
                review_notes += f" Also {len(major)} major issue(s)."

    elif major:
        verdict = "revise"
        issue_list = "; ".join(
            f"[{i['severity']}] {i['description']}" for i in major + minor
        )
        review_notes = f"Issues requiring revision: {issue_list}."

    else:
        verdict = "pass"
        if minor:
            minor_notes = "; ".join(i["description"] for i in minor)
            review_notes = (
                f"All {n_checks} check(s) passed with {len(minor)} minor note(s): "
                f"{minor_notes}."
            )
        else:
            review_notes = f"All {n_checks} check(s) passed. No issues found."

    return {
        **state,
        "verdict": verdict,
        "review_notes": review_notes,
        "logs": [*state.get("logs", []), f"Verdict: {verdict}"],
    }
