"""ResearchState schema for Research Subgraph.

This state is internal to the Research Subgraph and not directly
exposed to the Parent Graph.
"""

from typing import Any, Literal, Optional, TypedDict


class Source(TypedDict):
    """Retrieved source record."""
    url: Optional[str]
    title: str
    content: str
    reliability_score: Optional[float]
    metadata: dict[str, Any]


class StructuredQuery(TypedDict):
    """Structured research query with extracted components.

    Phase 2A: Enhanced query representation for better retrieval.
    """
    normalized_text: str  # The rewritten query string
    key_concepts: list[str]  # Important terms/entities extracted
    query_intent: Literal["factual", "analytical", "exploratory", "general"]  # Type of research
    scope: Literal["narrow", "moderate", "broad"]  # Breadth indicator


class FilterDecision(TypedDict):
    """Explainable filtering decision for a source.

    Phase 2A: Transparent record of why sources were kept or dropped.
    """
    source_index: int  # Index in sources list
    keep: bool  # Final decision: True = kept, False = dropped
    relevance_score: float  # Supporting evidence (0.0-1.0)
    reasoning: str  # Explanation of the decision


class ResearchState(TypedDict):
    """Research Subgraph internal state.

    The Research Subgraph owns this state.
    Outputs are returned as ResearchResult to the Parent Graph.
    """

    # Input from parent
    research_query: str
    context: dict[str, Any]
    task_id: str

    # Query normalization
    normalized_query: Optional[str]  # Phase 2: Simple normalized string (backward compatible)
    structured_query: Optional[StructuredQuery]  # Phase 2A: Enhanced structured representation

    # Retrieval strategy
    retrieval_strategy: Literal["vector", "keyword", "hybrid"]

    # Retrieved data
    sources: list[Source]
    filtered_sources: list[Source]
    filter_decisions: list[FilterDecision]  # Phase 2A: Explainable filtering decisions

    # Synthesis
    evidence: list[str]
    citations: list[str]  # Source citations supporting evidence
    summary: str

    # Quality metrics
    confidence: float  # 0.0-1.0
    open_questions: list[str]

    # Forward-looking reasoning (V3 M1)
    suggested_next_step: Optional[str]  # Concrete artifact-targeted suggestion; None when not applicable

    # Internal logs
    logs: list[str]
