"""Targeted hybrid test — factual query with web sources in synthesis input.

Follow-up to validate_hybrid_research.py (which used local-only sources).
Tests whether adding Tavily-style web sources to filtered_sources changes:
  (a) hallucination rate on factual claims, and
  (b) confidence calibration.

Usage:
    python3 scripts/validate_hybrid_web_factual.py [--models mistral:7b-instruct ...]

Prerequisites:
    Ollama running locally: https://ollama.ai
    Models pulled: ollama pull mistral:7b-instruct
                   ollama pull qwen2.5:7b
                   ollama pull llama3.1:8b

Scenario SW — "factual + web sources":
    Query:        "What Python version does LangGraph require?"
    Local source: progress.txt — project milestone notes, NO version info
    Web source 1: LangGraph description — explicitly states "Python 3.9"
    Web source 2: lobster-agent tech stack — explicitly states "Python 3.9 or later"

    The local source is intentionally unchanged from original S3 in
    validate_hybrid_research.py, so the only variable is the web sources.

6 checks per model:
    SW1. JSON valid (no fallback)
    SW2. suggested_next_step is null  [HARD GATE]
    SW3. Evidence references web source content (model used web snippets)
    SW4. No hallucinated Python version beyond web source
           "3.9" is correct (in sources); "3.10"/"3.11"/"3.12" are hallucinations
    SW5. Confidence >= 0.55 (rich evidence — meaningful answer expected)
    SW6. Confidence <= 0.90 (not over-inflated)

Baseline from prior run (validate_hybrid_research.py — S3, local-only):
    All three models:   confidence 0.75–0.80  (FAILED "conf <= 0.5")
    mistral:7b-instruct: hallucinated Python version
    llama3.1:8b:         hallucinated Python version
    qwen2.5:7b:          no hallucination (passed S3 hallucination check)
    All three:           null discipline PASSED (hard gate)

A model becomes a candidate for factual+web synthesis if it passes all 6 checks.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.graphs.research.nodes import synthesize_evidence
import sys as _sys
_synth_mod = _sys.modules["app.graphs.research.nodes.synthesize_evidence"]

from app.graphs.research.state import ResearchState


# ---------------------------------------------------------------------------
# Scenario SW fixtures
# ---------------------------------------------------------------------------

# Local source — identical to S3 in validate_hybrid_research.py
# Deliberately contains NO Python version information.
_LOCAL_CONTENT = """\
V3 Milestone Status

