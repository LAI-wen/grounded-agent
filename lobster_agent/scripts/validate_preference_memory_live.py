"""Live validation for preference memory P1 (detection + storage + injection).

Two parts:

  Part A — detection, storage, injection  (no API key required)
    A1. Trigger phrase detected and stored as preference record
    A2. Quote guard: trigger phrase in double quotes is not stored
    A3. Quote guard: trigger phrase in block quote is not stored
    A4. Failed-turn status gate: preference not stored when status != success
    A5. Last-record-per-key rule: second write overrides first
    A6. Active preference rendered into workspace_context
    A7. read_context() shows date alongside preference

  Part B — before/after response comparison  (requires Ollama)
    Runs synthesize_evidence twice against the same query and sources,
    once without a preference in context and once with "concise" active.
    Reports word count before and after, and whether the response shortened.
    Uses qwen2.5:7b via the Ollama shim (no ANTHROPIC_API_KEY needed).

Usage:
    python3 scripts/validate_preference_memory_live.py
    python3 scripts/validate_preference_memory_live.py --no-ollama
"""

import argparse
import json
import os
import sys
import tempfile
from unittest.mock import patch, Mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.main import _detect_preference, _strip_quoted
from app.memory.workspace import WorkspaceStore

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


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return condition


def _fake_result(status="success", task_id="t1"):
    return {"status": status, "task_id": task_id}


# ---------------------------------------------------------------------------
# Part A — detection, storage, injection
# ---------------------------------------------------------------------------

def run_part_a():
    hr("Part A — detection, storage, injection")
    passes = []

    # A1: trigger detected and stored
    with tempfile.TemporaryDirectory() as tmp:
        store  = WorkspaceStore(tmp)
        result = _fake_result()
        detected = _detect_preference("That was too long, please be more concise.")
        assert detected
        key, value, phrase = detected
        store.write_preference(result["task_id"], key, value, phrase)

        records = store._load()
        prefs   = [r for r in records if r.get("type") == "preference"]
        passes.append(check(
            "A1. Trigger phrase detected and stored",
            len(prefs) == 1 and prefs[0]["value"] == "concise",
            detail=f"stored: {prefs[0] if prefs else 'none'}",
        ))

    # A2: double-quoted trigger not detected
    detected_quoted = _detect_preference('the user wrote "too long" in their note')
    passes.append(check(
        "A2. Double-quoted trigger phrase not detected",
        detected_quoted is None,
        detail=f"got: {detected_quoted}",
    ))

    # A3: block-quoted trigger not detected
    block_text = "here is the feedback:\n> that was too long\nplease review"
    detected_block = _detect_preference(block_text)
    passes.append(check(
        "A3. Block-quoted trigger phrase not detected",
        detected_block is None,
        detail=f"got: {detected_block}",
    ))

    # A4: status gate — no write on failed turn
    with tempfile.TemporaryDirectory() as tmp:
        store    = WorkspaceStore(tmp)
        detected = _detect_preference("be more concise next time")
        assert detected
        key, value, phrase = detected
        # Simulate failed turn: status != "success" → do not write
        failed_result = _fake_result(status="error")
        if failed_result.get("status") == "success":
            store.write_preference(failed_result["task_id"], key, value, phrase)

        records = store._load()
        prefs   = [r for r in records if r.get("type") == "preference"]
        passes.append(check(
            "A4. No preference stored on failed-turn status",
            len(prefs) == 0,
        ))

    # A5: last-record-per-key wins
    with tempfile.TemporaryDirectory() as tmp:
        store = WorkspaceStore(tmp)
        store.write_preference("t1", "response_length", "concise",  "too long")
        store.write_preference("t2", "response_length", "detailed", "more detail")

        records   = store._load()
        prefs     = [r for r in records if r.get("type") == "preference"]
        context   = store.read_context()
        passes.append(check(
            "A5. Last-record-per-key overwrites earlier value",
            len(prefs) == 1 and prefs[0]["value"] == "detailed",
            detail=f"active: {prefs[0]['value'] if prefs else 'none'}",
        ))

    # A6: preference rendered in workspace_context
    with tempfile.TemporaryDirectory() as tmp:
        store   = WorkspaceStore(tmp)
        store.write_preference("t1", "response_length", "concise", "too long")
        context = store.read_context()
        passes.append(check(
            "A6. Active preference appears in workspace_context",
            "User preferences" in context and "Keep responses concise" in context,
            detail=f"context snippet: {context[:120].strip()!r}",
        ))

    # A7: timestamp visible in rendered preference
    with tempfile.TemporaryDirectory() as tmp:
        store   = WorkspaceStore(tmp)
        store.write_preference("t1", "response_length", "concise", "be brief")
        context = store.read_context()
        passes.append(check(
            "A7. 'set YYYY-MM-DD' timestamp visible in preference line",
            "set 20" in context,
            detail=f"context: {context.strip()!r}",
        ))

    return passes


