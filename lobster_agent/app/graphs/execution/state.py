"""ExecutionState schema for Execution Subgraph.

This state is internal to the Execution Subgraph and not directly
exposed to the Parent Graph.
"""

from typing import Any, Literal, Optional, TypedDict

from app.schemas import Artifact, Error, SafetyFlag


class ExecutionStep(TypedDict):
    """Individual execution step."""
    step_id: str
    action: str
    tool: Optional[str]                  # "file_read" | "file_write" | None
    params: dict[str, Any]
    tool_input: Optional[dict[str, Any]] # resolved, validated input for the tool
    status: Literal["pending", "running", "success", "failed"]


class SafetyCheckResult(TypedDict):
    """Result of safety verification."""
    passed: bool
    violations: list[str]
    warnings: list[str]
    flags: list[SafetyFlag]              # structured safety flag records


class ExecutionOutput(TypedDict):
    """Typed output produced by the Execution Subgraph.

    Matches spec §3.3 structured output requirement.
    The Parent Graph wrapper maps this into MainState.execution_result.
    """
    actions_taken: list[str]
    artifacts: list[Artifact]
    logs: list[str]
    success: bool
    status: Literal["success", "no_op", "partial", "failed"]
    errors: list[Error]


class ExecutionState(TypedDict):
    """Execution Subgraph internal state.

    The Execution Subgraph owns this state.
    Outputs are returned as ExecutionOutput to the Parent Graph.
    """

    # Input from parent
    task_description: str
    context: dict[str, Any]
    task_id: str

    # Planning (execution-level actionable steps, NOT workflow-level plan)
    execution_plan: list[ExecutionStep]

    # Generated action descriptions (human-readable summary per step)
    generated_actions: list[str]

    # Simulation
    simulation_result: Optional[dict[str, Any]]

    # Safety verification
    safety_check: Optional[SafetyCheckResult]

    # Execution results
    execution_result: Optional[ExecutionOutput]
    artifacts: list[Artifact]

    # Logs
    logs: list[str]

    # Status
    success: bool
    errors: list[Error]
    abort_reason: Optional[str]          # set when execution is aborted before execute node
