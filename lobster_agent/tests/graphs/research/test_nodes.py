"""Unit tests for Research Subgraph nodes.

Tests verify Phase 2 real implementations:
- normalize_query: LLM integration and fallback
- retrieve_sources: API calls and fallback
- filter_sources: LLM filtering and fallback
- synthesize_evidence: LLM synthesis and fallback
- finalize_research: Aggregation logic
"""

import pytest
from unittest.mock import patch, Mock
import sys
from app.graphs.research.state import ResearchState
from app.graphs.research.nodes import (
    normalize_query,
    retrieve_sources,
    filter_sources,
    synthesize_evidence,
    finalize_research,
)

# Get actual module objects for patching (not the re-exported functions)
normalize_query_mod = sys.modules['app.graphs.research.nodes.normalize_query']
filter_mod = sys.modules['app.graphs.research.nodes.filter']
synthesize_evidence_mod = sys.modules['app.graphs.research.nodes.synthesize_evidence']


def create_test_state(**overrides) -> ResearchState:
    """Create a test ResearchState with defaults."""
    defaults = {
        "research_query": "Test query",
        "context": {},
        "task_id": "test-123",
        "normalized_query": None,
        "structured_query": None,
        "retrieval_strategy": "hybrid",
        "sources": [],
        "filtered_sources": [],
        "filter_decisions": [],
        "evidence": [],
        "citations": [],
        "summary": "",
        "confidence": 0.0,
        "open_questions": [],
        "logs": [],
    }
    defaults.update(overrides)
    return ResearchState(**defaults)


# ===== normalize_query tests =====

def test_normalize_query_with_llm():
    """Test normalize_query with successful LLM call (Phase 2A: structured extraction)."""
    # Mock Anthropic client with structured response
    import json as json_module

    structured_response = {
        "normalized_text": "normalized test query",
        "key_concepts": ["test", "query"],
        "query_intent": "general",
        "scope": "moderate"
    }

    mock_response = Mock()
    mock_response.content = [Mock(text=f'```json\n{json_module.dumps(structured_response)}\n```')]

    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    # Patch the normalize_query module's imports
    with patch.object(normalize_query_mod.os.environ, 'get', return_value="test-key"):
        with patch.object(normalize_query_mod, 'Anthropic', return_value=mock_client):
            state = create_test_state(research_query="Original query")
            result = normalize_query(state)

            assert result["normalized_query"] == "normalized test query"
            assert result["structured_query"] is not None
            assert result["structured_query"]["normalized_text"] == "normalized test query"
            assert result["structured_query"]["query_intent"] == "general"
            assert "Structured query extracted" in result["logs"][0]


def test_normalize_query_without_api_key(monkeypatch):
    """Test normalize_query falls back to original query without API key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    state = create_test_state(research_query="Test query")
    result = normalize_query(state)

    assert result["normalized_query"] == "Test query"
    assert "No ANTHROPIC_API_KEY found" in result["logs"][0]


def test_normalize_query_llm_failure():
    """Test normalize_query handles LLM failure gracefully."""
    mock_client = Mock()
    mock_client.messages.create.side_effect = Exception("LLM error")

    with patch.object(normalize_query_mod.os.environ, 'get', return_value="test-key"):
        with patch.object(normalize_query_mod, 'Anthropic', return_value=mock_client):
            state = create_test_state(research_query="Test query")
            result = normalize_query(state)

            assert result["normalized_query"] == "Test query"
            assert "Query normalization failed" in result["logs"][0]


# ===== retrieve_sources tests =====

def test_retrieve_sources_with_api(monkeypatch):
    """Test retrieve_sources with successful API call (Phase 2A: adapter-based)."""
    monkeypatch.setenv("RETRIEVAL_SERVICE_URL", "http://test-api.com")

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {
                "url": "http://example.com/1",
                "title": "Test Source 1",
                "content": "Test content 1",
                "score": 0.9
            }
        ]
    }

    # Patch requests.post in the http_adapter module (not retrieval.py)
    with patch('app.graphs.research.services.adapters.http_adapter.requests.post', return_value=mock_response):
        state = create_test_state(normalized_query="test query")
        result = retrieve_sources(state)

        assert len(result["sources"]) == 1
        assert result["sources"][0]["title"] == "Test Source 1"
        assert "Retrieved 1 sources" in result["logs"][0]


def test_retrieve_sources_without_api(monkeypatch):
    """Test retrieve_sources uses local file adapter when no HTTP API configured."""
    monkeypatch.delenv("RETRIEVAL_SERVICE_URL", raising=False)

    state = create_test_state(normalized_query="test query")
    result = retrieve_sources(state)

    # Local file adapter is now the default when HTTP is not configured.
    # Sources may be empty (no matches in project for "test query") but no error.
    assert result["sources"] is not None
    assert "logs" in result


def test_retrieve_sources_api_failure(monkeypatch):
    """Test retrieve_sources handles API failure gracefully (Phase 2A: adapter fallback)."""
    monkeypatch.setenv("RETRIEVAL_SERVICE_URL", "http://test-api.com")

    # Patch requests.post in the http_adapter module (not retrieval.py)
    with patch('app.graphs.research.services.adapters.http_adapter.requests.post', side_effect=Exception("API error")):
        state = create_test_state(normalized_query="test query")
        result = retrieve_sources(state)

        # Should fall back to mock
        assert len(result["sources"]) > 0


# ===== filter_sources tests =====

def test_filter_sources_with_llm():
    """Test filter_sources with successful LLM assessment."""
    # Mock sources
    sources = [
        {
            "url": "http://example.com/1",
            "title": "Relevant Source",
            "content": "Very relevant content",
            "reliability_score": 0.8,
            "metadata": {}
        },
        {
            "url": "http://example.com/2",
            "title": "Irrelevant Source",
            "content": "Not relevant",
            "reliability_score": 0.7,
            "metadata": {}
        }
    ]

    # Mock LLM response
    mock_response = Mock()
    mock_response.content = [Mock(text='[{"source_index": 0, "relevance": 0.9, "reason": "Highly relevant"}]')]

    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    with patch.object(filter_mod.os.environ, 'get', return_value="test-key"):
        with patch.object(filter_mod, 'Anthropic', return_value=mock_client):
            state = create_test_state(sources=sources, normalized_query="test")
            result = filter_sources(state)

            assert len(result["filtered_sources"]) == 1
            assert result["filtered_sources"][0]["title"] == "Relevant Source"
            assert "LLM filtering" in result["logs"][0]


def test_filter_sources_without_api_key(monkeypatch):
    """Test filter_sources falls back to reliability filtering without API key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    sources = [
        {"title": "Good", "content": "test", "reliability_score": 0.8, "metadata": {}},
        {"title": "Bad", "content": "test", "reliability_score": 0.4, "metadata": {}}
    ]

    state = create_test_state(sources=sources)
    result = filter_sources(state)

    assert len(result["filtered_sources"]) == 1
    assert result["filtered_sources"][0]["title"] == "Good"
    assert "No API key" in result["logs"][0]


