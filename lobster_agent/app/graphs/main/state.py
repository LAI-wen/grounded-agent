"""MainState schema for Parent Graph.

MainState is owned by the Parent Graph and contains workflow-level state.
Subgraphs produce outputs that are mapped back into MainState fields.
"""

from datetime import datetime
from typing import Any, Literal, Optional, TypedDict

from app.schemas import Artifact, Error, SafetyFlag, TraceEntry


# Type aliases for clarity
TaskType = Literal["research", "execution", "hybrid"]
SubgraphIdentifier = Literal["research", "execution", "review"]
WorkflowStatus = Literal["pending", "running", "awaiting_review", "success", "no_op", "partial", "blocked", "failed"]


class NormalizedTask(TypedDict):
    """Structured task definition.

    Minimum schema as defined in spec v1.3.
    """
    objective: str
    constraints: list[str]
    requested_outputs: list[str]
    assumptions: list[str]
    priority: Optional[Literal["low", "medium", "high"]]


class Message(TypedDict):
    """Structured message format.

    Minimal message schema for V1 thread continuity.

    IMPORTANT: Keep metadata minimal - avoid dumping full state snapshots or large payloads.
    """
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime
    metadata: dict[str, Any]  # Keep minimal - only essential context, not full payloads


class ResearchResult(TypedDict):
    """Structured output from Research Subgraph."""
    summary: str
    evidence: list[str]  # TODO Phase 2: Upgrade to structured Evidence schema with claim/source/confidence
    citations: list[str]
    confidence: float  # 0.0-1.0
    open_questions: list[str]


class ExecutionResult(TypedDict):
    """Structured output from Execution Subgraph."""
    actions_taken: list[str]
    artifacts: list[Artifact]
    logs: list[str]
    success: bool
    status: Literal["success", "no_op", "partial", "failed"]
    errors: list[Error]  # Use structured Error schema for consistency


class ReviewResult(TypedDict):
    """Structured output from Review Subgraph."""
    verdict: Literal["pass", "blocked", "partial", "revise", "fail"]
    issues: list[str]  # TODO Phase 2: Consider structured ReviewIssue schema with type/severity/location
    approved_artifacts: list[Artifact]
    final_response: str
    review_notes: str


class MainState(TypedDict):
    """Parent Graph state schema.

    This state is owned by the Parent Graph orchestration layer.
    Subgraphs should not directly mutate unrelated MainState fields.
    """

    # User input
    user_request: str

    # Task normalization
    normalized_task: Optional[NormalizedTask]
    task_id: str
    task_type: Optional[TaskType]

    # Workflow routing
    required_subgraphs: list[SubgraphIdentifier]
    current_subgraph: Optional[SubgraphIdentifier]
    next_step: Optional[str]  # TODO Phase 2: Currently unused, router returns next node directly via conditional_edges.
                               # Consider either: (1) actively maintain this field for observability, or
                               # (2) remove it. If maintaining, use Literal union: SubgraphIdentifier | "finalize" | "error"

    # Planning
    plan: Optional[str]  # TODO Phase 2: Upgrade to structured Plan schema with steps/dependencies workflow-level high-level plan

    # Subgraph results
    research_result: Optional[ResearchResult]
    execution_result: Optional[ExecutionResult]
    review_result: Optional[ReviewResult]

    # Artifacts and outputs
    artifacts: list[Artifact]

    # Conversation history
    messages: list[Message]

    # Error tracking
    errors: list[Error]

    # Observability
    trace: list[TraceEntry]
    safety_flags: list[SafetyFlag]

    # Status
    status: WorkflowStatus
