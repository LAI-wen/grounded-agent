"""Search tool - placeholder for Phase 1.

Phase 2+: Implement actual search with approved adapters/providers.
"""

from typing import Any


def search(query: str, strategy: str = "hybrid") -> list[dict[str, Any]]:
    """Search for information using approved sources.

    Phase 1: Placeholder that returns mock results.
    Phase 2+: Real search implementation with:
    - Approved adapters/providers
    - Normalized source records
    - No arbitrary network requests

    Args:
        query: Search query
        strategy: Search strategy (vector/keyword/hybrid)

    Returns:
        List of search results
    """
    # Placeholder implementation
    return [
        {
            "title": f"Result for: {query}",
            "url": "https://example.com/result",
            "content": "Mock search result content",
            "relevance_score": 0.85,
        }
    ]
