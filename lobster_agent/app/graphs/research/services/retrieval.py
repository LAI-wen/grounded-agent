"""Retrieval service for Research Subgraph.

Phase 2: Real search/retrieval implementation using external API.
Phase 2A: Refactored to use pluggable adapter interface.
"""

from typing import Any, Literal, Optional

from .adapters import RetrievalAdapter, HTTPRetrievalAdapter, LocalFileRetrievalAdapter, MockRetrievalAdapter


def get_retrieval_adapter(adapter_type: Optional[str] = None) -> RetrievalAdapter:
    """Factory function to get the appropriate retrieval adapter.

    Auto-detect priority: HTTP (if RETRIEVAL_SERVICE_URL set) → local file → mock.

    Args:
        adapter_type: Optional override: "http", "local", or "mock".
                     If None, auto-detects based on environment.

    Returns:
        RetrievalAdapter instance
    """
    if adapter_type == "mock":
        return MockRetrievalAdapter()

    if adapter_type == "local":
        return LocalFileRetrievalAdapter()

    if adapter_type == "http":
        adapter = HTTPRetrievalAdapter()
        if not adapter.is_available():
            return LocalFileRetrievalAdapter()
        return adapter

    # Auto-detect: HTTP → local file → mock
    http_adapter = HTTPRetrievalAdapter()
    if http_adapter.is_available():
        return http_adapter

    return LocalFileRetrievalAdapter()


def search_sources(
    query: str,
    strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
    max_results: int = 20,
    adapter: Optional[RetrievalAdapter] = None
) -> list[dict[str, Any]]:
    """Search for sources based on query.

    Phase 2A: Uses pluggable adapter interface with automatic fallback.

    Args:
        query: Search query (typically the normalized query)
        strategy: Retrieval strategy (vector/keyword/hybrid)
        max_results: Maximum number of results to retrieve (default: 20)
        adapter: Optional adapter instance (auto-detects if None)

    Returns:
        List of source records
    """
    # Get adapter if not provided
    if adapter is None:
        adapter = get_retrieval_adapter()

    # Use adapter to search
    try:
        return adapter.search(query, strategy, max_results)
    except Exception:
        # If the adapter fails, fall back to mock
        mock_adapter = MockRetrievalAdapter()
        return mock_adapter.search(query, strategy, max_results)
