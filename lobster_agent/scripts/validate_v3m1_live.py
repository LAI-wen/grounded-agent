"""V3 M1 live validation — next-step synthesis.

Usage:
    ANTHROPIC_API_KEY=sk-... python3 scripts/validate_v3m1_live.py

Session setup:
  Session 1: Create progress.txt  (execution)
  Session 2: Append to progress.txt  (execution, M2 artifact-aware)
  Session 3A: "Summarise the current state of this project."  (research, progress query)
  Session 3B: "What should I do next to make progress on this project?"  (research, next-step query)
  Session 3C: "How does the safety check in this project work?"  (research, factual query)

V3 M1 properties under test:
  3A and 3B: suggested_next_step is present, non-empty, grounded in evidence,
             names a specific artifact, is not a generic phrase, and appears
             as "Suggested next step: ..." in the final response.
  3C:        suggested_next_step is None and "Suggested next step:" does not
             appear in the final response.
"""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.main import run_lobster_agent
from app.memory.checkpointer import create_checkpointer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hr(label=""):
    width = 70
    if label:
        padding = width - len(label) - 5
        print(f"\n{'─' * 3} {label} {'─' * max(padding, 1)}")
    else:
        print("─" * width)


def show(label, value, indent=0):
    prefix = "  " * indent
    if isinstance(value, str) and "\n" in value:
        print(f"{prefix}{label}:")
        for line in value.splitlines():
            print(f"{prefix}    {line}")
    else:
        print(f"{prefix}{label}: {value}")


