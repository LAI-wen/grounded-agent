"""Integration tests for Research Subgraph.

Tests verify the complete research flow from query normalization through finalization.
"""

import pytest
from unittest.mock import patch, Mock
import sys
from app.graphs.research import create_research_graph, create_research_input

# Get actual module objects for patching (not the re-exported functions)
normalize_query_mod = sys.modules['app.graphs.research.nodes.normalize_query']
filter_mod = sys.modules['app.graphs.research.nodes.filter']
synthesize_evidence_mod = sys.modules['app.graphs.research.nodes.synthesize_evidence']


def test_research_flow_without_api_keys(monkeypatch):
    """Test complete research flow without API keys (uses fallbacks)."""
    # Clear API keys to test fallback paths
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RETRIEVAL_SERVICE_URL", raising=False)

    # Create research graph
    graph = create_research_graph()
    compiled_graph = graph.compile()

    # Create input
    initial_state = create_research_input(
        research_query="What is the impact of AI on software development?",
        context={"user_request": "Research AI impact"},
        task_id="test-integration-1"
    )

    # Run graph
    result = compiled_graph.invoke(initial_state)

    # Verify final state
    assert result["normalized_query"] is not None  # Should fall back to original
    assert len(result["sources"]) > 0  # Should have mock sources
    assert len(result["filtered_sources"]) >= 0  # May filter some
    assert result["summary"] != ""  # Should have summary
    assert 0.0 <= result["confidence"] <= 1.0
    assert len(result["logs"]) > 0  # Should have logs from each node

    # Verify all nodes executed
    log_messages = " ".join(result["logs"])
    assert "query" in log_messages.lower() or "Query" in log_messages
    assert "Retrieved" in log_messages or "sources" in log_messages
    assert "filter" in log_messages.lower() or "Filter" in log_messages
    assert "finalized" in log_messages.lower() or "Finalized" in log_messages


def test_research_flow_with_llm_mocked(monkeypatch):
    """Test complete research flow with mocked LLM responses (Phase 2A: structured query)."""
    monkeypatch.delenv("RETRIEVAL_SERVICE_URL", raising=False)

    # Mock Anthropic client for all LLM calls
    def create_mock_response(text):
        mock_resp = Mock()
        mock_resp.content = [Mock(text=text)]
        return mock_resp

    mock_client = Mock()

    # Mock responses for different calls (in order: normalize, filter, synthesize)
    mock_client.messages.create.side_effect = [
        # normalize - structured query (Phase 2A)
        create_mock_response('''```json
{
  "normalized_text": "AI impact on software development",
  "key_concepts": ["AI", "software development", "impact"],
  "query_intent": "analytical",
  "scope": "moderate"
}
```'''),
        # filter
        create_mock_response('[{"source_index": 0, "relevance": 0.9, "reason": "Relevant"}]'),
        # synthesize
        create_mock_response('''```json
{
  "evidence": ["AI accelerates development", "AI improves code quality"],
  "citations": ["Mock Source 1"],
  "confidence": 0.8,
  "open_questions": ["Long-term effects unclear"]
}
```''')
    ]

    # Patch all three node modules consistently
    with patch.object(normalize_query_mod.os.environ, 'get', return_value="test-key"), \
         patch.object(filter_mod.os.environ, 'get', return_value="test-key"), \
         patch.object(synthesize_evidence_mod.os.environ, 'get', return_value="test-key"), \
         patch.object(normalize_query_mod, 'Anthropic', return_value=mock_client), \
         patch.object(filter_mod, 'Anthropic', return_value=mock_client), \
         patch.object(synthesize_evidence_mod, 'Anthropic', return_value=mock_client):

        # Create and run graph
        graph = create_research_graph()
        compiled_graph = graph.compile()

        initial_state = create_research_input(
            research_query="AI impact on dev",
            context={},
            task_id="test-integration-2"
        )

        result = compiled_graph.invoke(initial_state)

        # Verify LLM-enhanced results (Phase 2A: structured query)
        assert result["normalized_query"] == "AI impact on software development"
        assert result["structured_query"] is not None
        assert result["structured_query"]["query_intent"] == "analytical"
        assert result["structured_query"]["scope"] == "moderate"
        assert len(result["evidence"]) == 2
        assert result["confidence"] == 0.6  # 1 filtered source → ceiling 0.60
        assert len(result["open_questions"]) == 1
        assert "Research completed" in result["summary"]


def test_research_flow_handles_retrieval_api_failure(monkeypatch):
    """Test research flow gracefully handles retrieval API failure (Phase 2A: adapter fallback)."""
    monkeypatch.setenv("RETRIEVAL_SERVICE_URL", "http://fake-api.com")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Mock failed API call (patch http_adapter, not retrieval.py)
    with patch('app.graphs.research.services.adapters.http_adapter.requests.post', side_effect=Exception("API down")):
        graph = create_research_graph()
        compiled_graph = graph.compile()

        initial_state = create_research_input(
            research_query="Test query",
            context={},
            task_id="test-integration-3"
        )

        result = compiled_graph.invoke(initial_state)

        # Should still complete with fallback mock sources
        assert len(result["sources"]) > 0
        assert result["summary"] != ""


def test_research_flow_with_empty_retrieval_results(monkeypatch):
    """Test research flow when retrieval returns no results."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch('app.graphs.research.nodes.retrieve.search_sources', return_value=[]):
        graph = create_research_graph()
        compiled_graph = graph.compile()

        initial_state = create_research_input(
            research_query="Obscure query with no results",
            context={},
            task_id="test-integration-4"
        )

        result = compiled_graph.invoke(initial_state)

        # Should handle empty results gracefully
        assert result["sources"] == []
        assert result["filtered_sources"] == []
        assert result["evidence"] == []
        assert result["confidence"] == 0.0
        assert "no evidence" in result["summary"].lower()


def test_research_flow_node_sequence():
    """Test that nodes execute in correct sequence."""
    graph = create_research_graph()
    compiled_graph = graph.compile()

    initial_state = create_research_input(
        research_query="Test sequence",
        context={},
        task_id="test-integration-5"
    )

    result = compiled_graph.invoke(initial_state)

    # Verify logs show correct sequence
    logs = result["logs"]
    assert len(logs) >= 5  # At least one log per node

    # Check that normalized_query is set before retrieval uses it
    assert result["normalized_query"] is not None

    # Check that sources are retrieved before filtering
    if result["sources"]:
        assert result["filtered_sources"] is not None

    # Check that evidence synthesis happens after filtering
    if result["filtered_sources"]:
        assert result["evidence"] is not None

    # Check that finalization happens last
    assert result["summary"] != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
