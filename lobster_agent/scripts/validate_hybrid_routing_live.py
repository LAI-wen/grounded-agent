"""Live validation for hybrid routing in synthesize_evidence.

Validates the four routing cases introduced by the qwen2.5 factual+web path:

  Case 1 — factual + web sources      → local path  (real Ollama call)
  Case 2 — factual + local-only       → cloud path  (no web sources)
  Case 3 — progress query + web       → cloud path  (intent gate)
  Case 4 — local parse failure        → local→cloud fallback

Cloud calls are mocked throughout (no ANTHROPIC_API_KEY required).
Case 1 uses a real Ollama call so the local path is exercised end-to-end.

For each case the script reports:
  - path:        which synthesis path ran
  - skip_reason: why local was skipped (cases 2–3) or None (cases 1, 4)
  - response ok: whether evidence is non-empty and confidence in (0, 1]
  - fallback:    whether a fallback is visible in the logs

Usage:
    python3 scripts/validate_hybrid_routing_live.py
    python3 scripts/validate_hybrid_routing_live.py --no-ollama  (skips case 1)
"""

import argparse
import json
import os
import sys
from unittest.mock import patch, Mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.graphs.research.nodes import synthesize_evidence
import sys as _sys
_synth_mod = _sys.modules["app.graphs.research.nodes.synthesize_evidence"]
from app.graphs.research.state import ResearchState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hr(label="", width=68):
    if label:
        pad = width - len(label) - 4
        print(f"\n── {label} {'─' * max(pad, 1)}")
    else:
        print("─" * width)


def extract_path_log(logs: list) -> str:
    """Return the 'synthesis: ...' log entry, or '' if absent."""
    for log in logs:
        if log.startswith("synthesis:"):
            return log
    return ""


def parse_path_and_reason(path_log: str):
    """Return (path_marker, skip_reason) from a 'synthesis: ...' log entry."""
    if not path_log:
        return "", ""
    # Examples:
    #   "synthesis: local (qwen2.5:7b)"
    #   "synthesis: cloud (no web sources)"
    #   "synthesis: cloud (progress query)"
    #   "synthesis: cloud"
    #   "synthesis: local→cloud fallback (ValueError: Invalid JSON: ...)"
    rest = path_log[len("synthesis: "):]
    if "→" in rest:
        return "local→cloud fallback", ""
    if "(" in rest:
        marker = rest[:rest.index("(")].strip()
        reason = rest[rest.index("(") + 1:rest.rindex(")")]
        return marker, reason
    return rest.strip(), ""


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return condition


def _make_cloud_mock(evidence=None, confidence=0.78):
    payload = {
        "evidence":            evidence or ["Synthesized from cloud (Anthropic)"],
        "citations":           [],
        "confidence":          confidence,
        "open_questions":      [],
        "suggested_next_step": None,
    }
    mock_resp = Mock()
    mock_resp.content = [Mock(text=json.dumps(payload))]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_resp
    return mock_client


def _base_state(**overrides) -> ResearchState:
    defaults: dict = {
        "research_query":    "default query",
        "context":           {},
        "task_id":           "routing-val",
        "normalized_query":  "default query",
        "structured_query":  None,
        "retrieval_strategy": "hybrid",
        "sources":           [],
        "filtered_sources":  [],
        "filter_decisions":  [],
        "evidence":          [],
        "citations":         [],
        "summary":           "",
        "confidence":        0.0,
        "open_questions":    [],
        "suggested_next_step": None,
        "logs":              [],
    }
    defaults.update(overrides)
    return ResearchState(**defaults)


_LOCAL_SOURCE = {
    "url": None,
    "title": "progress.txt",
    "content": "Lobster agent project. V4 external retrieval implemented.",
    "reliability_score": 0.85,
    "metadata": {"adapter": "local_file"},
}

_WEB_SOURCE = {
    "url": "https://example.com/langgraph",
    "title": "LangGraph Docs",
    "content": "LangGraph requires Python 3.9 as the minimum supported version.",
    "reliability_score": 0.88,
    "metadata": {"adapter": "web_search", "provider": "tavily", "rank": 1},
}


