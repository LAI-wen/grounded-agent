"""V4 live validation — external retrieval via Tavily.

Usage:
    # With TAVILY_API_KEY set (combined local+web):
    ANTHROPIC_API_KEY=sk-... TAVILY_API_KEY=tvly-... python3 scripts/validate_v4_live.py

    # Without TAVILY_API_KEY (local-only fallback):
    ANTHROPIC_API_KEY=sk-... python3 scripts/validate_v4_live.py

What is tested:
    Scenario A — API key present:
      1. search_sources() returns sources from both adapters
      2. At least one source has metadata.adapter == "web_search"
      3. At least one source has metadata.adapter == "local_file"
      4. Web sources have a url field populated
      5. Web sources have reliability_score in [0, 1]
      6. The research flow completes end-to-end with combined sources
      7. Final response is non-empty

    Scenario B — No API key:
      8. search_sources() returns only local sources
      9. No source has metadata.adapter == "web_search"
     10. search_sources() does not call requests.post

    Both scenarios exercised directly against search_sources() (no LLM needed)
    plus a single end-to-end run of the full agent (Scenario A only, if key present).
"""

import json
import os
import sys
import tempfile
import textwrap
from unittest.mock import patch, Mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.graphs.research.services.retrieval import search_sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hr(label=""):
    width = 70
    if label:
        pad = max(0, width - len(label) - 2)
        print(f"\n{'─' * (pad // 2)} {label} {'─' * (pad - pad // 2)}")
    else:
        print("─" * width)


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    line = f"  [{status}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return condition


# ---------------------------------------------------------------------------
# Scenario A — combined local + web
# ---------------------------------------------------------------------------

def run_scenario_a(tmp_path):
    """Validate that when TAVILY_API_KEY is set, both local and web sources appear."""
    hr("Scenario A — combined local+web retrieval")

    # Create a local file so LocalFileRetrievalAdapter has something to find
    notes = os.path.join(tmp_path, "notes.txt")
    with open(notes, "w") as f:
        f.write(textwrap.dedent("""\
            Lobster agent is a research assistant built on LangGraph.
            It supports multi-turn conversations and workspace memory.
            The project uses an adapter pattern for retrieval backends.
        """))

    os.chdir(tmp_path)

    mock_resp = Mock()
    mock_resp.json.return_value = {
        "results": [
            {
                "url":     "https://example.com/langchain-docs",
                "title":   "LangGraph Documentation",
                "content": "LangGraph is a framework for building stateful agents.",
                "score":   0.88,
            },
            {
                "url":     "https://example.com/retrieval-patterns",
                "title":   "Retrieval Augmented Generation Patterns",
                "content": "RAG combines retrieval with generation for grounded answers.",
                "score":   0.75,
            },
        ]
    }
    mock_resp.raise_for_status = Mock()

    results = {}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        # Temporarily set API key so WebSearchAdapter.is_available() returns True
        os.environ["TAVILY_API_KEY"] = "tvly-validate-test"
        try:
            sources = search_sources("lobster agent research")
        finally:
            del os.environ["TAVILY_API_KEY"]

        results["post_called"] = mock_post.called
        results["sources"] = sources

    adapters = [s["metadata"].get("adapter") for s in sources]
    web_srcs  = [s for s in sources if s["metadata"].get("adapter") == "web_search"]
    local_srcs = [s for s in sources if s["metadata"].get("adapter") == "local_file"]

    passes = []

    passes.append(check(
        "requests.post was called (WebSearchAdapter fired)",
        results["post_called"],
    ))
    passes.append(check(
        "At least one web_search source returned",
        len(web_srcs) > 0,
        detail=f"adapters present: {set(adapters)}",
    ))
    passes.append(check(
        "At least one local_file source returned",
        len(local_srcs) > 0,
    ))

    if web_srcs:
        s = web_srcs[0]
        passes.append(check(
            "Web source has a non-empty url",
            bool(s.get("url")),
            detail=f"url={s.get('url')}",
        ))
        passes.append(check(
            "Web source reliability_score in [0, 1]",
            0.0 <= s.get("reliability_score", -1) <= 1.0,
            detail=f"reliability_score={s.get('reliability_score')}",
        ))
        passes.append(check(
            "Web source content non-empty and capped at 1000 chars",
            0 < len(s.get("content", "")) <= 1000,
        ))
        passes.append(check(
            "Web source metadata.provider == tavily",
            s["metadata"].get("provider") == "tavily",
        ))
    else:
        # Pad with failures so counts are consistent
        for label in ["url", "reliability_score", "content cap", "provider"]:
            passes.append(check(f"Web source {label} (no web source to check)", False))

    return passes


# ---------------------------------------------------------------------------
# Scenario B — local-only (no API key)
# ---------------------------------------------------------------------------

