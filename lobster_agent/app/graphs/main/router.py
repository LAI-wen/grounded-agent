"""Router logic for Parent Graph.

Handles routing decisions based on task_type and required_subgraphs.
"""

from typing import Literal

from .state import MainState, SubgraphIdentifier

# Maximum number of revise-loop retries (initial attempt + up to 2 retries = 3 total).
_MAX_RETRIES = 2


def route_next_step(state: MainState) -> Literal["research", "execution", "review", "finalize", "error"]:
    """Determine the next node to execute based on workflow state.

    Routing logic:
    1. If there are errors and status is failed, route to error handling
    2. If required_subgraphs is not empty, route to the next subgraph
    3. If all subgraphs are complete, route to finalize
    4. Otherwise, route to error (invalid state)

    Args:
        state: Current MainState

    Returns:
        Next node name to execute
    """
    # Check for failure state
    if state["status"] == "failed":
        return "error"

    # Get remaining subgraphs to execute
    required = state.get("required_subgraphs", [])
    current = state.get("current_subgraph")

    # If no subgraphs required, finalize
    if not required:
        return "finalize"

    # If we have a current subgraph
    if current:
        # Check if current is in required list (state validation)
        if current not in required:
            # Current subgraph not in required list - corrupted state
            return "error"

        # Find the index of current subgraph
        try:
            current_idx = required.index(current)
            # Check if there are more subgraphs after this one
            if current_idx + 1 < len(required):
                next_subgraph = required[current_idx + 1]
                return next_subgraph  # type: ignore
            else:
                # All subgraphs complete — check for revise loop before finalizing.
                # If review produced a 'revise' verdict and retries remain, route
                # back to execution so the planner can act on the review notes.
                review_result = state.get("review_result") or {}
                if (
                    current == "review"
                    and review_result.get("verdict") == "revise"
                    and state.get("retry_count", 0) < _MAX_RETRIES
                ):
                    return "execution"
                return "finalize"
        except ValueError:
            # Should not happen since we checked `in` above, but be safe
            return "error"

    # If no current subgraph set yet, start with first one
    if not current and required:
        return required[0]  # type: ignore

    # Default to finalize if we can't determine next step
    return "finalize"


def determine_required_subgraphs(task_type: str) -> list[SubgraphIdentifier]:
    """Map task_type to required_subgraphs list.

    Args:
        task_type: High-level task classification

    Returns:
        Ordered list of subgraphs to execute
    """
    mapping: dict[str, list[SubgraphIdentifier]] = {
        "research": ["research"],
        "execution": ["execution", "review"],
        "hybrid": ["research", "execution", "review"],
    }
    return mapping.get(task_type, [])
