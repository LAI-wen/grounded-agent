"""Tests for LocalFileRetrievalAdapter."""

import os
import pytest
from pathlib import Path

from app.graphs.research.services.adapters.local_file_adapter import LocalFileRetrievalAdapter
from app.graphs.research.services.adapters.mock_adapter import MockRetrievalAdapter
from app.graphs.research.services.retrieval import get_retrieval_adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_always_true(self, tmp_path):
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))
        assert adapter.is_available() is True


# ---------------------------------------------------------------------------
# Basic search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_matching_file_returned(self, tmp_path):
        _write(tmp_path, "src/utils.py", "def langgraph_setup():\n    pass\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph setup")

        assert len(results) == 1
        assert results[0]["title"] == "src/utils.py"
        assert "langgraph_setup" in results[0]["content"]

    def test_no_matches_returns_empty(self, tmp_path):
        _write(tmp_path, "readme.txt", "nothing useful here\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph checkpointer")

        assert results == []

    def test_max_results_respected(self, tmp_path):
        for i in range(10):
            _write(tmp_path, f"file_{i}.py", f"langgraph node number {i}\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph", max_results=3)

        assert len(results) == 3

    def test_strategy_stored_in_metadata(self, tmp_path):
        _write(tmp_path, "module.py", "import langgraph\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph", strategy="keyword")

        assert results[0]["metadata"]["strategy"] == "keyword"

    def test_source_schema_fields(self, tmp_path):
        _write(tmp_path, "app.py", "langgraph is used here\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        r = adapter.search("langgraph")[0]

        assert r["url"] is None
        assert r["reliability_score"] == 0.85
        assert r["metadata"]["adapter"] == "local_file"
        assert r["metadata"]["is_mock"] is False

    def test_ranked_by_match_count(self, tmp_path):
        # file_a mentions term once; file_b mentions it three times
        _write(tmp_path, "file_a.py", "langgraph\n")
        _write(tmp_path, "file_b.py", "langgraph langgraph langgraph\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph")

        assert results[0]["title"] == "file_b.py"
        assert results[1]["title"] == "file_a.py"

    def test_excerpt_is_bounded(self, tmp_path):
        # Write a file with 100 lines; match is on line 50
        lines = [f"line {i}\n" for i in range(100)]
        lines[50] = "langgraph target line\n"
        _write(tmp_path, "big.py", "".join(lines))
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph")

        # Excerpt must be much shorter than the full file
        excerpt_lines = results[0]["content"].splitlines()
        assert len(excerpt_lines) <= 11  # 5 before + match + 5 after


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------

class TestSkipRules:
    def test_git_dir_skipped(self, tmp_path):
        _write(tmp_path, ".git/config", "langgraph mentioned here\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph")

        assert results == []

    def test_pycache_skipped(self, tmp_path):
        _write(tmp_path, "__pycache__/module.cpython-39.pyc", "langgraph\n")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph")

        assert results == []

    def test_binary_extension_skipped(self, tmp_path):
        p = tmp_path / "image.png"
        p.write_bytes(b"langgraph fake binary content")
        adapter = LocalFileRetrievalAdapter(root=str(tmp_path))

        results = adapter.search("langgraph")

        assert results == []


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------

class TestFactory:
    def test_factory_returns_local_when_no_http(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RETRIEVAL_SERVICE_URL", raising=False)
        adapter = get_retrieval_adapter()
        assert isinstance(adapter, LocalFileRetrievalAdapter)

    def test_factory_explicit_local(self):
        adapter = get_retrieval_adapter(adapter_type="local")
        assert isinstance(adapter, LocalFileRetrievalAdapter)

    def test_factory_explicit_mock_still_works(self):
        adapter = get_retrieval_adapter(adapter_type="mock")
        assert isinstance(adapter, MockRetrievalAdapter)
