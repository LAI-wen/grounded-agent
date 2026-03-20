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
        "suggested_next_step": None,
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
    """Test retrieve_sources node maps search_sources output to Source schema correctly."""
    fake_sources = [
        {
            "url": "http://example.com/1",
            "title": "Test Source 1",
            "content": "Test content 1",
            "reliability_score": 0.9,
            "metadata": {"adapter": "http"},
        }
    ]

    with patch('app.graphs.research.nodes.retrieve.search_sources', return_value=fake_sources):
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
            assert result["confidence"] == 0.75  # 2 local-only sources → ceiling 0.75
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
    """Test synthesize_evidence early-returns when sources AND workspace_context are both absent."""
    state = create_test_state(filtered_sources=[], context={})
    result = synthesize_evidence(state)

    assert result["evidence"] == []
    assert result["confidence"] == 0.0
    assert "No sources available" in result["summary"]


def test_synthesize_evidence_workspace_context_bypasses_early_return():
    """When filtered_sources is empty but workspace_context is present, LLM is called."""
    import json as json_module

    llm_response = {
        "evidence": ["hello.txt was created in the previous session"],
        "citations": [],
        "confidence": 0.8,
        "open_questions": [],
    }
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    workspace_ctx = "\nWorkspace history (recent tasks):\n- [2026-03-20] execution: \"Create hello.txt\" → success, artifacts: [hello.txt]\n"
    state = create_test_state(
        filtered_sources=[],
        context={"workspace_context": workspace_ctx},
        normalized_query="What files have you created?",
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
            result = synthesize_evidence(state)

    # LLM was called (not early-returned)
    mock_client.messages.create.assert_called_once()
    # workspace_context appeared in the prompt
    call_kwargs = mock_client.messages.create.call_args
    prompt_text = call_kwargs[1]["messages"][0]["content"]
    assert "Workspace history" in prompt_text
    # Result came from LLM
    assert result["evidence"] == ["hello.txt was created in the previous session"]
    assert result["confidence"] == 0.6  # 0 filtered sources → ceiling 0.60


def test_synthesize_evidence_workspace_context_in_prompt():
    """workspace_context block is included in the formatted user prompt."""
    import json as json_module

    llm_response = {"evidence": [], "citations": [], "confidence": 0.5, "open_questions": []}
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    workspace_ctx = "\nWorkspace history (recent tasks):\n- [2026-03-20] execution: \"Build X\" → success\n"
    source = {"title": "Doc", "content": "Some content", "reliability_score": 0.7, "metadata": {}}
    state = create_test_state(
        filtered_sources=[source],
        context={"workspace_context": workspace_ctx},
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
            synthesize_evidence(state)

    call_kwargs = mock_client.messages.create.call_args
    prompt_text = call_kwargs[1]["messages"][0]["content"]
    assert "Workspace history" in prompt_text


def test_synthesize_evidence_both_sources_present_in_prompt():
    """When both filtered_sources and workspace_context are present, the LLM prompt
    contains both the file source excerpts and the workspace history — neither is
    dropped or used as a fallback for the other (M3 behaviour)."""
    import json as json_module

    llm_response = {
        "evidence": ["notes.txt exists from prior session", "README describes the project"],
        "citations": ["workspace history", "README"],
        "confidence": 0.9,
        "open_questions": [],
    }
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    workspace_ctx = (
        "\nWorkspace history (recent tasks):\n"
        "- [2026-03-20] execution: \"Create notes.txt\" → success, artifacts: [notes.txt]\n"
    )
    source = {
        "title": "README",
        "content": "This project is a multi-agent assistant.",
        "reliability_score": 0.8,
        "metadata": {},
    }
    state = create_test_state(
        filtered_sources=[source],
        context={"workspace_context": workspace_ctx},
        normalized_query="Summarise what this project has done so far",
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
            result = synthesize_evidence(state)

    call_kwargs = mock_client.messages.create.call_args
    prompt_text = call_kwargs[1]["messages"][0]["content"]

    # Both file source content and workspace history must be in the same prompt
    assert "README" in prompt_text, "file source title missing from prompt"
    assert "multi-agent assistant" in prompt_text, "file source content missing from prompt"
    assert "Workspace history" in prompt_text, "workspace history missing from prompt"
    assert "notes.txt" in prompt_text, "workspace artifact missing from prompt"

    # Log must identify the combined-source path
    assert any("combined" in log for log in result["logs"]), \
        "expected combined-source log entry"


def test_suggested_next_step_for_progress_query():
    """When LLM returns a suggested_next_step for a progress query, it flows through
    to the result (V3 M1 behaviour)."""
    import json as json_module

    llm_response = {
        "evidence": ["notes.txt was created on 2026-03-20"],
        "citations": ["workspace history"],
        "confidence": 0.8,
        "open_questions": [],
        "suggested_next_step": "Extend notes.txt with a summary of V3 M1 completion",
    }
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    state = create_test_state(
        filtered_sources=[{"title": "notes.txt", "content": "Project started.", "reliability_score": 0.8, "metadata": {}}],
        research_query="What should I do next to make progress on this project?",
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
            result = synthesize_evidence(state)

    assert result["suggested_next_step"] == "Extend notes.txt with a summary of V3 M1 completion"


def test_suggested_next_step_null_for_factual_query():
    """When LLM returns null for suggested_next_step (factual query), result is None."""
    import json as json_module

    llm_response = {
        "evidence": ["The safety check function rejects path traversal"],
        "citations": ["safety.py"],
        "confidence": 0.9,
        "open_questions": [],
        "suggested_next_step": None,
    }
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    state = create_test_state(
        filtered_sources=[{"title": "safety.py", "content": "def safety_check():", "reliability_score": 0.9, "metadata": {}}],
        research_query="How does the safety check work?",
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
            result = synthesize_evidence(state)

    assert result["suggested_next_step"] is None


def test_suggested_next_step_in_final_response():
    """When research_result contains a suggested_next_step, _compose_response includes it."""
    from app.graphs.main.nodes import _compose_response

    research_result = {
        "summary": "The project has created notes.txt.",
        "evidence": ["notes.txt was created"],
        "citations": ["workspace history"],
        "confidence": 0.8,
        "open_questions": [],
        "suggested_next_step": "Extend notes.txt with a V3 progress entry",
    }

    response = _compose_response(None, None, research_result)

    assert "Suggested next step:" in response
    assert "Extend notes.txt with a V3 progress entry" in response


# ===== synthesize_evidence hybrid routing tests =====

def _web_source(title="LangGraph Docs"):
    return {
        "url": "https://example.com/doc",
        "title": title,
        "content": "LangGraph requires Python 3.9 as the minimum supported version.",
        "reliability_score": 0.88,
        "metadata": {"adapter": "web_search", "provider": "tavily", "rank": 1},
    }


def _local_source(title="progress.txt"):
    return {
        "url": None,
        "title": title,
        "content": "V3 milestone complete. Current focus: V4 external retrieval.",
        "reliability_score": 0.85,
        "metadata": {"adapter": "local_file"},
    }


def _mock_synthesis_response(evidence=None, confidence=0.80):
    import json as _json
    payload = {
        "evidence":            evidence or ["LangGraph requires Python 3.9"],
        "citations":           [],
        "confidence":          confidence,
        "open_questions":      [],
        "suggested_next_step": None,
    }
    mock_resp = Mock()
    mock_resp.content = [Mock(text=_json.dumps(payload))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_resp
    return mock_client


def test_synthesize_evidence_routes_to_local_for_factual_web_query():
    """Factual query + web source + qwen available → local path; Anthropic not called."""
    local_client  = _mock_synthesis_response()
    cloud_client  = Mock()

    state = create_test_state(
        normalized_query="What Python version does LangGraph require?",
        filtered_sources=[_local_source(), _web_source()],
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=True):
            with patch.object(synthesize_evidence_mod, "_OllamaClient", return_value=local_client):
                with patch.object(synthesize_evidence_mod, "Anthropic", return_value=cloud_client):
                    result = synthesize_evidence(state)

    local_client.messages.create.assert_called_once()
    cloud_client.messages.create.assert_not_called()
    assert any("synthesis: local" in log for log in result["logs"])
    assert result["evidence"] == ["LangGraph requires Python 3.9"]


def test_synthesize_evidence_stays_cloud_for_local_only_sources():
    """No web sources → cloud path; log records skip reason 'no web sources'."""
    cloud_client = _mock_synthesis_response()

    state = create_test_state(
        normalized_query="What Python version does LangGraph require?",
        filtered_sources=[_local_source()],   # no web_search adapter
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=True):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=cloud_client):
                result = synthesize_evidence(state)

    cloud_client.messages.create.assert_called_once()
    assert any("no web sources" in log for log in result["logs"])


def test_synthesize_evidence_stays_cloud_for_progress_query_with_web_sources():
    """Progress/next-step query → cloud path even when web sources are present."""
    cloud_client = _mock_synthesis_response()

    state = create_test_state(
        normalized_query="What should I do next to make progress on this project?",
        filtered_sources=[_local_source(), _web_source()],
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=True):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=cloud_client):
                result = synthesize_evidence(state)

    cloud_client.messages.create.assert_called_once()
    assert any("progress query" in log for log in result["logs"])


def test_synthesize_evidence_falls_back_to_cloud_on_local_parse_failure():
    """Invalid JSON from local model → one cloud retry; log records fallback path."""
    import json as _json

    # Local model returns unparseable text (not valid JSON)
    bad_local_resp = Mock()
    bad_local_resp.content = [Mock(text="Sorry, I cannot answer that.")]
    local_client = Mock()
    local_client.messages.create.return_value = bad_local_resp

    cloud_client = _mock_synthesis_response(evidence=["LangGraph requires Python 3.9"])

    state = create_test_state(
        normalized_query="What Python version does LangGraph require?",
        filtered_sources=[_local_source(), _web_source()],
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=True):
            with patch.object(synthesize_evidence_mod, "_OllamaClient", return_value=local_client):
                with patch.object(synthesize_evidence_mod, "Anthropic", return_value=cloud_client):
                    result = synthesize_evidence(state)

    local_client.messages.create.assert_called_once()
    cloud_client.messages.create.assert_called_once()
    assert any("local→cloud fallback" in log for log in result["logs"])
    assert result["evidence"] == ["LangGraph requires Python 3.9"]


def test_synthesize_evidence_concise_preference_in_system_prompt():
    """When workspace_context contains a 'concise' preference, the system prompt
    passed to the LLM contains the hard IMPORTANT instruction (not just user context)."""
    import json as json_module

    llm_response = {
        "evidence": ["Lobster Agent uses LangGraph"],
        "citations": ["notes.txt"],
        "confidence": 0.8,
        "open_questions": [],
        "suggested_next_step": None,
    }
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    workspace_ctx = (
        "\nUser preferences (apply to this response):\n"
        "  - Keep responses concise (set 2026-03-20)\n"
    )
    source = {"title": "notes.txt", "content": "Lobster Agent project notes.", "reliability_score": 0.8, "metadata": {}}
    state = create_test_state(
        filtered_sources=[source],
        context={"workspace_context": workspace_ctx},
        normalized_query="Summarise this project.",
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
                synthesize_evidence(state)

    call_kwargs = mock_client.messages.create.call_args[1]
    system_text = call_kwargs["system"]
    assert "IMPORTANT" in system_text
    assert "concise" in system_text.lower()
    # Base system prompt still present
    assert "research synthesizer" in system_text.lower()


def test_synthesize_evidence_no_preference_uses_base_system_prompt():
    """When no preference is active, the system prompt is the unmodified base prompt."""
    import json as json_module

    llm_response = {"evidence": ["claim"], "citations": [], "confidence": 0.7, "open_questions": []}
    mock_response = Mock()
    mock_response.content = [Mock(text=json_module.dumps(llm_response))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response

    source = {"title": "doc.txt", "content": "content", "reliability_score": 0.7, "metadata": {}}
    state = create_test_state(
        filtered_sources=[source],
        context={},
        normalized_query="What is X?",
    )

    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=mock_client):
                synthesize_evidence(state)

    call_kwargs = mock_client.messages.create.call_args[1]
    system_text = call_kwargs["system"]
    # ceiling instruction added for 1 source; no preference instruction
    assert "Keep responses concise" not in system_text
    assert "Give detailed responses" not in system_text


# ===== confidence over-claim guard tests =====

def _make_high_conf_client(confidence=0.99):
    """Mock client that always returns confidence=0.99 (over-claiming model)."""
    import json as _json
    payload = {
        "evidence":            ["claim"],
        "citations":           [],
        "confidence":          confidence,
        "open_questions":      [],
        "suggested_next_step": None,
    }
    mock_resp = Mock()
    mock_resp.content = [Mock(text=_json.dumps(payload))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_resp
    return mock_client


def test_confidence_ceiling_1_source_clamps_to_0_60():
    """1-source synthesis: model confidence 0.99 is clamped to 0.60."""
    client = _make_high_conf_client(confidence=0.99)
    state = create_test_state(
        filtered_sources=[_local_source()],
        normalized_query="What is X?",
    )
    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=client):
                result = synthesize_evidence(state)

    assert result["confidence"] == 0.60
    assert any("confidence over-claim guard" in log for log in result["logs"])
    assert any("<=1 source" in log for log in result["logs"])


def test_confidence_ceiling_2_3_local_only_clamps_to_0_75():
    """2 local-only sources: model confidence 0.99 is clamped to 0.75."""
    client = _make_high_conf_client(confidence=0.99)
    state = create_test_state(
        filtered_sources=[_local_source("a.txt"), _local_source("b.txt")],
        normalized_query="What is X?",
    )
    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=client):
                result = synthesize_evidence(state)

    assert result["confidence"] == 0.75
    assert any("2-3 local-only" in log for log in result["logs"])


def test_confidence_ceiling_2_3_with_web_clamps_to_0_80():
    """2 sources including a web source: model confidence 0.99 is clamped to 0.80."""
    client = _make_high_conf_client(confidence=0.99)
    state = create_test_state(
        filtered_sources=[_local_source(), _web_source()],
        normalized_query="What is X?",
    )
    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=client):
                result = synthesize_evidence(state)

    assert result["confidence"] == 0.80
    assert any("2-3 sources with web" in log for log in result["logs"])


def test_confidence_ceiling_4_plus_sources_unchanged():
    """4+ sources: no ceiling applied; model confidence preserved."""
    client = _make_high_conf_client(confidence=0.99)
    sources = [_local_source(f"file{i}.txt") for i in range(4)]
    state = create_test_state(
        filtered_sources=sources,
        normalized_query="What is X?",
    )
    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=client):
                result = synthesize_evidence(state)

    assert result["confidence"] == 0.99
    assert not any("confidence over-claim guard" in log for log in result["logs"])


def test_confidence_ceiling_no_clamp_when_model_already_below():
    """Model returns 0.50 against a 1-source ceiling of 0.60 — no clamp, no log entry."""
    client = _make_high_conf_client(confidence=0.50)
    state = create_test_state(
        filtered_sources=[_local_source()],
        normalized_query="What is X?",
    )
    with patch.object(synthesize_evidence_mod.os.environ, "get", return_value="test-key"):
        with patch.object(synthesize_evidence_mod, "_qwen_available", return_value=False):
            with patch.object(synthesize_evidence_mod, "Anthropic", return_value=client):
                result = synthesize_evidence(state)

    assert result["confidence"] == 0.50
    assert not any("confidence over-claim guard" in log for log in result["logs"])


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