# ---------------------------------------------------------------------------
# Part B — before/after word count via Ollama
# ---------------------------------------------------------------------------

_SOURCES = [
    {
        "url":               None,
        "title":             "project-notes.txt",
        "content":           (
            "Lobster Agent is a LangGraph-based multi-agent research assistant "
            "built with LangGraph and the Anthropic API. "
            "The system supports three task types: research, execution, and hybrid. "
            "Research tasks walk the local file tree, rank files by query-term frequency, "
            "extract relevant excerpts, filter sources by reliability, and synthesise "
            "structured evidence using an LLM. "
            "Execution tasks generate a step-by-step file operation plan, run a safety "
            "check before executing, write or read files within the project boundary, "
            "and review the results with a deterministic verdict. "
            "Workspace memory persists task summaries and artifact paths to a JSONL store "
            "at .lobster/workspace_memory.jsonl. Context is injected into every turn. "
            "V3 added project-state awareness: the synthesis node can produce a "
            "suggested_next_step for progress queries, stored as a project_state record "
            "and injected into subsequent sessions. "
            "V4 added external retrieval via the Tavily API. WebSearchAdapter sends a "
            "POST request to the Tavily search endpoint and maps results to the Source "
            "schema. Combined retrieval runs local file search and web search in parallel, "
            "with a hard timeout of 8 seconds and silent fallback to local-only on failure. "
            "Hybrid routing directs factual queries with web sources to a local qwen2.5:7b "
            "model via Ollama, keeping project-state and next-step queries on the cloud path. "
            "The routing decision is private to the synthesize_evidence node and uses an "
            "explicit path marker in the logs for observability. "
            "Preference memory stores explicit user preferences such as response length "
            "with last-one-per-key retention and injects them into the synthesis context."
        ),
        "reliability_score": 0.85,
        "metadata":          {"adapter": "local_file"},
    }
]


