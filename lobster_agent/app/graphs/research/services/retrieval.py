"""Retrieval service for Research Subgraph.

Phase 2:  Real search/retrieval implementation using external API.
Phase 2A: Refactored to use pluggable adapter interface.
V4:       search_sources() now runs local file search and web search in
          parallel (sequentially but both always attempted) when a
          TAVILY_API_KEY is present, merging the results into one list.
          filter_sources handles quality ranking — no changes needed there.
"""

from typing import Any, Literal, Optional

from .adapters import (
    RetrievalAdapter,
    HTTPRetrievalAdapter,
    LocalFileRetrievalAdapter,
    MockRetrievalAdapter,
    WebSearchAdapter,
)

_LOCAL_MAX  = 10   # local file results cap when combined with web
_WEB_MAX    =  5   # web results cap (higher signal, lower volume)
_LOCAL_ONLY = 20   # local-only cap when web is unavailable


def get_retrieval_adapter(adapter_type: Optional[str] = None) -> RetrievalAdapter:
    """Factory function for a single retrieval adapter (legacy / test use).

    Auto-detect priority: HTTP (if RETRIEVAL_SERVICE_URL set) → local file → mock.
    For combined local+web retrieval, use search_sources() directly.

    Args:
        adapter_type: Optional override: "http", "local", "web", or "mock".
    """
    if adapter_type == "mock":
        return MockRetrievalAdapter()
    if adapter_type == "local":
        return LocalFileRetrievalAdapter()
    if adapter_type == "web":
        return WebSearchAdapter()
    if adapter_type == "http":
        adapter = HTTPRetrievalAdapter()
        return adapter if adapter.is_available() else LocalFileRetrievalAdapter()

    # Auto-detect: HTTP → local file → mock
    http_adapter = HTTPRetrievalAdapter()
    if http_adapter.is_available():
        return http_adapter
    return LocalFileRetrievalAdapter()


def search_sources(
    query: str,
    strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
    max_results: int = 20,
    adapter: Optional[RetrievalAdapter] = None,
) -> list[dict[str, Any]]:
    """Search for sources, combining local files and web results when available.

    V4 behaviour (no adapter override):
      1. Always run LocalFileRetrievalAdapter (up to _LOCAL_MAX results).
      2. If TAVILY_API_KEY is set, also run WebSearchAdapter (up to _WEB_MAX).
      3. Return local + web combined — filter_sources handles ranking.
      Web failures are silent: local-only results returned if web call fails.

    Legacy behaviour (adapter override provided):
      Use the supplied adapter only. Preserves backward compatibility for tests
      that inject a mock or specific adapter.

    Args:
        query:      Search query (typically the normalized query text).
        strategy:   Retrieval strategy — passed through to adapters.
        max_results: Ignored when adapter=None (per-source caps apply);
                     respected when a single adapter override is supplied.
        adapter:    Optional single-adapter override (test / legacy use).
    """
    if adapter is not None:
        try:
            return adapter.search(query, strategy, max_results)
        except Exception:
            return MockRetrievalAdapter().search(query, strategy, max_results)

    # V4 combined path
    local_sources = _safe_search(
        LocalFileRetrievalAdapter(), query, strategy, _LOCAL_MAX
    )

    web_adapter = WebSearchAdapter()
    web_sources: list[dict[str, Any]] = []
    if web_adapter.is_available():
        web_sources = _safe_search(web_adapter, query, strategy, _WEB_MAX)

    return local_sources + web_sources


def _safe_search(
    adapter: RetrievalAdapter,
    query: str,
    strategy: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """Run adapter.search(), returning [] on any exception."""
    try:
        return adapter.search(query, strategy, max_results)
    except Exception:
        return []
