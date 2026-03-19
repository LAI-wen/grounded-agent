"""Retrieve node for Research Subgraph.

Retrieves relevant information from available sources using external retrieval service.
Phase 2: Real retrieval service integration.
"""

from ..state import ResearchState, Source
from ..services.retrieval import search_sources


def retrieve_sources(state: ResearchState) -> ResearchState:
    """Retrieve sources based on normalized query.

    Phase 2: Actually retrieves from search service or falls back to mock.

    Uses the normalized_query (if available) or falls back to original research_query.
    Calls external retrieval service API and maps results to Source schema.

    Args:
        state: Current ResearchState

    Returns:
        Updated ResearchState with sources populated
    """
    # Use normalized query if available, otherwise fall back to original
    query = state.get("normalized_query") or state["research_query"]
    strategy = state.get("retrieval_strategy", "hybrid")

    try:
        # Call retrieval service (will use API or fallback to mock)
        raw_sources = search_sources(query=query, strategy=strategy, max_results=20)

        # Map to Source TypedDict
        sources: list[Source] = []
        for item in raw_sources:
            sources.append(Source(
                url=item.get("url"),
                title=item["title"],
                content=item["content"],
                reliability_score=item.get("reliability_score"),
                metadata=item.get("metadata", {})
            ))

        return {
            **state,
            "sources": sources,
            "logs": [
                *state.get("logs", []),
                f"Retrieved {len(sources)} sources for query: '{query[:50]}...'"
            ],
        }

    except Exception as e:
        # If retrieval completely fails, return empty sources
        return {
            **state,
            "sources": [],
            "logs": [
                *state.get("logs", []),
                f"Retrieval failed: {str(e)}"
            ],
        }
