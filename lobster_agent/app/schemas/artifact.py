"""Artifact schema for structured artifact records."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Artifact(BaseModel):
    """Structured artifact record.

    Artifacts represent outputs produced by subgraphs, such as files,
    code, data, images, or documents.
    """

    type: Literal["file", "code", "data", "image", "document"] = Field(
        description="Type of artifact"
    )
    name: str = Field(
        description="Artifact name or title"
    )
    path: Optional[str] = Field(
        default=None,
        description="File path if applicable, otherwise None"
    )
    value: Optional[str] = Field(
        default=None,
        description="Artifact content if inline, otherwise None"
    )
    source_subgraph: Optional[str] = Field(
        default=None,
        description="Which subgraph produced this artifact"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when artifact was created"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context or metadata"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "file",
                "name": "analysis.txt",
                "path": "/project/outputs/analysis.txt",
                "value": None,
                "source_subgraph": "execution",
                "created_at": "2024-01-15T10:30:00Z",
                "metadata": {"size_bytes": 1024}
            }
        }
    )
