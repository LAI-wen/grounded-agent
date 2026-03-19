"""Assemble node for Review Subgraph.

Phase 2C: Fully deterministic assembly from review_target content.
No LLM is used here — the final_response is built by reading what
was actually produced, not by generating new content.

approved_artifacts semantics:
  pass   → all artifacts from review_target (fully approved)
  revise → structurally acceptable artifacts only (type + name + path set)
           not fully endorsed, but structurally usable
  fail   → empty (no artifacts approved; preserved in state for inspection)
"""

from typing import Optional

from ..state import ReviewIssue, ReviewState


def assemble_response(state: ReviewState) -> ReviewState:
    """Assemble final_response and approved_artifacts from validated content."""
    verdict = state.get("verdict", "pass")
    review_target = state["review_target"]
    issues = state.get("issues", [])

    all_artifacts = review_target.get("artifacts", [])
    exec_result = review_target.get("execution_result") or {}
    research_result = review_target.get("research_result")

    approved_artifacts = _select_approved_artifacts(verdict, all_artifacts)
    final_response = _build_final_response(
        verdict=verdict,
        all_artifacts=all_artifacts,
        issues=issues,
        exec_result=exec_result,
        research_result=research_result,
    )

    return {
        **state,
        "final_response": final_response,
        "approved_artifacts": approved_artifacts,
        "logs": [*state.get("logs", []), "Response assembled"],
    }


# ---------------------------------------------------------------------------
# Approved artifacts selection
# ---------------------------------------------------------------------------

def _select_approved_artifacts(verdict: str, all_artifacts: list) -> list:
    if verdict == "pass":
        return list(all_artifacts)
    if verdict in ("revise", "partial"):
        return [a for a in all_artifacts if _is_structurally_acceptable(a)]
    # fail or blocked
    return []


def _is_structurally_acceptable(artifact) -> bool:
    """Artifact has type, name, and path all set (not None/empty)."""
    return bool(
        _attr(artifact, "type")
        and _attr(artifact, "name")
        and _attr(artifact, "path")
    )


# ---------------------------------------------------------------------------
# Final response construction
# ---------------------------------------------------------------------------

def _build_final_response(
    verdict: str,
    all_artifacts: list,
    issues: list[ReviewIssue],
    exec_result: dict,
    research_result: Optional[dict] = None,
) -> str:
    lines: list[str] = []

    # If research results exist, include them first
    if research_result:
        summary = research_result.get("summary", "")
        if summary:
            lines.append("Research findings:")
            lines.append(summary)
            lines.append("")  # blank line separator

    # Then handle execution verdict
    if verdict == "pass":
        lines.append("Task completed successfully.")

        if all_artifacts:
            lines.append("\nOutputs produced:")
            for a in all_artifacts:
                name = _attr(a, "name") or "unknown"
                path = _attr(a, "path")
                lines.append(f"  - {name} → {path or '(no path)'}")

        exec_logs = exec_result.get("logs", [])
        if exec_logs:
            lines.append("\nExecution summary:")
            for entry in exec_logs[-3:]:
                lines.append(f"  {entry}")

        lines.append("\nAll validation checks passed.")

    elif verdict == "blocked":
        lines.append("Execution was blocked — no actions were performed.")

        critical = [i for i in issues if i["severity"] == "critical"]
        if critical:
            lines.append(
                "\nReason: " + "; ".join(i["description"] for i in critical)
            )

        lines.append("\nNo outputs were produced.")

    elif verdict in ("revise", "partial"):
        lines.append("Task completed with issues requiring review.")

        if all_artifacts:
            lines.append("\nOutputs produced:")
            for a in all_artifacts:
                name = _attr(a, "name") or "unknown"
                path = _attr(a, "path")
                lines.append(f"  - {name} → {path or '(no path)'}")

        if issues:
            lines.append("\nIssues to address:")
            for i in issues:
                lines.append(f"  [{i['severity']}] {i['description']}")

        lines.append("\nReview these issues before relying on the outputs.")

    else:  # fail
        lines.append("Task failed validation — outputs are not approved.")

        critical = [i for i in issues if i["severity"] == "critical"]
        if critical:
            lines.append(
                "\nReason: " + "; ".join(i["description"] for i in critical)
            )

        if all_artifacts:
            lines.append("\nPartial outputs (preserved for inspection):")
            for a in all_artifacts:
                name = _attr(a, "name") or "unknown"
                path = _attr(a, "path")
                lines.append(f"  - {name} → {path or '(no path)'}")

        lines.append("\nCheck the execution error log for details.")

    return "\n".join(lines)


def _attr(artifact, field: str):
    """Get a field from either a Pydantic model or a plain dict."""
    if isinstance(artifact, dict):
        return artifact.get(field)
    return getattr(artifact, field, None)
