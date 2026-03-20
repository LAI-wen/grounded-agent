"""Cloud behavioral validation for response_length preference memory.

Requires ANTHROPIC_API_KEY. No TAVILY_API_KEY needed.

Three turns against the same project root and notes file:

  Turn 1 — baseline
    Query: "Summarise everything this project does and how it works."
    No preference in workspace_context. Claude Haiku produces a thorough,
    multi-topic response. Records W_before, N_before, confidence_before,
    and core_keywords (project-specific nouns extracted from evidence).

  Trigger turn
    Query: "That was too long. Please be more concise in future."
    Preference detector fires on "too long", stores response_length=concise.
    Verified: preference record written before Turn 2 proceeds.

  Turn 2 — with preference active
    Same query as Turn 1. workspace_context now contains:
      "User preferences (apply to this response):
         - Keep responses concise (set YYYY-MM-DD)"
    Records W_after, N_after, confidence_after.

Checks:
  C1. Preference record written after trigger turn        [gate — run invalid if fails]
  C2. "User preferences" present in Turn 2 context       [gate — run invalid if fails]
  C3. W_after < W_before * 0.85  (>= 15% word reduction) [primary behavioral gate]
  C4. N_after >= 2                (evidence not hollowed)
  C5. >= 2 core keywords preserved in Turn 2 evidence
  C6. |confidence_after - confidence_before| <= 0.20

Usage:
    ANTHROPIC_API_KEY=sk-... python3 scripts/validate_preference_cloud.py
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.main import run_lobster_agent
from app.memory.workspace import WorkspaceStore


# ---------------------------------------------------------------------------
# Notes file — rich enough that Claude Haiku produces >= 60 words unprompted
# ---------------------------------------------------------------------------

_NOTES = """\
Lobster Agent — Project Reference

Architecture
Lobster Agent is built on LangGraph and the Anthropic API. The parent graph
routes tasks to three subgraphs: research, execution, and review. The main
graph handles task classification, routing, and final response assembly.

Task Classification
The normalize_task node classifies each user request as research, execution,
or hybrid using Claude Haiku, with a keyword heuristic fallback when no API
key is set. Prior workspace history and conversation messages are included
in the classification prompt for context-aware routing.

Research Pipeline
Five nodes run in sequence: normalize_query extracts a structured query;
retrieve_sources fetches local files and optionally web results via Tavily;
filter_sources scores and selects the most relevant sources; synthesize_evidence
uses an LLM to extract structured evidence claims, citations, confidence score,
and open questions; finalize_research assembles the summary.

Execution Pipeline
The execution subgraph generates a file-operation plan using Claude Haiku, runs
a safety check that enforces project-root path boundaries, executes file_read
and file_write steps, and returns a list of artifacts with operation metadata.

Review Subgraph
After execution, the review subgraph produces a deterministic verdict: pass,
revise, fail, or blocked. The blocked verdict applies when execution was
safety-aborted before any file operations occurred.

Workspace Memory
The WorkspaceStore persists task summaries, artifact paths, project-state
records, and user preferences to an append-only JSONL file at
.lobster/workspace_memory.jsonl. read_context() injects the last five task
summaries, the most recent suggestion, and active user preferences into
every turn via workspace_context.

V3 Project-State Awareness
The synthesis node produces a suggested_next_step for progress and next-step
queries. The suggestion names a specific artifact and action verb, is grounded
in evidence, and is stored as a project_state record with last-1 retention.
Cross-session recall surfaces the stored suggestion in subsequent sessions.

V4 External Retrieval
The WebSearchAdapter calls the Tavily REST API with an 8-second hard timeout.
When TAVILY_API_KEY is set, search_sources() combines local file results
(up to 10) with web results (up to 5). All web failures are silent — local
results are returned if the Tavily call fails.

Hybrid Routing
The synthesize_evidence node routes factual queries with web sources to a
local qwen2.5:7b model via Ollama. Project-state and next-step queries always
use the cloud path. Routing skip reasons are logged for observability.

