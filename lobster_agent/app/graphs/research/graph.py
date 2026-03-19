"""Research Subgraph definition using LangGraph.

The Research Subgraph handles information gathering and synthesis.
Phase 2: 5-node structure with real LLM integration.
"""

from typing import Any

from langgraph.graph import StateGraph, END

from .nodes import (
    normalize_query,
    retrieve_sources,
    filter_sources,
    synthesize_evidence,
    finalize_research,
)
from .state import ResearchState


def create_research_graph() -> StateGraph:
    """Create the Research Subgraph.

    Phase 2 Graph structure:
    - START -> normalize -> retrieve -> filter -> synthesize -> finalize -> END

    5-node linear flow:
    1. normalize_query: LLM rewrites query into clear search query
    2. retrieve_sources: Calls retrieval service API
    3. filter_sources: LLM assesses source relevance
    4. synthesize_evidence: LLM extracts evidence and citations
    5. finalize_research: Aggregates final results

    Returns:
        Compiled StateGraph
    """
    # Create graph
    graph = StateGraph(ResearchState)

    # Add nodes (Phase 2: 5-node structure)
    graph.add_node("normalize", normalize_query)
    graph.add_node("retrieve", retrieve_sources)
    graph.add_node("filter", filter_sources)
    graph.add_node("synthesize", synthesize_evidence)
    graph.add_node("finalize", finalize_research)

    # Set entry point
    graph.set_entry_point("normalize")

    # Add edges (linear flow)
    graph.add_edge("normalize", "retrieve")
    graph.add_edge("retrieve", "filter")
    graph.add_edge("filter", "synthesize")
    graph.add_edge("synthesize", "finalize")
    graph.add_edge("finalize", END)

    return graph


def create_research_input(
    research_query: str,
    context: dict[str, Any],
    task_id: str
) -> ResearchState:
    """Create initial ResearchState.

    Args:
        research_query: The research query to process
        context: Additional context
        task_id: Task identifier

    Returns:
        Initial ResearchState
    """
    return ResearchState(
        research_query=research_query,
        context=context,
        task_id=task_id,
        normalized_query=None,
        structured_query=None,
        retrieval_strategy="hybrid",
        sources=[],
        filtered_sources=[],
        filter_decisions=[],
        evidence=[],
        citations=[],
        summary="",
        confidence=0.0,
        open_questions=[],
        logs=[],
    )
