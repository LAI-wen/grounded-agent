"""Parent Graph definition using LangGraph.

The Parent Graph orchestrates the workflow by routing to appropriate subgraphs.
"""

import uuid
from typing import Any

from langgraph.graph import StateGraph, END

from .nodes import normalize_task, finalize_response, handle_error
from .router import route_next_step
from .state import MainState
from .wrappers import (
    invoke_research_subgraph,
    invoke_execution_subgraph,
    invoke_review_subgraph,
)


def create_main_graph() -> StateGraph:
    """Create the Parent Graph.

    Graph structure:
    - START -> normalize_task -> router
    - router -> research | execution | review | finalize | error
    - research -> router
    - execution -> router
    - review -> router
    - finalize -> END
    - error -> END

    Returns:
        Compiled StateGraph
    """
    # Create graph
    graph = StateGraph(MainState)

    # Add nodes
    graph.add_node("normalize_task", normalize_task)
    graph.add_node("research", invoke_research_subgraph)
    graph.add_node("execution", invoke_execution_subgraph)
    graph.add_node("review", invoke_review_subgraph)
    graph.add_node("finalize", finalize_response)
    graph.add_node("error", handle_error)

    # Set entry point
    graph.set_entry_point("normalize_task")

    # Add conditional routing from normalize_task
    graph.add_conditional_edges(
        "normalize_task",
        route_next_step,
        {
            "research": "research",
            "execution": "execution",
            "review": "review",
            "finalize": "finalize",
            "error": "error",
        }
    )

    # Add conditional routing from each subgraph back to router
    for subgraph in ["research", "execution", "review"]:
        graph.add_conditional_edges(
            subgraph,
            route_next_step,
            {
                "research": "research",
                "execution": "execution",
                "review": "review",
                "finalize": "finalize",
                "error": "error",
            }
        )

    # Terminal nodes
    graph.add_edge("finalize", END)
    graph.add_edge("error", END)

    return graph


def create_initial_state(user_request: str) -> MainState:
    """Create initial MainState from user request.

    Args:
        user_request: User's input request

    Returns:
        Initial MainState with defaults
    """
    return MainState(
        user_request=user_request,
        normalized_task=None,
        task_id=str(uuid.uuid4()),
        task_type=None,
        required_subgraphs=[],
        current_subgraph=None,
        next_step=None,
        plan=None,
        research_result=None,
        execution_result=None,
        review_result=None,
        artifacts=[],
        messages=[],
        errors=[],
        trace=[],
        safety_flags=[],
        status="pending",
    )