Preference Memory
The agent detects explicit user preferences using a keyword whitelist. The
response_length preference (concise or detailed) is injected into the synthesis
prompt via workspace_context with a set-date timestamp.
"""

# Keywords that are specific enough to distinguish real evidence from filler.
# At least 2 of these must appear in Turn 2 evidence to pass C5.
_CORE_KEYWORDS = {
    "langgraph", "subgraph", "research", "execution", "workspace",
    "synthesize", "synthesis", "retrieval", "normalize", "evidence",
    "filter", "classification", "review", "verdict", "memory",
    "preference", "tavily", "ollama", "routing",
}


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


def evidence_words(result: dict) -> int:
    rr = result.get("research_result") or {}
    ev = rr.get("evidence") or result.get("evidence") or []
    return sum(len(e.split()) for e in ev)


def evidence_items(result: dict) -> list:
    rr = result.get("research_result") or {}
    return rr.get("evidence") or result.get("evidence") or []


def evidence_confidence(result: dict) -> float:
    rr = result.get("research_result") or {}
    return float(rr.get("confidence") or result.get("confidence") or 0.0)


def keywords_in_evidence(ev_list: list, keywords: set) -> set:
    combined = " ".join(ev_list).lower()
    return {kw for kw in keywords if kw in combined}


def store_has_preference(store: WorkspaceStore) -> bool:
    try:
        records = store._load()
        return any(r.get("type") == "preference" for r in records)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("=" * 68)
        print("  validate_preference_cloud.py")
        print("=" * 68)
        print("\n  SKIP — ANTHROPIC_API_KEY not set.")
        print("  Run with: ANTHROPIC_API_KEY=sk-... python3 scripts/validate_preference_cloud.py")
        return 2   # distinct from test failure (1) and success (0)

    print("=" * 68)
    print("  Preference Cloud Behavioral Validation")
    print("  response_length preference — before/after on Claude Haiku")
    print("=" * 68)

    with tempfile.TemporaryDirectory(prefix="lobster_pref_cloud_") as tmp:
        notes_path = os.path.join(tmp, "notes.txt")
        with open(notes_path, "w") as f:
            f.write(_NOTES)

        os.chdir(tmp)
        store = WorkspaceStore(tmp)

        # ── Turn 1 — baseline ──────────────────────────────────────────
        hr("Turn 1 — baseline (no preference)")
        QUERY = "Summarise everything this project does and how it works."
        result1 = run_lobster_agent(QUERY)

        ev1       = evidence_items(result1)
        W_before  = evidence_words(result1)
        N_before  = len(ev1)
        C_before  = evidence_confidence(result1)
        kw_before = keywords_in_evidence(ev1, _CORE_KEYWORDS)

        print(f"\n  status:     {result1.get('status')}")
        print(f"  evidence:   {N_before} items  /  {W_before} words")
        print(f"  confidence: {C_before:.2f}")
        print(f"  keywords:   {sorted(kw_before)}")
        for i, e in enumerate(ev1, 1):
            print(f"  [{i}] {e[:100]}")

        # ── Trigger turn ───────────────────────────────────────────────
        hr("Trigger turn — store preference")
        result_t = run_lobster_agent(
            "That was too long. Please be more concise in future."
        )
        print(f"\n  status: {result_t.get('status')}")
        pref_stored = store_has_preference(store)
        print(f"  preference stored: {pref_stored}")

        if not pref_stored:
            print("\n  [ABORT] Preference not written — trigger turn did not succeed.")
            print("  C1 gate failed. Run is invalid; stopping here.")
            return 1

        # Confirm what was stored
        records = store._load()
        pref_rec = next((r for r in records if r.get("type") == "preference"), None)
        print(f"  stored record: {pref_rec}")

        # ── Turn 2 — with preference active ───────────────────────────
        hr("Turn 2 — same query, preference active")
        result2 = run_lobster_agent(QUERY)

        ev2      = evidence_items(result2)
        W_after  = evidence_words(result2)
        N_after  = len(ev2)
        C_after  = evidence_confidence(result2)
        kw_after = keywords_in_evidence(ev2, _CORE_KEYWORDS)

        # Extract the workspace_context that was used for Turn 2
        ctx2 = store.read_context()

        print(f"\n  status:     {result2.get('status')}")
        print(f"  evidence:   {N_after} items  /  {W_after} words")
        print(f"  confidence: {C_after:.2f}")
        print(f"  keywords:   {sorted(kw_after)}")
        for i, e in enumerate(ev2, 1):
            print(f"  [{i}] {e[:100]}")

        # ── Checks ────────────────────────────────────────────────────
        hr("CHECKS")

        reduction_pct  = 100 * (W_before - W_after) / max(W_before, 1)
        kw_preserved   = kw_before & kw_after
        conf_drop      = C_before - C_after   # positive means confidence fell

        passes = []

        passes.append(check(
            "C1. Preference record written after trigger turn  [gate]",
            pref_stored,
        ))
        passes.append(check(
            "C2. 'User preferences' present in Turn 2 workspace_context  [gate]",
            "User preferences" in ctx2,
            detail=f"context snippet: {ctx2[:120].strip()!r}",
        ))
        passes.append(check(
            f"C3. >= 15% word reduction  ({W_before} → {W_after}, {reduction_pct:+.0f}%)",
            W_after < W_before * 0.85,
        ))
        passes.append(check(
            f"C4. >= 2 evidence items remain in Turn 2  (N={N_after})",
            N_after >= 2,
        ))
        passes.append(check(
            f"C5. >= 2 core keywords preserved  ({sorted(kw_preserved)})",
            len(kw_preserved) >= 2,
        ))
        passes.append(check(
            f"C6. Confidence not hollowed (no drop > 0.20)  ({C_before:.2f} → {C_after:.2f}, drop={conf_drop:+.2f})",
            conf_drop <= 0.20,
        ))

        # ── Summary ───────────────────────────────────────────────────
        hr("SUMMARY")
        print(f"\n  W_before={W_before}  W_after={W_after}  "
              f"reduction={reduction_pct:+.0f}%")
        print(f"  N_before={N_before}  N_after={N_after}")
        print(f"  confidence_before={C_before:.2f}  confidence_after={C_after:.2f}  "
              f"Δ={C_after - C_before:+.2f}")
        print(f"  core keywords before: {sorted(kw_before)}")
        print(f"  core keywords after:  {sorted(kw_after)}")
        print(f"  keywords preserved:   {sorted(kw_preserved)}")

        hr()
        passed = sum(passes)
        total  = len(passes)
        gates_ok = passes[0] and passes[1]   # C1, C2

        if not gates_ok:
            print(f"\n  {passed}/{total} checks passed")
            print("  RESULT: INVALID — gate checks C1/C2 failed.")
            print("  The injection chain is broken; preference did not reach Turn 2.")
            return 1

        if passed == total:
            print(f"\n  {passed}/{total} checks passed")
            print("  RESULT: CONFIRMED — response_length preference produces")
            print("  measurable length reduction without hollowing evidence.")
        else:
            failed_names = [
                label for label, ok in zip(
                    ["C1", "C2", "C3", "C4", "C5", "C6"], passes
                ) if not ok
            ]
            print(f"\n  {passed}/{total} checks passed  (failed: {failed_names})")
            if "C3" in failed_names:
                print("  RESULT: INCONCLUSIVE — preference injected but Claude Haiku")
                print("  did not produce a measurable length reduction.")
                print("  Smallest follow-up: move the preference instruction from the")
                print("  workspace_context user block into the synthesis system prompt")
                print("  so it appears as a hard instruction rather than context.")
            else:
                print("  RESULT: PARTIAL — primary gate passed but secondary checks failed.")
                print(f"  Review failed checks: {failed_names}")

        return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
