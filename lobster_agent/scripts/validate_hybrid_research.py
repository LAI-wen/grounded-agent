"""Hybrid viability test — synthesize_evidence node, research layer only.

Usage:
    python3 scripts/validate_hybrid_research.py [--models mistral:7b-instruct llama3.1:8b]

Prerequisites:
    Ollama running locally: https://ollama.ai
    Models pulled:  ollama pull mistral:7b-instruct
                    ollama pull llama3.1:8b

Tests synthesize_evidence against three scenarios:
  S1: Project-state summary  → suggestion expected, evidence grounded
  S2: Next-step query        → suggestion expected, concrete + traceable
  S3: Factual query          → suggested_next_step must be null  (hard gate)

Produces per model:
  - 8-check pass/fail matrix
  - Raw model output saved to /tmp/lobster_hybrid_<timestamp>/
  - Overall verdict: VIABLE / BORDERLINE / NOT VIABLE
  - Role-level routing recommendation
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

# Import via the package so __init__.py runs and registers all submodules,
# then retrieve the actual module object from sys.modules — same pattern as
# test_nodes.py.  Direct "import nodes.synthesize_evidence" resolves to the
# *function* (bound by __init__.py), not the submodule.
from app.graphs.research.nodes import synthesize_evidence          # loads submodule
import sys as _sys
_synth_mod = _sys.modules["app.graphs.research.nodes.synthesize_evidence"]

from app.graphs.research.state import ResearchState


# ---------------------------------------------------------------------------
# Fixtures — shared across all scenarios
# ---------------------------------------------------------------------------

_PROGRESS_CONTENT = """\
V3 Milestone Status

