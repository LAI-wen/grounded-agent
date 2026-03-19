"""Smoke tests for Research Subgraph.

These tests verify basic functionality of the Research Subgraph.
"""

import pytest
from app.graphs.research import create_research_graph, create_research_input


def test_research_graph_creation():
    """Test that research graph can be created successfully."""
    graph = create_research_graph()
    assert graph is not None
    compiled = graph.compile()
    assert compiled is not None


def test_research_input_creation():
    """Test that research input is created with correct structure."""
    research_input = create_research_input(
        research_query="Test query",
        context={},
        task_id="test-123"
    )

    assert research_input["research_query"] == "Test query"
    assert research_input["task_id"] == "test-123"
    assert research_input["sources"] == []
    assert research_input["confidence"] == 0.0


def test_research_workflow_execution():
    """Test that research workflow executes successfully."""
    graph = create_research_graph()
    compiled = graph.compile()

    research_input = create_research_input(
        research_query="What is machine learning?",
        context={},
        task_id="test-ml"
    )

    result = compiled.invoke(research_input)

    # Verify workflow completed
    assert result["summary"] != ""
    assert len(result["evidence"]) > 0
    assert 0.0 <= result["confidence"] <= 1.0
    assert len(result["logs"]) > 0


def test_research_source_filtering():
    """Test that source filtering occurs."""
    graph = create_research_graph()
    compiled = graph.compile()

    research_input = create_research_input(
        research_query="Test filtering",
        context={},
        task_id="test-filter"
    )

    result = compiled.invoke(research_input)

    # Should have retrieved and filtered sources
    assert "sources" in result
    assert "filtered_sources" in result
    # Filtered should be <= original
    assert len(result["filtered_sources"]) <= len(result["sources"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
