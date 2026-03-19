"""Inspect node for Review Subgraph.

Phase 2C: Two-phase inspection.

Phase A (always runs, no LLM):
  Deterministic structural checks via validator.run_structural_checks().
  Failures become ReviewIssue records with matching severity.

Phase B (only when structural checks pass, requires ANTHROPIC_API_KEY):
  LLM quality assessment — Claude identifies content quality issues but
  does NOT rewrite or regenerate any content.
  Falls back cleanly when API key is absent or LLM call fails.
"""

import json
import os

from anthropic import Anthropic

from ..prompts.templates import (
    INSPECT_QUALITY_SYSTEM_PROMPT,
    INSPECT_QUALITY_USER_PROMPT,
)
from ..services.validator import run_structural_checks
from ..state import ReviewIssue, ReviewState, ValidationCheck


def inspect_outputs(state: ReviewState) -> ReviewState:
    """Inspect execution results and artifacts for structural and quality issues.

    Populates:
    - validation_checks: full audit of all structural rules run
    - issues: all failing checks + any LLM quality issues
    - improvements: reserved (empty in Phase 2C)
    """
    review_target = state["review_target"]
    expected_outputs = state.get("expected_outputs", [])

    # ------------------------------------------------------------------
    # Phase A: Deterministic structural checks (always runs)
    # ------------------------------------------------------------------
    validation_checks: list[ValidationCheck] = run_structural_checks(
        review_target, expected_outputs
    )

    issues: list[ReviewIssue] = [
        ReviewIssue(
            issue_type=c["rule"],
            severity=c["severity"],
            description=c["message"],
            location=c["location"],
        )
        for c in validation_checks
        if not c["passed"]
    ]

    n_failed = len(issues)
    logs = [
        *state.get("logs", []),
        f"Structural checks: {len(validation_checks)} run, {n_failed} failed",
    ]

    # ------------------------------------------------------------------
    # Phase B: LLM quality check
    # Only runs when no critical structural failures exist
    # ------------------------------------------------------------------
    has_critical = any(
        not c["passed"] and c["severity"] == "critical"
        for c in validation_checks
    )

    if has_critical:
        logs.append("Skipping LLM quality check: critical structural failure(s) found")
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logs.append("No ANTHROPIC_API_KEY — skipping LLM quality check")
        else:
            try:
                llm_issues, llm_log = _run_llm_quality_check(
                    review_target=review_target,
                    api_key=api_key,
                )
                issues.extend(llm_issues)
                logs.append(llm_log)
            except Exception as e:
                logs.append(
                    f"LLM quality check failed ({e}) — using structural checks only"
                )

    return {
        **state,
        "issues": issues,
        "validation_checks": validation_checks,
        "improvements": [],
        "logs": logs,
    }


# ---------------------------------------------------------------------------
# LLM quality check helpers
# ---------------------------------------------------------------------------

def _run_llm_quality_check(
    review_target: dict,
    api_key: str,
) -> tuple[list[ReviewIssue], str]:
    """Call Claude to identify quality issues in artifacts.

    Returns (issues, log_message).
    Claude returns a structured JSON list — never free-form text.
    """
    client = Anthropic(api_key=api_key)

    task_description = _extract_task_description(review_target)
    artifacts_summary = _build_artifacts_summary(review_target)

    user_prompt = INSPECT_QUALITY_USER_PROMPT.format(
        task_description=task_description,
        artifacts_summary=artifacts_summary,
    )

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        system=INSPECT_QUALITY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    issues_data = _parse_json(response.content[0].text.strip())

    issues: list[ReviewIssue] = [
        ReviewIssue(
            issue_type=item.get("rule", "quality_issue"),
            severity=item.get("severity", "minor"),
            description=item.get("description", ""),
            location=item.get("location"),
        )
        for item in issues_data
    ]

    return issues, f"LLM quality check: {len(issues)} issue(s) found"


def _extract_task_description(review_target: dict) -> str:
    """Build a brief task description from execution actions for the LLM prompt."""
    exec_result = review_target.get("execution_result")
    if exec_result:
        actions = exec_result.get("actions_taken", [])
        if actions:
            return "; ".join(str(a)[:100] for a in actions[:5])
    return "Unknown task (no actions_taken in execution_result)"


def _build_artifacts_summary(review_target: dict) -> str:
    """Build a compact, token-efficient artifact summary for the LLM prompt."""
    artifacts = review_target.get("artifacts", [])
    if not artifacts:
        return "(no artifacts)"

    lines = []
    for a in artifacts:
        name = _attr(a, "name") or "unknown"
        path = _attr(a, "path")
        value = _attr(a, "value")

        header = f"File: {name} (path: {path})"
        if value:
            preview = value[:800] + "..." if len(value) > 800 else value
            lines.append(f"{header}\nContent:\n{preview}")
        else:
            lines.append(f"{header}\nContent: (large file — not inlined)")

    return "\n\n".join(lines)


def _attr(artifact, field: str):
    """Get a field from either a Pydantic model or a plain dict."""
    if isinstance(artifact, dict):
        return artifact.get(field)
    return getattr(artifact, field, None)


def _parse_json(text: str) -> list:
    """Extract and parse a JSON array from LLM response text."""
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end].strip()
    return json.loads(text)
