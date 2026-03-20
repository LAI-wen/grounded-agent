"""Tests for WebSearchAdapter and V4 combined retrieval.

No TAVILY_API_KEY required — all HTTP calls are mocked.
"""

import pytest
from unittest.mock import patch, Mock

from app.graphs.research.services.adapters.web_search_adapter import WebSearchAdapter
from app.graphs.research.services.retrieval import search_sources


# ---------------------------------------------------------------------------
# WebSearchAdapter.is_available
# ---------------------------------------------------------------------------

def test_web_adapter_available_when_key_set():
    adapter = WebSearchAdapter(api_key="tvly-test-key")
    assert adapter.is_available() is True


def test_web_adapter_not_available_when_key_absent(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    adapter = WebSearchAdapter(api_key="")
    assert adapter.is_available() is False


# ---------------------------------------------------------------------------
# WebSearchAdapter.search — happy path
# ---------------------------------------------------------------------------

def test_web_adapter_maps_response_to_source_schema():
    """Tavily response fields map correctly to the Source schema."""
    mock_resp = Mock()
    mock_resp.json.return_value = {
        "results": [
            {
                "url":     "https://example.com/page",
                "title":   "Example Result",
                "content": "Some relevant text from the page.",
                "score":   0.92,
            }
        ]
    }
    mock_resp.raise_for_status = Mock()

    with patch("requests.post", return_value=mock_resp):
        adapter = WebSearchAdapter(api_key="tvly-test")
        sources = adapter.search("test query")

    assert len(sources) == 1
    s = sources[0]
    assert s["url"] == "https://example.com/page"
    assert s["title"] == "Example Result"
    assert s["content"] == "Some relevant text from the page."
    assert abs(s["reliability_score"] - 0.92) < 0.01
    assert s["metadata"]["adapter"] == "web_search"
    assert s["metadata"]["provider"] == "tavily"
    assert s["metadata"]["rank"] == 1


def test_web_adapter_uses_default_reliability_when_score_absent():
    """When Tavily omits the score field, reliability defaults to 0.7."""
    mock_resp = Mock()
    mock_resp.json.return_value = {
        "results": [{"url": "https://x.com", "title": "T", "content": "C"}]
    }
    mock_resp.raise_for_status = Mock()

    with patch("requests.post", return_value=mock_resp):
        sources = WebSearchAdapter(api_key="tvly-test").search("q")

    assert abs(sources[0]["reliability_score"] - 0.7) < 0.01


def test_web_adapter_caps_content_at_1000_chars():
    long_content = "x" * 2000
    mock_resp = Mock()
    mock_resp.json.return_value = {
        "results": [{"url": "u", "title": "t", "content": long_content}]
    }
    mock_resp.raise_for_status = Mock()

    with patch("requests.post", return_value=mock_resp):
        sources = WebSearchAdapter(api_key="tvly-test").search("q")

    assert len(sources[0]["content"]) == 1000


# ---------------------------------------------------------------------------
# WebSearchAdapter.search — failure containment
# ---------------------------------------------------------------------------

def test_web_adapter_returns_empty_on_http_error():
    """HTTP errors (4xx/5xx) are caught; search() returns []."""
    mock_resp = Mock()
    mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")

    with patch("requests.post", return_value=mock_resp):
        adapter = WebSearchAdapter(api_key="tvly-test")
        sources = adapter.search("query")

    assert sources == []
    assert adapter.last_error is not None


def test_web_adapter_returns_empty_on_timeout():
    """Network timeout is caught; search() returns []."""
    import requests as req_lib
    with patch("requests.post", side_effect=req_lib.exceptions.Timeout):
        adapter = WebSearchAdapter(api_key="tvly-test")
        sources = adapter.search("query")

    assert sources == []
    assert adapter.last_error is not None


def test_web_adapter_returns_empty_when_key_absent(monkeypatch):
    """No API key → is_available() False → search() returns [] without calling network."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    adapter = WebSearchAdapter(api_key="")

    with patch("requests.post") as mock_post:
        sources = adapter.search("query")
        mock_post.assert_not_called()

    assert sources == []


# ---------------------------------------------------------------------------
# search_sources — V4 combined path
# ---------------------------------------------------------------------------

def test_search_sources_combines_local_and_web(tmp_path, monkeypatch):
    """With TAVILY_API_KEY set, results from both local and web are returned."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("lobster agent notes")

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    mock_resp = Mock()
    mock_resp.json.return_value = {
        "results": [{"url": "https://web.com", "title": "Web Doc",
                     "content": "web content about lobster", "score": 0.8}]
    }
    mock_resp.raise_for_status = Mock()

    with patch("requests.post", return_value=mock_resp):
        sources = search_sources("lobster agent")

    adapters = [s["metadata"].get("adapter") for s in sources]
    assert "local_file" in adapters, "local file results missing"
    assert "web_search" in adapters, "web search results missing"


def test_search_sources_local_only_when_web_unavailable(tmp_path, monkeypatch):
    """Without TAVILY_API_KEY, only local file sources are returned."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("lobster agent project")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with patch("requests.post") as mock_post:
        sources = search_sources("lobster agent")
        mock_post.assert_not_called()

    adapters = [s["metadata"].get("adapter") for s in sources]
    assert all(a == "local_file" for a in adapters)
    assert len(sources) > 0


def test_search_sources_returns_local_when_web_fails(tmp_path, monkeypatch):
    """Web search failure is silent — local results still returned."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("lobster agent project notes")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    with patch("requests.post", side_effect=Exception("network error")):
        sources = search_sources("lobster agent")

    # Local sources present; no exception raised
    assert any(s["metadata"].get("adapter") == "local_file" for s in sources)
    assert not any(s["metadata"].get("adapter") == "web_search" for s in sources)


def test_search_sources_adapter_override_uses_single_adapter():
    """Supplying an adapter override uses that adapter only (backward compat)."""
    from app.graphs.research.services.adapters.mock_adapter import MockRetrievalAdapter
    mock_adapter = MockRetrievalAdapter(num_sources=2)

    with patch("requests.post") as mock_post:
        sources = search_sources("test", adapter=mock_adapter)
        mock_post.assert_not_called()

    assert len(sources) == 2
    assert all(s["metadata"]["adapter"] == "mock" for s in sources)
