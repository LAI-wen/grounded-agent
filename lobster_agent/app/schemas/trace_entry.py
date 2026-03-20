"""TraceEntry schema for workflow execution tracing."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TraceEntry(BaseModel):
    """Structured trace entry for workflow execution.

    Captures workflow-level progression and routing decisions.
    For detailed execution logs, use node-level logs instead.
    """

    step_id: str = Field(
        description="Unique identifier for this step"
    )
    node_name: str = Field(
        description="Which node executed"
    )
    subgraph: Optional[str] = Field(
        default=None,
        description="Which subgraph this belongs to (None for parent graph nodes)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this step occurred"
    )
    input_summary: str = Field(
        default="",
        description="Brief summary of inputs (not full payload)"
    )
    output_summary: str = Field(
        default="",
        description="Brief summary of outputs (not full payload)"
    )
    status: Literal["success", "failure", "partial", "revise_loop"] = Field(
        description="Step execution status"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "step_id": "trace_001",
                "node_name": "normalize_task",
                "subgraph": None,
                "timestamp": "2024-01-15T10:30:00Z",
                "input_summary": "user_request: 'Research climate change'",
                "output_summary": "task_type: 'research', required_subgraphs: ['research']",
                "status": "success"
            }
        }
    )
