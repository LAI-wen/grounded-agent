"""Interpret node for Research Subgraph.

Converts user request into research queries.

LEGACY / DEPRECATED
-------------------
This file is a Phase 1 placeholder. It is superseded by normalize_query.py,
which implements the full LLM-backed query normalization introduced in Phase 2A.

Do not add features here. Do not remove this file yet — it is kept for
reference during Phase 2 development and will be deleted in a later cleanup
pass once the replacement is confirmed stable.
"""

from ..state import ResearchState


def interpret_research_task(state: ResearchState) -> ResearchState:
    """Interpret research task and create structured query.

    Phase 1: Placeholder implementation.
    Phase 2+: Use LLM to properly interpret and structure the research query.
    """
    research_query = state["research_query"]

    # Simple placeholder - just use the query as-is
    return {
        **state,
        "retrieval_strategy": "hybrid",
        "logs": [*state.get("logs", []), "Interpreted research query"],
    }
