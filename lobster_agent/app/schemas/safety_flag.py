"""SafetyFlag schema for security and safety warnings."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SafetyFlag(BaseModel):
    """Structured safety warning.

    Captures security and safety concerns during workflow execution.
    """

    code: str = Field(
        description="Flag identifier (e.g., 'path_violation', 'destructive_op')"
    )
    severity: Literal["high", "medium", "low"] = Field(
        description="Safety concern severity"
    )
    message: str = Field(
        description="Description of the safety concern"
    )
    source: str = Field(
        description="Which component raised the flag"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Relevant context (file path, command, etc.)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "path_violation",
                "severity": "high",
                "message": "Attempted to access file outside project directory",
                "source": "execution.nodes.safety_check",
                "context": {
                    "requested_path": "/etc/passwd",
                    "allowed_root": "/project"
                }
            }
        }
    )
