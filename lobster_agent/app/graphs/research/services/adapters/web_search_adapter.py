"""Web search retrieval adapter — Tavily API.

V4: External retrieval via public web search. Slots into the existing
RetrievalAdapter interface with no changes to retrieve.py, filter.py,
or synthesize_evidence.py.

Configuration:
    TAVILY_API_KEY — required; adapter returns [] silently when absent.

Failure contract:
    search() never raises. Any network error, timeout, or bad response
    returns [] and the failure is recorded in the adapter's last_error.

Design constraints (from V4 spec):
    - Hard timeout: 8 s
    - Max results: 5 (web snippets are higher signal, lower volume)
    - reliability_score: 0.7 default (below local file 0.85 — provenance uncertain)
    - url field populated (distinguishes web sources from local, url=None)
"""

import os
from typing import Any, Literal, Optional

from .base import RetrievalAdapter

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_TIMEOUT_SECONDS = 8
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_RELIABILITY = 0.7


class WebSearchAdapter(RetrievalAdapter):
    """Retrieval adapter that queries the Tavily web-search API.

    Returns clean text snippets as Source records. No HTML parsing
    required — Tavily extracts content server-side.

    Always available when TAVILY_API_KEY is set; silently no-ops otherwise.
    """

    def __init__(self, api_key: Optional[str] = None,
                 timeout: int = _TIMEOUT_SECONDS):
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self._timeout = timeout
        self.last_error: Optional[str] = None   # inspectable in tests / logs

    def is_available(self) -> bool:
        return bool(self._api_key)

    def search(
        self,
        query: str,
        strategy: Literal["vector", "keyword", "hybrid"] = "hybrid",
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> list[dict[str, Any]]:
        """Query Tavily and return up to max_results Source records.

        Returns [] on any failure — never raises.
        """
        self.last_error = None

        if not self.is_available():
            return []

        try:
            import requests

            payload = {
                "api_key":      self._api_key,
                "query":        query,
                "search_depth": "basic",
                "max_results":  min(max_results, _DEFAULT_MAX_RESULTS),
            }

            response = requests.post(
                _TAVILY_ENDPOINT,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()

            results = response.json().get("results", [])
            return [self._map(item, idx, strategy)
                    for idx, item in enumerate(results)]

        except Exception as exc:
            self.last_error = str(exc)
            return []

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _map(self, item: dict, rank: int,
             strategy: str) -> dict[str, Any]:
        """Map a Tavily result dict to the Source record schema."""
        score = item.get("score")
        reliability = float(score) if score is not None else _DEFAULT_RELIABILITY
        # Clamp to [0, 1] — Tavily scores are already in this range
        reliability = max(0.0, min(1.0, reliability))

        return {
            "url":               item.get("url"),
            "title":             item.get("title") or f"Web result {rank + 1}",
            "content":           (item.get("content") or "")[:1000],
            "reliability_score": reliability,
            "metadata": {
                "adapter":  "web_search",
                "provider": "tavily",
                "rank":     rank + 1,
                "strategy": strategy,
            },
        }