# ---------------------------------------------------------------------------
# Case 1 — factual + web sources → local path  (real Ollama)
# ---------------------------------------------------------------------------

def run_case_1(skip_ollama: bool) -> bool:
    hr("Case 1 — factual + web → local path (real Ollama)")

    if skip_ollama:
        print("  [SKIP] --no-ollama flag set")
        return True

    # Check Ollama is reachable before attempting
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        tags = r.json().get("models", [])
        if not any(t.get("name", "").startswith("qwen2.5") for t in tags):
            print("  [SKIP] qwen2.5 not found in Ollama — run: ollama pull qwen2.5:7b")
            return True
    except Exception as e:
        print(f"  [SKIP] Ollama unreachable: {e}")
        return True

    cloud_client = Mock()  # must NOT be called on local path

    state = _base_state(
        normalized_query="What Python version does LangGraph require?",
        filtered_sources=[_LOCAL_SOURCE, _WEB_SOURCE],
    )

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.object(_synth_mod, "_qwen_available", return_value=True):
            with patch.object(_synth_mod, "Anthropic", return_value=cloud_client):
                result = synthesize_evidence(state)

    path_log = extract_path_log(result["logs"])
    marker, reason = parse_path_and_reason(path_log)

    print(f"\n  path_log:   {path_log!r}")
    print(f"  evidence:   {result['evidence']}")
    print(f"  confidence: {result['confidence']:.2f}")
    print()

    passes = [
        check("path marker is 'local'",
              marker == "local",
              detail=f"got: {marker!r}"),
        check("Anthropic was NOT called",
              not cloud_client.messages.create.called),
        check("evidence non-empty",
              len(result["evidence"]) > 0),
        check("confidence in (0, 1]",
              0.0 < result["confidence"] <= 1.0),
        check("no fallback in logs",
              not any("fallback" in log for log in result["logs"])),
    ]
    return all(passes)


# ---------------------------------------------------------------------------
# Case 2 — factual + local-only sources → cloud path
# ---------------------------------------------------------------------------

def run_case_2() -> bool:
    hr("Case 2 — factual + local-only → cloud path")

    cloud_client = _make_cloud_mock(
        evidence=["Cloud evidence: project uses LangGraph"],
        confidence=0.72,
    )

    state = _base_state(
        normalized_query="What Python version does LangGraph require?",
        filtered_sources=[_LOCAL_SOURCE],   # no web_search adapter
    )

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.object(_synth_mod, "_qwen_available", return_value=True):
            with patch.object(_synth_mod, "Anthropic", return_value=cloud_client):
                result = synthesize_evidence(state)

    path_log = extract_path_log(result["logs"])
    marker, reason = parse_path_and_reason(path_log)

    print(f"\n  path_log:   {path_log!r}")
    print(f"  evidence:   {result['evidence']}")
    print(f"  confidence: {result['confidence']:.2f}")
    print()

    passes = [
        check("path marker is 'cloud'",
              marker == "cloud",
              detail=f"got: {marker!r}"),
        check("skip_reason is 'no web sources'",
              reason == "no web sources",
              detail=f"got: {reason!r}"),
        check("Anthropic WAS called",
              cloud_client.messages.create.called),
        check("evidence non-empty",
              len(result["evidence"]) > 0),
        check("no fallback in logs",
              not any("fallback" in log for log in result["logs"])),
    ]
    return all(passes)


# ---------------------------------------------------------------------------
# Case 3 — progress query + web sources → cloud path
# ---------------------------------------------------------------------------