Milestone 1: Complete — Next-step synthesis implemented. 176 tests passing.
Milestone 2: Complete — Project-state persistence, cross-session recall validated.
Current focus: V4 — External retrieval adapter (Tavily/Brave Search).
Adapter interface already defined in services/adapters/. New: WebSearchAdapter planned.
"""

# Web source 1 — LangGraph description with explicit Python version.
# "3.9" is the only version mentioned.  3.10 / 3.11 / 3.12 are NOT mentioned.
_WEB_CONTENT_1 = """\
LangGraph is a stateful graph orchestration library by LangChain for building \
multi-actor, multi-step LLM applications. It models workflows as directed graphs \
where each node is a callable that reads and writes shared state. \
LangGraph requires Python 3.9 as the minimum supported version.
"""

# Web source 2 — lobster-agent tech stack note.
_WEB_CONTENT_2 = """\
Lobster Agent technical stack: Python 3.9 or later, LangGraph for graph \
orchestration, Anthropic API (claude-haiku-4-5-20251001) for all LLM calls, \
SQLite for conversation thread persistence, pytest for testing.
"""

_LOCAL_SOURCE = {
    "url":               None,
    "title":             "progress.txt",
    "content":           _LOCAL_CONTENT,
    "reliability_score": 0.85,
    "metadata":          {"adapter": "local_file", "match_count": 3},
}

_WEB_SOURCE_1 = {
    "url":               "https://example.com/langgraph-docs",
    "title":             "LangGraph — Overview",
    "content":           _WEB_CONTENT_1,
    "reliability_score": 0.88,
    "metadata":          {"adapter": "web_search", "provider": "tavily", "rank": 1},
}

_WEB_SOURCE_2 = {
    "url":               "https://example.com/lobster-stack",
    "title":             "Lobster Agent Tech Stack",
    "content":           _WEB_CONTENT_2,
    "reliability_score": 0.75,
    "metadata":          {"adapter": "web_search", "provider": "tavily", "rank": 2},
}

_WORKSPACE_CTX = (
    "\nWorkspace history (recent tasks):\n"
    "- [2026-03-20] execution: \"Create progress.txt with V3 milestone status\""
    " → success, artifacts: [progress.txt]\n"
)

# Keywords that appear ONLY in web sources, NOT in the local file.
# Used to check whether evidence draws from web content.
_WEB_ONLY_KEYWORDS = {
    "langgraph", "stateful", "multi-actor", "orchestration",
    "directed graph", "callable", "python 3.9", "3.9",
}

# Python versions that are hallucinations given the sources.
# Sources mention ONLY "3.9". Any other specific version is an invention.
_HALLUCINATED_VERSIONS = [
    "python 3.10", "python 3.11", "python 3.12", "python 3.13",
    "python 2.", "python 3.8", "python 3.7",
    ">= 3.10", ">= 3.11", ">=3.10", ">=3.11",
    "requires 3.10", "requires 3.11", "minimum 3.10", "minimum 3.11",
]


def _make_sw_state() -> ResearchState:
    defaults: dict = {
        "research_query":    "What Python version does LangGraph require?",
        "context":           {"workspace_context": _WORKSPACE_CTX},
        "task_id":           "hybrid-web-test",
        "normalized_query":  "What Python version does LangGraph require?",
        "structured_query":  None,
        "retrieval_strategy": "hybrid",
        "sources":           [],
        "filtered_sources":  [_LOCAL_SOURCE, _WEB_SOURCE_1, _WEB_SOURCE_2],
        "filter_decisions":  [],
        "evidence":          [],
        "citations":         [],
        "summary":           "",
        "confidence":        0.0,
        "open_questions":    [],
        "suggested_next_step": None,
        "logs":              [],
    }
    return ResearchState(**defaults)


# ---------------------------------------------------------------------------
# Ollama shim  (identical to validate_hybrid_research.py)
# ---------------------------------------------------------------------------

class _Content:
    def __init__(self, text: str):
        self.text = text


class _OllamaResponse:
    def __init__(self, text: str):
        self.content = [_Content(text)]


class _OllamaMessages:
    def __init__(self, model: str, timeout: int, raw_store: dict, key: str):
        self._model   = model
        self._timeout = timeout
        self._store   = raw_store
        self._key     = key

    def create(self, model=None, max_tokens=None, system=None,
               messages=None, **kwargs) -> _OllamaResponse:
        import requests
        payload = {
            "model":    self._model,
            "messages": [{"role": "system", "content": system or ""}]
                        + (messages or []),
            "stream":   False,
            "options":  {"num_predict": max_tokens or 2000, "temperature": 0},
        }
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        self._store[self._key] = text
        return _OllamaResponse(text)


class _OllamaClient:
    def __init__(self, model: str, timeout: int, raw_store: dict, key: str):
        self.messages = _OllamaMessages(model, timeout, raw_store, key)


def _make_ollama_factory(model: str, timeout: int, raw_store: dict, key: str):
    def factory(api_key=None, **kwargs):
        return _OllamaClient(model, timeout, raw_store, key)
    return factory


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _fallback_used(result: dict) -> bool:
    return any("fallback" in log.lower() or "failed" in log.lower()
               for log in result.get("logs", []))


def _evidence_uses_web(evidence: list) -> bool:
    """At least one web-only keyword appears in evidence."""
    combined = " ".join(str(e) for e in evidence).lower()
    return any(kw in combined for kw in _WEB_ONLY_KEYWORDS)


def _no_hallucinated_version(evidence: list) -> bool:
    """Evidence doesn't claim a Python version not stated in the web sources.

    Web sources say only 'Python 3.9'. Any other specific version is
    an hallucination (the model is drawing from internal knowledge rather
    than the supplied sources).
    """
    combined = " ".join(str(e) for e in evidence).lower()
    return not any(h in combined for h in _HALLUCINATED_VERSIONS)


def evaluate_checks(result: dict) -> dict:
    evidence = result.get("evidence", [])
    conf     = result.get("confidence", 0.0)
    sug      = result.get("suggested_next_step")

    return {
        "SW1. JSON valid (no fallback)":
            not _fallback_used(result),

        "SW2. suggested_next_step is null  [HARD GATE]":
            sug is None,

        "SW3. Evidence draws from web source content":
            bool(evidence) and _evidence_uses_web(evidence),

        "SW4. No hallucinated Python version":
            _no_hallucinated_version(evidence),

        "SW5. Confidence >= 0.55  (rich evidence expected)":
            conf >= 0.55,

        "SW6. Confidence <= 0.90  (not over-inflated)":
            conf <= 0.90,
    }


# ---------------------------------------------------------------------------
# Baseline reference from prior S3 (local-only) run
# ---------------------------------------------------------------------------

# Hard-coded from validate_hybrid_research.py — S3, local-only sources.
# Used for delta comparison only; not re-run here.
_BASELINE = {
    "mistral:7b-instruct": {
        "S3 conf <= 0.5":            False,   # reported ~0.78
        "S3 no hallucinated version": False,  # invented Python version
        "S3 null discipline":         True,
    },
    "qwen2.5:7b": {
        "S3 conf <= 0.5":            False,   # reported ~0.75
        "S3 no hallucinated version": True,   # clean
        "S3 null discipline":         True,
    },
    "llama3.1:8b": {
        "S3 conf <= 0.5":            False,   # reported ~0.80
        "S3 no hallucinated version": False,  # invented Python version
        "S3 null discipline":         True,
    },
}

_BASELINE_CONF = {
    "mistral:7b-instruct": 0.78,
    "qwen2.5:7b":          0.75,
    "llama3.1:8b":         0.80,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_model(model: str, raw_store: dict, timeout: int = 90) -> dict:
    state   = _make_sw_state()
    key     = f"{model}_SW"
    factory = _make_ollama_factory(model, timeout, raw_store, key)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "ollama-local"}):
        with patch.object(_synth_mod, "Anthropic", factory):
            return synthesize_evidence(state)


def check_ollama(model: str, timeout: int = 10) -> bool:
    try:
        import requests
        r    = requests.get("http://localhost:11434/api/tags", timeout=timeout)
        tags = r.json().get("models", [])
        return any(t.get("name", "").startswith(model.split(":")[0]) for t in tags)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def hr(label="", width=68):
    if label:
        pad = width - len(label) - 4
        print(f"\n── {label} {'─' * max(pad, 1)}")
    else:
        print("─" * width)


def print_matrix(model: str, checks: dict, conf: float) -> None:
    passed = sum(1 for v in checks.values() if v)
    total  = len(checks)
    print(f"\n  Model: {model}")
    for label, ok in checks.items():
        gate = "  ← hard gate" if "HARD GATE" in label else ""
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}{gate}")
    print(f"\n  Score: {passed}/{total}   confidence={conf:.2f}")


def print_baseline_delta(model: str, new_checks: dict, new_conf: float) -> None:
    base = _BASELINE.get(model)
    if not base:
        return

    base_conf  = _BASELINE_CONF.get(model, 0.0)
    conf_delta = new_conf - base_conf

    halluc_before = not base["S3 no hallucinated version"]
    halluc_after  = not new_checks.get("SW4. No hallucinated Python version", True)
    halluc_change = (
        "improved (hallucination gone)"   if halluc_before and not halluc_after else
        "regressed (hallucination added)" if not halluc_before and halluc_after else
        "unchanged (no hallucination)"    if not halluc_before and not halluc_after else
        "unchanged (still hallucinating)"
    )

    conf_label = (
        f"{'↑' if conf_delta > 0.02 else '↓' if conf_delta < -0.02 else '≈'} "
        f"{base_conf:.2f} → {new_conf:.2f} (Δ{conf_delta:+.2f})"
    )

    print(f"\n  Delta vs baseline (S3 local-only):")
    print(f"    hallucination: {halluc_change}")
    print(f"    confidence:    {conf_label}")


def verdict(checks: dict) -> str:
    passed   = sum(1 for v in checks.values() if v)
    total    = len(checks)
    gate_key = "SW2. suggested_next_step is null  [HARD GATE]"
    if not checks.get(gate_key, False):
        return "NOT VIABLE — null-discipline gate failed"
    if passed == total:
        return "VIABLE — all checks pass"
    if passed >= 5:
        return f"BORDERLINE — {passed}/{total} checks pass"
    return f"NOT VIABLE — {passed}/{total} checks pass"


def save_raw(output_dir: Path, model: str, raw_text: str, result: dict) -> None:
    safe  = model.replace(":", "_").replace("/", "_")
    fname = output_dir / f"{safe}_SW_raw.json"
    payload = {
        "model":             model,
        "scenario":          "SW",
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "raw_response_text": raw_text,
        "fallback_used":     _fallback_used(result),
        "parsed_fields": {
            "evidence":            result.get("evidence", []),
            "citations":           result.get("citations", []),
            "confidence":          result.get("confidence"),
            "open_questions":      result.get("open_questions", []),
            "suggested_next_step": result.get("suggested_next_step"),
        },
        "logs": result.get("logs", []),
    }
    with open(fname, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"  raw saved → {fname}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(models: list) -> int:
    output_dir = Path(f"/tmp/lobster_hybrid_web_{int(time.time())}")
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir : {output_dir}")

    hr("SCENARIO")
    print("""\
  SW — factual + web sources
  Query:  "What Python version does LangGraph require?"
  Sources: 1 local (progress.txt, no version info)
           + 2 web  (both state "Python 3.9")
  Comparison: S3 from prior run used local-only (no version info in any source)