Milestone 1: Complete — Next-step synthesis implemented. 176 tests passing.
Milestone 2: Complete — Project-state persistence, cross-session recall validated.
Current focus: V4 — External retrieval adapter (Tavily/Brave Search).
Adapter interface already defined in services/adapters/. New: WebSearchAdapter planned.
"""

_WORKSPACE_CTX = (
    "\nWorkspace history (recent tasks):\n"
    "- [2026-03-20] execution: \"Create progress.txt with V3 milestone status\""
    " → success, artifacts: [progress.txt]\n"
    "- [2026-03-20] research: \"What should I do next to make progress?\" → success\n"
    "Most recent suggestion: Extend progress.txt with V4 implementation timeline"
    " and adapter selection rationale (2026-03-20)\n"
)

_PROGRESS_SOURCE = {
    "url": None,
    "title": "progress.txt",
    "content": _PROGRESS_CONTENT,
    "reliability_score": 0.85,
    "metadata": {"adapter": "local_file", "match_count": 5},
}

# Terms that must appear in evidence to count as grounded in the source
_SOURCE_KEYWORDS = {
    "milestone", "synthesis", "tests", "passing", "persistence", "recall",
    "retrieval", "adapter", "progress", "v4", "v3", "complete",
}

# Suggestions matching these phrases are considered generic (not concrete enough)
_GENERIC_PHRASES = [
    "keep working", "continue working", "continue making progress",
    "keep making progress", "continue documenting", "keep documenting",
    "review your work", "review the work", "further document",
    "make progress", "keep going",
]


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> ResearchState:
    defaults: dict = {
        "research_query": "",
        "context": {},
        "task_id": "hybrid-test",
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


SCENARIOS = {
    "S1": {
        "label": "S1 — project-state summary  (suggestion expected)",
        "state": _base_state(
            research_query="Summarise the current state of this project.",
            normalized_query="Summarise the current state of this project.",
            filtered_sources=[_PROGRESS_SOURCE],
            context={"workspace_context": _WORKSPACE_CTX},
        ),
    },
    "S2": {
        "label": "S2 — next-step query  (concrete suggestion expected)",
        "state": _base_state(
            research_query="What should I do next to make progress on this project?",
            normalized_query="What should I do next to make progress on this project?",
            filtered_sources=[_PROGRESS_SOURCE],
            context={"workspace_context": _WORKSPACE_CTX},
        ),
    },
    "S3": {
        "label": "S3 — factual query  (null discipline — hard gate)",
        "state": _base_state(
            research_query="What Python version does this project require?",
            normalized_query="What Python version does this project require?",
            filtered_sources=[_PROGRESS_SOURCE],  # source has no Python version info
            context={"workspace_context": _WORKSPACE_CTX},
        ),
    },
}


# ---------------------------------------------------------------------------
# Ollama client shim
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
            "model":   self._model,
            "messages": [{"role": "system", "content": system or ""}]
                        + (messages or []),
            "stream":  False,
            "options": {"num_predict": max_tokens or 2000, "temperature": 0},
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


def _make_ollama_factory(model: str, timeout: int,
                         raw_store: dict, key: str):
    """Return a callable matching the Anthropic(api_key=...) signature."""
    def factory(api_key=None, **kwargs):
        return _OllamaClient(model, timeout, raw_store, key)
    return factory


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _fallback_used(result: dict) -> bool:
    return any("fallback" in log.lower() or "failed" in log.lower()
               for log in result.get("logs", []))


def _is_generic(suggestion: str) -> bool:
    low = suggestion.lower()
    return any(ph in low for ph in _GENERIC_PHRASES)


def _grounded_in_source(text: str) -> bool:
    """At least one source keyword appears in text."""
    low = text.lower()
    return any(kw in low for kw in _SOURCE_KEYWORDS)


def _traceable_to_evidence(suggestion: str, evidence: list) -> bool:
    """At least one word >4 chars from the suggestion appears in evidence."""
    words = [w.strip(".,;:\"'()").lower()
             for w in suggestion.split() if len(w) > 4]
    evidence_text = " ".join(str(e) for e in evidence).lower()
    return any(w in evidence_text for w in words)


def _has_python_hallucination(evidence: list) -> bool:
    """True if evidence invents Python version info not in the source."""
    combined = " ".join(str(e) for e in evidence).lower()
    return any(ph in combined for ph in [
        "python 3.", "python 2.", "requires python", "python version",
        ">= 3", ">= 2", "3.9", "3.10", "3.11", "3.12",
    ])


def evaluate_checks(s1: dict, s2: dict, s3: dict) -> dict:
    """Compute the 8 binary checks from three scenario results."""
    s1_sug = s1.get("suggested_next_step") or ""
    s2_sug = s2.get("suggested_next_step") or ""
    s3_sug = s3.get("suggested_next_step")

    return {
        "S1 JSON valid (no fallback)":
            not _fallback_used(s1),

        "S1 evidence grounded in source":
            bool(s1.get("evidence"))
            and _grounded_in_source(" ".join(s1.get("evidence", []))),

        "S1 suggestion present and non-generic":
            bool(s1_sug) and not _is_generic(s1_sug),

        "S2 suggestion names specific artifact":
            bool(s2_sug)
            and _grounded_in_source(s2_sug),  # must reference source vocabulary

        "S2 suggestion traceable to evidence":
            bool(s2_sug)
            and _traceable_to_evidence(s2_sug, s2.get("evidence", [])),

        "S3 suggested_next_step is null  [HARD GATE]":
            s3_sug is None,

        "S3 no hallucinated Python version":
            not _has_python_hallucination(s3.get("evidence", [])),

        "S3 confidence <= 0.5":
            s3.get("confidence", 1.0) <= 0.5,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(model: str, scenario_id: str, scenario: dict,
                 raw_store: dict, timeout: int = 90) -> dict:
    """Run one scenario through synthesize_evidence patched to use Ollama."""
    state = scenario["state"]
    store_key = f"{model}_{scenario_id}"

    factory = _make_ollama_factory(model, timeout, raw_store, store_key)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "ollama-local"}):
        with patch.object(_synth_mod, "Anthropic", factory):
            result = synthesize_evidence(state)

    return result


def check_ollama(model: str, timeout: int = 10) -> bool:
    """Return True if Ollama is reachable and the model is available."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=timeout)
        tags = r.json().get("models", [])
        names = [t.get("name", "") for t in tags]
        # model names in Ollama can include :latest suffix
        return any(m.startswith(model.split(":")[0]) for m in names)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def hr(label="", width=68):
    if label:
        pad = width - len(label) - 4
        print(f"\n── {label} {'─' * max(pad, 1)}")
    else:
        print("─" * width)


def print_matrix(model: str, checks: dict) -> None:
    passed = sum(1 for v in checks.values() if v)
    total  = len(checks)
    print(f"\n  Model: {model}")
    for label, ok in checks.items():
        gate = "  ← hard gate" if "HARD GATE" in label else ""
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}{gate}")
    print(f"\n  Score: {passed}/{total}")


def verdict(checks: dict) -> str:
    passed   = sum(1 for v in checks.values() if v)
    total    = len(checks)
    gate_key = "S3 suggested_next_step is null  [HARD GATE]"
    if not checks.get(gate_key, False):
        return "NOT VIABLE — null-discipline hard gate failed"
    if passed == total:
        return "VIABLE — all checks pass"
    if passed >= 6:
        return f"BORDERLINE — {passed}/{total} checks pass (gate passed)"
    return f"NOT VIABLE — {passed}/{total} checks pass"