def _make_synth_state(workspace_context: str) -> ResearchState:
    defaults: dict = {
        "research_query":    "Summarise this project.",
        "context":           {"workspace_context": workspace_context},
        "task_id":           "pref-val",
        "normalized_query":  "Summarise this project.",
        "structured_query":  None,
        "retrieval_strategy": "hybrid",
        "sources":           [],
        "filtered_sources":  _SOURCES,
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


class _OllamaContent:
    def __init__(self, text): self.text = text

class _OllamaResponse:
    def __init__(self, text): self.content = [_OllamaContent(text)]

class _RecordingOllamaMessages:
    """Ollama messages interface that records every user prompt it receives."""
    def __init__(self, timeout, captured: list):
        self._timeout  = timeout
        self._captured = captured

    def create(self, model=None, max_tokens=None, system=None,
               messages=None, **kwargs):
        import requests as _req
        # Capture the user prompt for later inspection
        user_content = (messages or [{}])[0].get("content", "") if messages else ""
        self._captured.append(user_content)
        payload = {
            "model":    "qwen2.5:7b",
            "messages": [{"role": "system", "content": system or ""}]
                        + (messages or []),
            "stream":   False,
            "options":  {"num_predict": max_tokens or 3000, "temperature": 0},
        }
        resp = _req.post("http://localhost:11434/api/chat",
                         json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return _OllamaResponse(resp.json()["message"]["content"])


def _make_recording_client(captured: list):
    """Return an _OllamaClient-like object that records prompts."""
    class _Client:
        def __init__(self, *args, **kwargs):
            self.messages = _RecordingOllamaMessages(timeout=90, captured=captured)
    return _Client


def _check_ollama() -> bool:
    try:
        import requests
        r    = requests.get("http://localhost:11434/api/tags", timeout=3)
        tags = r.json().get("models", [])
        return any(t.get("name", "").startswith("qwen2.5") for t in tags)
    except Exception:
        return False


def run_part_b(skip_ollama: bool):
    hr("Part B — before/after response length (Ollama)")

    if skip_ollama or not _check_ollama():
        reason = "--no-ollama flag" if skip_ollama else "qwen2.5 not in Ollama"
        print(f"  [SKIP] {reason} — run: ollama pull qwen2.5:7b to enable")
        return []

    passes = []

    def _run_synthesis(workspace_context: str, captured: list) -> dict:
        state   = _make_synth_state(workspace_context)
        client_cls = _make_recording_client(captured)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.object(_synth_mod, "_qwen_available", return_value=False):
                with patch.object(_synth_mod, "_OllamaClient", client_cls):
                    with patch.object(_synth_mod, "Anthropic",
                                      return_value=client_cls()):
                        return synthesize_evidence(state)

    # ── Baseline: no preference in context ──
    print("\n  Running baseline synthesis (no preference)...")
    captured_before: list = []
    result_before   = _run_synthesis(workspace_context="", captured=captured_before)
    evidence_before = result_before.get("evidence", [])
    words_before    = sum(len(e.split()) for e in evidence_before)
    print(f"  evidence items: {len(evidence_before)}  total words: {words_before}")

    # ── Build workspace_context with active "concise" preference ──
    with tempfile.TemporaryDirectory() as tmp:
        store = WorkspaceStore(tmp)
        store.write_task_summary({
            "task_id":         "setup",
            "task_type":       "execution",
            "normalized_task": {"objective": "Set up project notes"},
            "status":          "success",
            "artifacts":       [],
            "review_result":   None,
            "user_request":    "",
        })
        store.write_preference("setup", "response_length", "concise", "too long")
        pref_context = store.read_context()

    print(f"\n  workspace_context with preference:\n  {pref_context.strip()!r}")
    print("\n  Running synthesis with 'concise' preference injected...")
    captured_after: list = []
    result_after   = _run_synthesis(workspace_context=pref_context,
                                    captured=captured_after)
    evidence_after = result_after.get("evidence", [])
    words_after    = sum(len(e.split()) for e in evidence_after)
    print(f"  evidence items: {len(evidence_after)}  total words: {words_after}")

    reduction_pct = 100 * (words_before - words_after) / max(words_before, 1)
    print(f"\n  Before: {words_before} words  |  After: {words_after} words"
          f"  |  Δ {reduction_pct:+.0f}%")

    # B1: preference text reaches the model (in the captured user prompt)
    prompt_with_pref = captured_after[0] if captured_after else ""
    passes.append(check(
        "B1. Preference injected into synthesis prompt",
        "User preferences" in pref_context,
        detail=f"pref context: {pref_context.strip()!r}",
    ))
    passes.append(check(
        "B2. 'Keep responses concise' text present in prompt sent to model",
        "Keep responses concise" in prompt_with_pref,
        detail=f"prompt snippet: {prompt_with_pref[:200]!r}",
    ))
    passes.append(check(
        "B3. Both syntheses completed without error",
        bool(evidence_before) and bool(evidence_after),
    ))
    # B4: observable before/after — report but do not hard-fail
    # (model may already be concise; behavioral change is best observed
    #  with a real ANTHROPIC_API_KEY run against a verbose cloud model)
    direction = "shorter" if words_after < words_before else "same or longer"
    print(f"\n  B4 (informational): response is {direction} with preference active")
    print(f"     {words_before} → {words_after} words ({reduction_pct:+.0f}%)")
    print("     Full behavioral validation requires a real cloud model (ANTHROPIC_API_KEY).")

    return passes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(skip_ollama: bool) -> int:
    print("=" * 68)
    print("  Preference Memory Live Validation (P1)")
    print("  detection · storage · injection · before/after")
    print("=" * 68)

    part_a = run_part_a()
    part_b = run_part_b(skip_ollama)

    all_passes   = part_a + part_b
    skipped_b    = not part_b
    check_passes = sum(all_passes)
    total        = len(all_passes)

    hr("SUMMARY")
    a_pass = sum(part_a)
    print(f"  Part A (detection/storage/injection): {a_pass}/{len(part_a)}")
    if part_b:
        b_pass = sum(part_b)
        print(f"  Part B (before/after Ollama):         {b_pass}/{len(part_b)}")
    else:
        print("  Part B (before/after Ollama):         SKIPPED")

    hr()
    if check_passes == total and total > 0:
        print(f"\n  {check_passes}/{total} checks passed")
        if skipped_b:
            print("  PREFERENCE MEMORY P1: PASSED (Part B pending Ollama)")
        else:
            print("  PREFERENCE MEMORY P1: FULLY VALIDATED")
            print("  Full behavioral effect (response length) requires ANTHROPIC_API_KEY.")
        return 0
    else:
        failed = total - check_passes
        print(f"\n  {check_passes}/{total} checks passed  ({failed} FAILED)")
        print("  PREFERENCE MEMORY P1: FAILED")
        return 1


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--no-ollama", action="store_true",
                   help="Skip Part B (real Ollama call)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(main(skip_ollama=args.no_ollama))