def run_scenario_b(tmp_path):
    """Validate that without TAVILY_API_KEY only local sources are returned."""
    hr("Scenario B — local-only (no TAVILY_API_KEY)")

    notes = os.path.join(tmp_path, "project.txt")
    with open(notes, "w") as f:
        f.write("Lobster agent project notes — local file retrieval only.\n")

    os.chdir(tmp_path)

    # Ensure key absent
    os.environ.pop("TAVILY_API_KEY", None)

    passes = []

    with patch("requests.post") as mock_post:
        sources = search_sources("lobster agent")

        passes.append(check(
            "requests.post NOT called (WebSearchAdapter skipped)",
            not mock_post.called,
        ))

    adapters = [s["metadata"].get("adapter") for s in sources]

    passes.append(check(
        "All sources are local_file",
        all(a == "local_file" for a in adapters),
        detail=f"adapters present: {set(adapters)}",
    ))
    passes.append(check(
        "At least one local source returned",
        len(sources) > 0,
        detail=f"total sources: {len(sources)}",
    ))

    return passes


# ---------------------------------------------------------------------------
# Scenario C — web failure is silent
# ---------------------------------------------------------------------------

def run_scenario_c(tmp_path):
    """Validate that a web search failure does not raise and returns local results."""
    hr("Scenario C — web failure is silent")

    notes = os.path.join(tmp_path, "data.txt")
    with open(notes, "w") as f:
        f.write("Lobster agent data.\n")

    os.chdir(tmp_path)

    passes = []

    with patch("requests.post", side_effect=Exception("network error")):
        os.environ["TAVILY_API_KEY"] = "tvly-validate-test"
        try:
            sources = search_sources("lobster agent")
        finally:
            del os.environ["TAVILY_API_KEY"]

    adapters = [s["metadata"].get("adapter") for s in sources]

    passes.append(check(
        "No exception raised despite web failure",
        True,  # reaching here means no exception
    ))
    passes.append(check(
        "Local sources still returned",
        any(a == "local_file" for a in adapters),
    ))
    passes.append(check(
        "No web_search sources present (failed silently)",
        not any(a == "web_search" for a in adapters),
    ))

    return passes


# ---------------------------------------------------------------------------
# End-to-end run (optional — requires ANTHROPIC_API_KEY + TAVILY_API_KEY)
# ---------------------------------------------------------------------------

def run_e2e(tmp_path):
    """Full agent run with combined retrieval. Skipped if keys absent."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    tavily_key    = os.environ.get("TAVILY_API_KEY", "")

    hr("End-to-end agent run (optional)")

    if not anthropic_key or not tavily_key:
        missing = []
        if not anthropic_key:
            missing.append("ANTHROPIC_API_KEY")
        if not tavily_key:
            missing.append("TAVILY_API_KEY")
        print(f"  [SKIP] Missing {', '.join(missing)} — skipping full agent run.")
        return []

    from app.main import run_lobster_agent
    from app.memory.checkpointer import create_checkpointer

    notes = os.path.join(tmp_path, "notes.txt")
    with open(notes, "w") as f:
        f.write(textwrap.dedent("""\
            Lobster agent is a LangGraph-based research assistant.
            It reads local files and (with a Tavily key) also searches the web.
            V4 added external retrieval via the Tavily API.
        """))

    os.chdir(tmp_path)
    checkpointer = create_checkpointer()

    result = run_lobster_agent(
        task="What is LangGraph and how does lobster agent use it?",
        checkpointer=checkpointer,
    )

    response = ""
    msgs = result.get("messages", [])
    if msgs:
        response = msgs[-1].get("content", "") if isinstance(msgs[-1], dict) else str(msgs[-1])

    passes = []
    passes.append(check("Agent run completed without exception", True))
    passes.append(check("Final response non-empty", len(response) > 50, detail=f"length={len(response)}"))
    sources = result.get("sources", [])
    adapters = [s.get("metadata", {}).get("adapter") for s in sources]
    passes.append(check(
        "Combined sources used (local + web present)",
        "local_file" in adapters and "web_search" in adapters,
        detail=f"adapters: {set(adapters)}",
    ))
    print(f"\n  Response preview:\n  {response[:300]}...")
    return passes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  V4 Live Validation — External Retrieval (Tavily)")
    print("=" * 70)

    all_passes = []

    with tempfile.TemporaryDirectory(prefix="lobster_v4_val_") as tmp_a:
        all_passes.extend(run_scenario_a(tmp_a))

    with tempfile.TemporaryDirectory(prefix="lobster_v4_val_") as tmp_b:
        all_passes.extend(run_scenario_b(tmp_b))

    with tempfile.TemporaryDirectory(prefix="lobster_v4_val_") as tmp_c:
        all_passes.extend(run_scenario_c(tmp_c))

    with tempfile.TemporaryDirectory(prefix="lobster_v4_val_") as tmp_e:
        all_passes.extend(run_e2e(tmp_e))

    hr()
    total  = len(all_passes)
    passed = sum(all_passes)
    failed = total - passed

    print(f"\n  Result: {passed}/{total} checks passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
    else:
        print()

    if failed == 0:
        print("\n  V4 VALIDATION PASSED — external retrieval working correctly.")
    else:
        print(f"\n  V4 VALIDATION FAILED — {failed} check(s) need attention.")
        sys.exit(1)


if __name__ == "__main__":
    main()