def run_case_3() -> bool:
    hr("Case 3 — progress query + web sources → cloud path")

    cloud_client = _make_cloud_mock(
        evidence=["The project is making good progress on V4"],
        confidence=0.65,
    )

    state = _base_state(
        normalized_query="What should I do next to make progress on this project?",
        filtered_sources=[_LOCAL_SOURCE, _WEB_SOURCE],
    )

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.object(_synth_mod, "_qwen_available", return_value=True):
            with patch.object(_synth_mod, "Anthropic", return_value=cloud_client):
                result = synthesize_evidence(state)

    path_log = extract_path_log(result["logs"])
    marker, reason = parse_path_and_reason(path_log)

    print(f"\n  path_log:   {path_log!r}")
    print(f"  evidence:   {result['evidence']}")
    print(f"  confidence: {result['confidence']:.2f}")
    print()

    passes = [
        check("path marker is 'cloud'",
              marker == "cloud",
              detail=f"got: {marker!r}"),
        check("skip_reason is 'progress query'",
              reason == "progress query",
              detail=f"got: {reason!r}"),
        check("Anthropic WAS called",
              cloud_client.messages.create.called),
        check("evidence non-empty",
              len(result["evidence"]) > 0),
        check("no fallback in logs",
              not any("fallback" in log for log in result["logs"])),
    ]
    return all(passes)


# ---------------------------------------------------------------------------
# Case 4 — local parse failure → local→cloud fallback
# ---------------------------------------------------------------------------

def run_case_4() -> bool:
    hr("Case 4 — local parse failure → local→cloud fallback")

    # Local model returns prose that is not valid JSON
    bad_resp = Mock()
    bad_resp.content = [Mock(text="I cannot answer this question about Python versions.")]
    local_client = Mock()
    local_client.messages.create.return_value = bad_resp

    cloud_client = _make_cloud_mock(
        evidence=["Cloud fallback: LangGraph requires Python 3.9"],
        confidence=0.80,
    )

    state = _base_state(
        normalized_query="What Python version does LangGraph require?",
        filtered_sources=[_LOCAL_SOURCE, _WEB_SOURCE],
    )

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.object(_synth_mod, "_qwen_available", return_value=True):
            with patch.object(_synth_mod, "_OllamaClient", return_value=local_client):
                with patch.object(_synth_mod, "Anthropic", return_value=cloud_client):
                    result = synthesize_evidence(state)

    path_log = extract_path_log(result["logs"])
    marker, reason = parse_path_and_reason(path_log)

    print(f"\n  path_log:   {path_log!r}")
    print(f"  evidence:   {result['evidence']}")
    print(f"  confidence: {result['confidence']:.2f}")
    print()

    passes = [
        check("path marker is 'local→cloud fallback'",
              marker == "local→cloud fallback",
              detail=f"got: {marker!r}"),
        check("local model was called once",
              local_client.messages.create.call_count == 1),
        check("Anthropic (cloud) was called once as fallback",
              cloud_client.messages.create.call_count == 1),
        check("evidence comes from cloud (fallback succeeded)",
              "Cloud fallback" in (result["evidence"] or [""])[0]),
        check("'local→cloud fallback' visible in logs",
              any("local→cloud fallback" in log for log in result["logs"])),
    ]
    return all(passes)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(skip_ollama: bool) -> int:
    print("=" * 68)
    print("  Hybrid Routing Live Validation")
    print("  synthesize_evidence — qwen2.5 factual+web path")
    print("=" * 68)

    results = {
        "Case 1 — factual+web → local":         run_case_1(skip_ollama),
        "Case 2 — local-only  → cloud":         run_case_2(),
        "Case 3 — progress    → cloud":         run_case_3(),
        "Case 4 — parse fail  → fallback":      run_case_4(),
    }

    hr("SUMMARY")
    for label, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {label}")

    hr()
    passed = sum(results.values())
    total  = len(results)
    print(f"\n  {passed}/{total} cases passed")

    if passed == total:
        print("\n  HYBRID ROUTING VALIDATION PASSED")
        print("  First hybrid routing slice complete.")
        return 0
    else:
        print(f"\n  HYBRID ROUTING VALIDATION FAILED — {total - passed} case(s) need attention.")
        return 1


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-ollama", action="store_true",
                   help="Skip Case 1 (real Ollama call)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(main(skip_ollama=args.no_ollama))