def print_recommendation(results_by_model: dict) -> None:
    hr("ROLE-LEVEL ROUTING RECOMMENDATION")
    print("""
  Nodes NOT under test (always run once per turn — lightweight):
    normalize_query    → local viable: structural rewrite, fallback exists
    filter_sources     → local viable: scoring degrades gracefully

  Node under test — synthesize_evidence:""")

    for model, (checks, _) in results_by_model.items():
        v = verdict(checks)
        gate_ok = checks.get("S3 suggested_next_step is null  [HARD GATE]", False)
        json_ok = checks.get("S1 JSON valid (no fallback)", False)
        print(f"\n    {model}: {v}")
        if not json_ok:
            print("      → Structured JSON output unreliable.")
            print("        Synthesis role: cloud-only.")
        elif not gate_ok:
            print("      → Null-discipline failure: produces suggestions on factual queries.")
            print("        Full synthesis role: cloud-only.")
            print("        Evidence/confidence fields only: may be viable with prompt hardening.")
        else:
            passed = sum(1 for v_ in checks.values() if v_)
            total  = len(checks)
            if passed == total:
                print("      → All checks pass. Synthesis role: local viable.")
                print("        suggested_next_step field: local viable.")
            else:
                print("      → Partial pass. Evidence/confidence: local viable.")
                print("        suggested_next_step field: verify quality manually.")

    print("""
  Hybrid routing implication:
    If a model passes all 8 checks:
      → Full synthesis node can run locally (evidence + suggestion).
    If a model fails the null-discipline gate only:
      → Split: evidence/citations/confidence locally, suggested_next_step cloud-side.
        This is architecturally clean — fields are extracted independently.
    If a model fails JSON validity:
      → Not viable for any synthesis role. Keep cloud-only.
""")


def save_raw(output_dir: Path, model: str, scenario_id: str,
             raw_text: str, result: dict) -> None:
    safe_model = model.replace(":", "_").replace("/", "_")
    fname = output_dir / f"{safe_model}_{scenario_id}_raw.json"
    payload = {
        "model":            model,
        "scenario":         scenario_id,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "raw_response_text": raw_text,
        "fallback_used":    _fallback_used(result),
        "parsed_fields": {
            "evidence":           result.get("evidence", []),
            "citations":          result.get("citations", []),
            "confidence":         result.get("confidence"),
            "open_questions":     result.get("open_questions", []),
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
    output_dir = Path(f"/tmp/lobster_hybrid_{int(time.time())}")
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir : {output_dir}")

    # Preflight
    unavailable = []
    for m in models:
        if not check_ollama(m):
            unavailable.append(m)
    if unavailable:
        print(f"\nERROR: these models are not available in Ollama: {unavailable}")
        print("Run: ollama pull <model>  for each missing model.")
        return 1

    raw_store: dict = {}       # keyed by "{model}_{scenario_id}"
    results_by_model: dict = {}   # keyed by model → (checks, scenario_results)

    for model in models:
        hr(f"MODEL: {model}")
        scenario_results: dict = {}

        for sid, scenario in SCENARIOS.items():
            print(f"\n  {scenario['label']}")
            try:
                t0 = time.time()
                result = run_scenario(model, sid, scenario, raw_store)
                elapsed = time.time() - t0
                scenario_results[sid] = result

                sug = result.get("suggested_next_step")
                conf = result.get("confidence", 0.0)
                n_ev = len(result.get("evidence", []))
                fb   = "  [fallback]" if _fallback_used(result) else ""
                print(f"  → {n_ev} evidence  conf={conf:.2f}  "
                      f"suggestion={repr(sug)[:60]}  ({elapsed:.1f}s){fb}")

                raw_text = raw_store.get(f"{model}_{sid}", "(not captured)")
                save_raw(output_dir, model, sid, raw_text, result)

            except Exception as exc:
                print(f"  ERROR: {exc}")
                # Fill with empty result so checks can still run
                scenario_results[sid] = {
                    "evidence": [], "citations": [], "confidence": 0.0,
                    "open_questions": [], "suggested_next_step": None, "logs": [str(exc)],
                }
                raw_store[f"{model}_{sid}"] = f"ERROR: {exc}"
                save_raw(output_dir, model, sid, f"ERROR: {exc}",
                         scenario_results[sid])

        checks = evaluate_checks(
            scenario_results["S1"],
            scenario_results["S2"],
            scenario_results["S3"],
        )
        results_by_model[model] = (checks, scenario_results)
        print_matrix(model, checks)

    # Summary
    hr("VERDICT SUMMARY")
    for model, (checks, _) in results_by_model.items():
        v = verdict(checks)
        print(f"  {model:30s}  {v}")

    print_recommendation(results_by_model)

    hr()
    all_viable = all(
        "NOT VIABLE" not in verdict(ch) for ch, _ in results_by_model.values()
    )
    result_label = "COMPLETE" if results_by_model else "NO RESULTS"
    print(f"HYBRID VIABILITY TEST: {result_label}")
    hr()

    return 0


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--models", nargs="+",
        default=["mistral:7b-instruct", "llama3.1:8b"],
        help="Ollama model names to test (default: mistral:7b-instruct llama3.1:8b)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.models))
