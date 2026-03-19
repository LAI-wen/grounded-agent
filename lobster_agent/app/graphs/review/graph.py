"""Review Subgraph definition using LangGraph.

The Review Subgraph validates outputs and assembles final responses.
"""

from typing import Any

from langgraph.graph import StateGraph, END

from .nodes import (
    inspect_outputs,
    generate_verdict,
    assemble_response,
)
from .state import ReviewState


def create_review_graph() -> StateGraph:
    """Create the Review Subgraph.

    Graph structure:
    - START -> inspect -> verdict -> assemble -> END

    Returns:
        Compiled StateGraph
    """
    # Create graph
    graph = StateGraph(ReviewState)

    # Add nodes
    graph.add_node("inspect", inspect_outputs)
    graph.add_node("verdict", generate_verdict)
    graph.add_node("assemble", assemble_response)

    # Set entry point
    graph.set_entry_point("inspect")

    # Add edges (linear flow for Phase 1)
    graph.add_edge("inspect", "verdict")
    graph.add_edge("verdict", "assemble")
    graph.add_edge("assemble", END)

    return graph


def create_review_input(
    review_target: dict[str, Any],
    expected_outputs: list[str],
    task_id: str
) -> ReviewState:
    """Create initial ReviewState.

    Args:
        review_target: What to review (outputs from previous subgraphs)
        expected_outputs: What outputs are expected
        task_id: Task identifier

    Returns:
        Initial ReviewState
    """
    return ReviewState(
        review_target=review_target,
        expected_outputs=expected_outputs,
        task_id=task_id,
        issues=[],
        improvements=[],
        validation_checks=[],
        verdict="pass",
        review_notes="",
        approved_artifacts=[],
        final_response="",
        logs=[],
    )