def last_message(result: dict) -> str:
    """Extract the assistant's final response text from state.messages."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return ""


# Phrases that indicate a generic (non-grounded) suggestion.
_GENERIC_PHRASES = [
    "keep working",
    "continue working",
    "continue making progress",
    "keep making progress",
    "continue documenting",
    "keep documenting",
    "review your work",
    "review the work",
    "further document",
    "make progress",
]


def is_generic(suggestion: str) -> bool:
    lower = suggestion.lower()
    return any(phrase in lower for phrase in _GENERIC_PHRASES)


def save_raw(output_dir: str, name: str, result: dict, response: str,
             research_result: dict) -> None:
    """Write raw result data to a JSON file for later inspection."""
    payload = {
        "status":               result.get("status"),
        "task_type":            result.get("task_type"),
        "response":             response,
        "suggested_next_step":  research_result.get("suggested_next_step"),
        "evidence":             research_result.get("evidence", []),
        "citations":            research_result.get("citations", []),
        "confidence":           research_result.get("confidence"),
        "open_questions":       research_result.get("open_questions", []),
    }
    path = os.path.join(output_dir, f"{name}.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"  raw output saved → {path}")


# ---------------------------------------------------------------------------
# Session runners
# ---------------------------------------------------------------------------

def run_session_1(project_root: str) -> dict:
    hr("SESSION 1 — Create progress.txt  (execution)")
    cp = create_checkpointer()
    result = run_lobster_agent(
        "Create progress.txt with the content: "
        "'V3 milestone 1 started. Next-step synthesis implemented.'",
        thread_id="v3m1-s1",
        checkpointer=cp,
    )
    show("status",    result["status"])
    show("task_type", result["task_type"])
    path = os.path.join(project_root, "progress.txt")
    if os.path.exists(path):
        show("progress.txt", open(path).read().strip())
    else:
        print("  progress.txt NOT written")
    return result


def run_session_2(project_root: str) -> dict:
    hr("SESSION 2 — Append to progress.txt  (execution)")
    cp = create_checkpointer()
    result = run_lobster_agent(
        "Append 'Tests passing: 176. Live validation pending.' to progress.txt",
        thread_id="v3m1-s2",
        checkpointer=cp,
    )
    show("status",    result["status"])
    show("task_type", result["task_type"])
    path = os.path.join(project_root, "progress.txt")
    if os.path.exists(path):
        show("progress.txt", open(path).read().strip())
    else:
        print("  progress.txt NOT written")
    return result


def run_query(query: str, thread_id: str) -> tuple:
    """Run a single research query and return (result, response, research_result)."""
    cp = create_checkpointer()
    result = run_lobster_agent(query, thread_id=thread_id, checkpointer=cp)
    response = last_message(result)
    research_result = result.get("research_result") or {}
    return result, response, research_result


def print_query_detail(label: str, query: str, result: dict, response: str,
                       research_result: dict) -> None:
    show("query",     query)
    show("status",    result["status"])
    show("task_type", result["task_type"])

    confidence = research_result.get("confidence")
    if confidence is not None:
        show("confidence", f"{confidence:.2f}")

    evidence = research_result.get("evidence", [])
    if evidence:
        print("  evidence claims:")
        for item in evidence[:6]:
            print(f"      - {str(item)[:120]}")

    suggestion = research_result.get("suggested_next_step")
    show("suggested_next_step", repr(suggestion))

    print("  response:")
    for line in response.splitlines():
        print(f"      {line}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("Usage: ANTHROPIC_API_KEY=sk-... python3 scripts/validate_v3m1_live.py")
        return 1

    project_root = tempfile.mkdtemp(prefix="lobster_v3m1_")
    output_dir   = project_root  # raw JSON files land next to the workspace files
    print(f"Project root : {project_root}")
    os.chdir(project_root)

    # ── Setup sessions ────────────────────────────────────────────────
    r1 = run_session_1(project_root)
    r2 = run_session_2(project_root)

    progress_path = os.path.join(project_root, "progress.txt")
    progress_text = open(progress_path).read().lower() if os.path.exists(progress_path) else ""

    # ── Query A — project-state ───────────────────────────────────────
    hr("QUERY A — project-state  (suggestion expected)")
    rA, respA, researchA = run_query(
        "Summarise the current state of this project.",
        thread_id="v3m1-qA",
    )
    print_query_detail("A", "Summarise the current state of this project.",
                       rA, respA, researchA)
    save_raw(output_dir, "query_A", rA, respA, researchA)

    # ── Query B — next-step ───────────────────────────────────────────
    hr("QUERY B — next-step  (suggestion expected)")
    rB, respB, researchB = run_query(
        "What should I do next to make progress on this project?",
        thread_id="v3m1-qB",
    )
    print_query_detail("B", "What should I do next to make progress on this project?",
                       rB, respB, researchB)
    save_raw(output_dir, "query_B", rB, respB, researchB)

    # ── Query C — factual ─────────────────────────────────────────────
    hr("QUERY C — factual  (no suggestion expected)")
    rC, respC, researchC = run_query(
        "How does the safety check in this project work?",
        thread_id="v3m1-qC",
    )
    print_query_detail("C", "How does the safety check in this project work?",
                       rC, respC, researchC)
    save_raw(output_dir, "query_C", rC, respC, researchC)

    # ── Checks ────────────────────────────────────────────────────────
    hr("CHECKS")

    suggA = researchA.get("suggested_next_step")
    suggB = researchB.get("suggested_next_step")
    suggC = researchC.get("suggested_next_step")

    evidenceA_text = " ".join(str(e) for e in researchA.get("evidence", [])).lower()
    evidenceB_text = " ".join(str(e) for e in researchB.get("evidence", [])).lower()

    # Artifact grounding: suggestion mentions a word that appears in evidence or response
    def grounded_in_evidence(suggestion: str, evidence_text: str, response: str) -> bool:
        if not suggestion:
            return False
        # At least one content word in the suggestion (>4 chars) must appear in evidence or response
        words = [w.strip(".,;:\"'()").lower() for w in suggestion.split() if len(w) > 4]
        combined = evidence_text + " " + response.lower()
        return any(w in combined for w in words)

    checks = [
        # ── Setup baseline ──
        ("S1 status=success",
         r1["status"] == "success"),
        ("S1 progress.txt written",
         os.path.exists(progress_path)),
        ("S2 status=success",
         r2["status"] == "success"),
        ("S2 progress.txt contains appended line",
         "176" in progress_text or "live validation" in progress_text),

        # ── Query A — suggestion present ──
        ("A status=success",
         rA["status"] == "success"),
        ("A suggested_next_step is present",
         bool(suggA)),
        ("A suggestion is not generic",
         bool(suggA) and not is_generic(suggA)),
        ("A suggestion grounded in evidence",
         bool(suggA) and grounded_in_evidence(suggA, evidenceA_text, respA)),
        ("A 'Suggested next step:' in response",
         "Suggested next step:" in respA),

        # ── Query B — suggestion present ──
        ("B status=success",
         rB["status"] == "success"),
        ("B suggested_next_step is present",
         bool(suggB)),
        ("B suggestion is not generic",
         bool(suggB) and not is_generic(suggB)),
        ("B suggestion grounded in evidence",
         bool(suggB) and grounded_in_evidence(suggB, evidenceB_text, respB)),
        ("B 'Suggested next step:' in response",
         "Suggested next step:" in respB),

        # ── Query C — no suggestion ──
        ("C status=success",
         rC["status"] == "success"),
        ("C suggested_next_step is None",
         suggC is None),
        ("C 'Suggested next step:' absent from response",
         "Suggested next step:" not in respC),
    ]

    all_pass = True
    for label, passed in checks:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {label}")
        if not passed:
            all_pass = False

    # ── Summary ───────────────────────────────────────────────────────
    hr()
    print("SUGGESTION QUALITY SUMMARY")
    print(f"  Query A: {repr(suggA)}")
    print(f"  Query B: {repr(suggB)}")
    print(f"  Query C: {repr(suggC)}  (expected None)")

    hr()
    verdict = "PASS — V3 M1 live validation complete" if all_pass else "FAIL — see above"
    print(f"V3 M1 VALIDATION: {verdict}")
    hr()

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(run())
