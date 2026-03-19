"""Mock retrieval adapter for testing and fallback.

Phase 2A: Mock adapter that returns synthetic test data.
"""

from typing import Any, Literal

from .base import RetrievalAdapter


class MockRetrievalAdapter(RetrievalAdapter):
    """Mock retrieval adapter for testing and fallback.

    Returns synthetic source data for development and testing.
    Always available, never fails.
    """

    def __init__(self, num_sources: int = 2):
        """Initialize mock adapter.

        Args:
            num_sources: Number of mock sources to return (default: 2)
        """
        self.num_sources = num_sources

    def is_available(self) -> bool:
        """Mock adapter is always available.

        Returns:
            Always True
        """
        return True

    def search(
        self,
        query: str,
        strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
        max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Return mock sources for testing/fallback.

        Args:
            query: Search query (used in mock content)
            strategy: Retrieval strategy (stored in metadata)
            max_results: Maximum number of results (limits mock sources)

        Returns:
            List of mock source records
        """
        # Generate mock sources
        mock_sources = []

        for i in range(min(self.num_sources, max_results)):
            mock_sources.append({
                "url": f"https://example.com/mock-source-{i+1}",
                "title": f"Mock Source {i+1}: {query[:50]}",
                "content": (
                    f"This is mock content related to '{query}'. "
                    f"It contains relevant information for testing purposes. "
                    f"Phase 2A mock adapter provides synthetic data when real retrieval is unavailable."
                ),
                "reliability_score": 0.75 - (i * 0.1),  # Decreasing reliability
                "metadata": {
                    "strategy": strategy,
                    "is_mock": True,
                    "adapter": "mock",
                    "mock_index": i
                },
            })

        return mock_sources