""")

    # Preflight
    unavailable = [m for m in models if not check_ollama(m)]
    if unavailable:
        print(f"\nERROR: models not available in Ollama: {unavailable}")
        print("Run: ollama pull <model>  for each missing model.")
        return 1

    raw_store:        dict = {}
    results_by_model: dict = {}

    for model in models:
        hr(f"MODEL: {model}")
        try:
            t0      = time.time()
            result  = run_model(model, raw_store)
            elapsed = time.time() - t0

            conf  = result.get("confidence", 0.0)
            n_ev  = len(result.get("evidence", []))
            sug   = result.get("suggested_next_step")
            fb    = "  [fallback]" if _fallback_used(result) else ""
            print(f"\n  {n_ev} evidence  conf={conf:.2f}  "
                  f"suggestion={repr(sug)[:60]}  ({elapsed:.1f}s){fb}")

            raw_text = raw_store.get(f"{model}_SW", "(not captured)")
            save_raw(output_dir, model, raw_text, result)

            checks = evaluate_checks(result)
            results_by_model[model] = (checks, result)
            print_matrix(model, checks, conf)
            print_baseline_delta(model, checks, conf)

        except Exception as exc:
            print(f"  ERROR: {exc}")
            empty = {
                "evidence": [], "citations": [], "confidence": 0.0,
                "open_questions": [], "suggested_next_step": None,
                "logs": [str(exc)],
            }
            checks = evaluate_checks(empty)
            results_by_model[model] = (checks, empty)
            raw_store[f"{model}_SW"] = f"ERROR: {exc}"
            save_raw(output_dir, model, f"ERROR: {exc}", empty)
            print_matrix(model, checks, 0.0)

    # Summary
    hr("VERDICT SUMMARY")
    for model, (checks, result) in results_by_model.items():
        v    = verdict(checks)
        conf = result.get("confidence", 0.0)
        print(f"  {model:30s}  conf={conf:.2f}  {v}")

    # Routing implication
    hr("ROUTING IMPLICATION")
    viable   = [m for m, (ch, _) in results_by_model.items()
                if "NOT VIABLE" not in verdict(ch)]
    borderline = [m for m, (ch, _) in results_by_model.items()
                  if "BORDERLINE" in verdict(ch)]
    not_viable = [m for m, (ch, _) in results_by_model.items()
                  if "NOT VIABLE" in verdict(ch)]

    print(f"""
  Factual query + web sources synthesis path:

  Viable candidates:   {viable   or ['none']}
  Borderline:          {borderline or ['none']}
  Not viable:          {not_viable or ['none']}

  Checks that most commonly distinguish viable from not viable on this path:
    SW3 (grounds in web content) — does the model USE the provided web sources?
    SW4 (no hallucinated version) — does web evidence prevent fabrication?
    SW5/SW6 (confidence range) — does richer evidence shift confidence correctly?

  If SW3 passes but SW4 fails:
    Model is reading web sources but still fabricating adjacent facts.
    Not viable for factual+web synthesis without prompt hardening.

  If SW4 passes but SW3 fails:
    Model avoided hallucination but ignored web sources entirely.
    Equivalent to local-only synthesis — web sources add no value.

  If both SW3 and SW4 pass:
    Model is using web content correctly and staying within it.
    Factual+web synthesis path is viable for this model.
""")

    hr()
    print("HYBRID WEB FACTUAL TEST: COMPLETE")
    hr()
    return 0


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--models", nargs="+",
        default=["mistral:7b-instruct", "qwen2.5:7b", "llama3.1:8b"],
        help="Ollama model names to test",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.models))
