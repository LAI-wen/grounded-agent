"""Finalize Research node for Research Subgraph.

Aggregates research results and generates final summary.
Phase 2: Final aggregation without LLM call.
"""

from ..state import ResearchState


def finalize_research(state: ResearchState) -> ResearchState:
    """Finalize research results with summary and final aggregation.

    Phase 2: Aggregates evidence, citations, confidence, and generates summary.

    This node does NOT make LLM calls - it simply aggregates the results
    from synthesize_evidence and creates a final summary.

    Args:
        state: Current ResearchState

    Returns:
        Updated ResearchState with summary and final logs
    """
    evidence = state.get("evidence", [])
    citations = state.get("citations", [])
    confidence = state.get("confidence", 0.0)
    open_questions = state.get("open_questions", [])
    filtered_sources = state.get("filtered_sources", [])

    # Generate summary based on evidence
    if evidence:
        header = (
            f"Research completed with {len(evidence)} evidence claims "
            f"from {len(filtered_sources)} sources. "
            f"Overall confidence: {confidence:.2f}."
        )
        evidence_lines = "\n".join(f"- {e}" for e in evidence[:5])
        summary = f"{header}\n\n{evidence_lines}"
        if open_questions:
            summary += f"\n\n{len(open_questions)} open question(s) identified."
    else:
        summary = "Research completed but no evidence was extracted from sources."

    return {
        **state,
        "summary": summary,
        "logs": [
            *state.get("logs", []),
            f"Research finalized: {len(evidence)} evidence, {len(citations)} citations, confidence: {confidence:.2f}"
        ],
    }