def test_filter_sources_empty_sources():
    """Test filter_sources handles empty source list."""
    state = create_test_state(sources=[])
    result = filter_sources(state)

    assert result["filtered_sources"] == []
    assert "No sources to filter" in result["logs"][0]


# ===== synthesize_evidence tests =====

def test_synthesize_evidence_with_llm():
    """Test synthesize_evidence with successful LLM synthesis."""
    filtered_sources = [
        {"title": "Source 1", "content": "Evidence content 1", "reliability_score": 0.8, "metadata": {}},
        {"title": "Source 2", "content": "Evidence content 2", "reliability_score": 0.7, "metadata": {}}
    ]

    # Mock LLM response
    llm_response = {
        "evidence": ["Claim 1", "Claim 2"],
        "citations": ["Source 1", "Source 2"],
        "confidence": 0.85,
        "open_questions": ["Question 1"]
    }

    import json as json_module
    mock_response = Mock()
    mock_response.content = [Mock(text=f'```json\n{json_module.dumps(llm_response)}\n```')]

    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    # Patch the synthesize_evidence module's imports
    with patch.object(synthesize_evidence_mod.os.environ, 'get', return_value="test-key"):
        with patch.object(synthesize_evidence_mod, 'Anthropic', return_value=mock_client):
            state = create_test_state(filtered_sources=filtered_sources, normalized_query="test")
            result = synthesize_evidence(state)

            # Verify mock was called
            mock_client.messages.create.assert_called_once()

            assert len(result["evidence"]) == 2
            assert result["confidence"] == 0.85
            assert len(result["open_questions"]) == 1
            assert "Synthesized" in result["logs"][-1]  # Verify LLM path was taken, not fallback


def test_synthesize_evidence_without_api_key(monkeypatch):
    """Test synthesize_evidence falls back to simple extraction without API key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    filtered_sources = [
        {"title": "Source 1", "content": "Content 1" * 50, "reliability_score": 0.8, "metadata": {}},
    ]

    state = create_test_state(filtered_sources=filtered_sources, normalized_query="test")
    result = synthesize_evidence(state)

    assert len(result["evidence"]) > 0
    assert len(result["citations"]) > 0
    assert result["confidence"] > 0


def test_synthesize_evidence_empty_sources():
    """Test synthesize_evidence handles empty filtered sources."""
    state = create_test_state(filtered_sources=[])
    result = synthesize_evidence(state)

    assert result["evidence"] == []
    assert result["confidence"] == 0.0
    assert "No sources available" in result["summary"]


# ===== finalize_research tests =====

def test_finalize_research_with_evidence():
    """Test finalize_research generates summary with evidence."""
    state = create_test_state(
        evidence=["Claim 1", "Claim 2"],
        citations=["Source 1"],
        confidence=0.8,
        open_questions=["Q1"],
        filtered_sources=[{"title": "Source 1"}]
    )

    result = finalize_research(state)

    assert "Research completed" in result["summary"]
    assert "2 evidence claims" in result["summary"]
    assert "confidence: 0.80" in result["summary"]
    assert "Research finalized" in result["logs"][0]


def test_finalize_research_without_evidence():
    """Test finalize_research handles case with no evidence."""
    state = create_test_state(evidence=[])

    result = finalize_research(state)

    assert "no evidence was extracted" in result["summary"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
