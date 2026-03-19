"""Error schema for structured error records."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Error(BaseModel):
    """Structured error record.

    Captures errors and exceptions that occur during workflow execution.
    """

    code: str = Field(
        description="Error code or type identifier"
    )
    severity: Literal["critical", "error", "warning"] = Field(
        description="Error severity level"
    )
    message: str = Field(
        description="Human-readable error message"
    )
    source: str = Field(
        description="Which component raised the error (node, subgraph, tool)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the error occurred"
    )
    stack_trace: Optional[str] = Field(
        default=None,
        description="Full stack trace if available"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "TOOL_EXECUTION_ERROR",
                "severity": "error",
                "message": "Failed to execute search tool: timeout exceeded",
                "source": "execution.nodes.execute",
                "timestamp": "2024-01-15T10:30:00Z",
                "stack_trace": "Traceback (most recent call last):\n  ..."
            }
        }
    )
