"""Base retrieval adapter interface.

Phase 2A: Abstract base class for pluggable retrieval providers.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal


class RetrievalAdapter(ABC):
    """Abstract base class for retrieval adapters.

    Retrieval adapters provide a pluggable interface for different
    information retrieval backends (HTTP APIs, vector stores, search engines, etc.).
    """

    @abstractmethod
    def search(
        self,
        query: str,
        strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
        max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Search for sources based on query.

        Args:
            query: Search query (typically the normalized query text)
            strategy: Retrieval strategy (vector/keyword/hybrid)
            max_results: Maximum number of results to retrieve

        Returns:
            List of source records with schema:
            {
                "url": Optional[str],
                "title": str,
                "content": str,
                "reliability_score": Optional[float],
                "metadata": dict[str, Any]
            }
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the adapter is available and properly configured.

        Returns:
            True if adapter can be used, False otherwise
        """
        pass
