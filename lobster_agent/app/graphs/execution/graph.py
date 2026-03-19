"""Execution Subgraph definition using LangGraph.

The Execution Subgraph handles task execution with safety checks.

Phase 2B change: conditional edge after safety_check routes to
execute only when safety passes; otherwise terminates immediately.
"""

from typing import Any

from langgraph.graph import StateGraph, END

from .nodes import (
    create_execution_plan,
    generate_actions,
    simulate_execution,
    perform_safety_check,
    execute_actions,
)
from .state import ExecutionState


def _route_after_safety_check(state: ExecutionState) -> str:
    """Route to 'execute' if safety passed, otherwise END."""
    safety_check = state.get("safety_check")
    if safety_check and safety_check.get("passed"):
        return "execute"
    return END


def create_execution_graph() -> StateGraph:
    """Create the Execution Subgraph.

    Graph structure:
    - START → plan → generate → simulate → safety_check
    - safety_check → [passed] → execute → END
    - safety_check → [failed] → END

    Returns:
        Compiled StateGraph
    """
    graph = StateGraph(ExecutionState)

    # Add nodes
    graph.add_node("plan", create_execution_plan)
    graph.add_node("generate", generate_actions)
    graph.add_node("simulate", simulate_execution)
    graph.add_node("safety_check", perform_safety_check)
    graph.add_node("execute", execute_actions)

    # Set entry point
    graph.set_entry_point("plan")

    # Linear flow up to safety_check
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "simulate")
    graph.add_edge("simulate", "safety_check")

    # Conditional routing: execute only when safety passes
    graph.add_conditional_edges(
        "safety_check",
        _route_after_safety_check,
        {"execute": "execute", END: END},
    )

    graph.add_edge("execute", END)

    return graph


def create_execution_input(
    task_description: str,
    context: dict[str, Any],
    task_id: str
) -> ExecutionState:
    """Create initial ExecutionState.

    Args:
        task_description: Description of task to execute
        context: Additional context (may include project_root)
        task_id: Task identifier

    Returns:
        Initial ExecutionState
    """
    return ExecutionState(
        task_description=task_description,
        context=context,
        task_id=task_id,
        execution_plan=[],
        generated_actions=[],
        simulation_result=None,
        safety_check=None,
        execution_result=None,
        artifacts=[],
        logs=[],
        success=False,
        errors=[],
        abort_reason=None,
    )
