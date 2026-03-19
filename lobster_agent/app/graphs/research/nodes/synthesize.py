"""Synthesize node for Research Subgraph.

Synthesizes structured knowledge from evidence.

LEGACY / DEPRECATED
-------------------
This file is a Phase 1 placeholder. It is superseded by synthesize_evidence.py,
which implements the full LLM-backed evidence synthesis introduced in Phase 2A.

Do not add features here. Do not remove this file yet — it is kept for
reference during Phase 2 development and will be deleted in a later cleanup
pass once the replacement is confirmed stable.
"""

from ..state import ResearchState


def synthesize_research(state: ResearchState) -> ResearchState:
    """Synthesize evidence into structured knowledge.

    Phase 1: Simple placeholder synthesis.
    Phase 2+: Use LLM to properly synthesize findings.
    """
    filtered_sources = state.get("filtered_sources", [])

    # Extract evidence (placeholder)
    evidence = [f"Evidence from {s['title']}" for s in filtered_sources]

    # Create summary (placeholder)
    summary = f"Research summary based on {len(filtered_sources)} sources"

    # Calculate confidence (placeholder: average of source reliability)
    if filtered_sources:
        avg_reliability = sum(
            s.get("reliability_score", 0.0) for s in filtered_sources
        ) / len(filtered_sources)
        confidence = avg_reliability
    else:
        confidence = 0.0

    # Identify open questions (placeholder)
    open_questions = ["Further investigation needed in area X"]

    return {
        **state,
        "evidence": evidence,
        "summary": summary,
        "confidence": confidence,
        "open_questions": open_questions,
        "logs": [*state.get("logs", []), "Synthesized research results"],
    }
