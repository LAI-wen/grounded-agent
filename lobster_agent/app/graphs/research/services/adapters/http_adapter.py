"""HTTP-based retrieval adapter.

Phase 2A: Adapter for external retrieval service via HTTP API.
"""

import os
from typing import Any, Literal, Optional
import requests

from .base import RetrievalAdapter


class HTTPRetrievalAdapter(RetrievalAdapter):
    """HTTP API-based retrieval adapter.

    Connects to an external retrieval service via HTTP POST.
    Requires RETRIEVAL_SERVICE_URL environment variable.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: int = 30):
        """Initialize HTTP adapter.

        Args:
            base_url: Base URL for retrieval service (defaults to RETRIEVAL_SERVICE_URL env var)
            timeout: Request timeout in seconds (default: 30)
        """
        self.base_url = base_url or os.environ.get("RETRIEVAL_SERVICE_URL")
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if HTTP retrieval service is configured.

        Returns:
            True if RETRIEVAL_SERVICE_URL is set, False otherwise
        """
        return self.base_url is not None and self.base_url != ""

    def search(
        self,
        query: str,
        strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
        max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Search using external HTTP retrieval service.

        Args:
            query: Search query
            strategy: Retrieval strategy (vector/keyword/hybrid)
            max_results: Maximum number of results

        Returns:
            List of source records

        Raises:
            RuntimeError: If adapter is not available
            requests.RequestException: If HTTP request fails
        """
        if not self.is_available():
            raise RuntimeError("HTTPRetrievalAdapter not configured (no RETRIEVAL_SERVICE_URL)")

        # Call external retrieval service
        response = requests.post(
            f"{self.base_url}/search",
            json={
                "query": query,
                "strategy": strategy,
                "max_results": max_results
            },
            timeout=self.timeout
        )

        response.raise_for_status()

        data = response.json()
        sources = data.get("results", [])

        # Map API response to Source schema
        mapped_sources = []
        for idx, item in enumerate(sources[:max_results]):
            mapped_sources.append({
                "url": item.get("url"),
                "title": item.get("title", f"Source {idx + 1}"),
                "content": item.get("content", ""),
                "reliability_score": item.get("score", item.get("relevance", 0.5)),
                "metadata": {
                    "strategy": strategy,
                    "rank": idx + 1,
                    "adapter": "http",
                    **item.get("metadata", {})
                }
            })

        # Filter out very low-quality sources (relevance < 0.3)
        filtered = [s for s in mapped_sources if s.get("reliability_score", 0) >= 0.3]

        return filtered
