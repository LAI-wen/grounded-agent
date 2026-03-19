"""Retrieval adapters for Research Subgraph.

Phase 2A: Pluggable retrieval backend interfaces.
"""

from .base import RetrievalAdapter
from .http_adapter import HTTPRetrievalAdapter
from .local_file_adapter import LocalFileRetrievalAdapter
from .mock_adapter import MockRetrievalAdapter

__all__ = [
    "RetrievalAdapter",
    "HTTPRetrievalAdapter",
    "LocalFileRetrievalAdapter",
    "MockRetrievalAdapter",
]
