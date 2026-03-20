"""M3 live validation — workspace-aware research continuation.

Usage:
    ANTHROPIC_API_KEY=sk-... python3 scripts/validate_m3_live.py

Three sessions against a fresh temporary project root:
  Session 1: Create notes.txt (execution)
  Session 2: Create summary.txt summarising the project (hybrid/execution)
  Session 3: Summarise what this project has done so far (research)

M3 property under test:
  Session 3 research synthesis draws from BOTH the file sources found by
  the file walk (notes.txt, summary.txt) AND the workspace history entries
  written by Sessions 1 and 2.  The response should mention content from
  the files and reference prior session activity in the same answer.
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from app.main import run_lobster_agent
from app.memory.checkpointer import create_checkpointer


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


def run():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("Usage: ANTHROPIC_API_KEY=sk-... python3 scripts/validate_m3_live.py")
        return 1

    project_root = tempfile.mkdtemp(prefix="lobster_m3_")
    print(f"Project root : {project_root}")
    os.chdir(project_root)

    # ── Session 1 ────────────────────────────────────────────────────
    hr("SESSION 1 — Create notes.txt  (execution)")
    cp1 = create_checkpointer()
    r1 = run_lobster_agent(
        "Create notes.txt with the content: 'Project started. Initial notes added on day one.'",
        thread_id="m3-s1",
        checkpointer=cp1,
    )
    show("status",    r1["status"])
    show("task_type", r1["task_type"])

    notes_path = os.path.join(project_root, "notes.txt")
    if os.path.exists(notes_path):
        show("notes.txt", open(notes_path).read().strip())
    else:
        print("notes.txt NOT written")

    # ── Session 2 ────────────────────────────────────────────────────
    hr("SESSION 2 — Create summary.txt  (execution or hybrid)")
    cp2 = create_checkpointer()
    r2 = run_lobster_agent(
        "Create summary.txt with a summary of what this project has done so far.",
        thread_id="m3-s2",
        checkpointer=cp2,
    )
    show("status",    r2["status"])
    show("task_type", r2["task_type"])

    summary_path = os.path.join(project_root, "summary.txt")
    if os.path.exists(summary_path):
        print("summary.txt:")
        for line in open(summary_path).read().strip().splitlines():
            print(f"    {line}")
    else:
        print("summary.txt NOT written")

    # ── Session 3 ────────────────────────────────────────────────────
    hr("SESSION 3 — Research query  (both sources expected)")
    cp3 = create_checkpointer()
    r3 = run_lobster_agent(
        "Summarise what this project has done so far.",
        thread_id="m3-s3",
        checkpointer=cp3,
    )
    show("status",    r3["status"])
    show("task_type", r3["task_type"])

    response3 = last_message(r3)
    show("response", response3[:900] if response3 else "(empty)")

    research3 = r3.get("research_result") or {}
    confidence3 = research3.get("confidence")
    if confidence3 is not None:
        show("confidence", f"{confidence3:.2f}")

    evidence3 = research3.get("evidence", [])
    if evidence3:
        print("evidence items:")
        for item in evidence3[:6]:
            print(f"    - {str(item)[:120]}")

    # Workspace context that was injected
    ws_ctx = r3.get("workspace_context", "")
    if ws_ctx:
        print("workspace_context injected:")
        for line in ws_ctx.strip().splitlines():
            print(f"    {line}")

    # ── Checks ───────────────────────────────────────────────────────
    hr("CHECKS")

    s2_file_text = open(summary_path).read().lower() if os.path.exists(summary_path) else ""
    r3_lower = response3.lower()
    evidence_text = " ".join(str(e) for e in evidence3).lower()
    combined_text = r3_lower + " " + evidence_text

    checks = [
        # Session 1 baseline
        ("S1 status=success",
         r1["status"] == "success"),
        ("S1 notes.txt written",
         os.path.exists(notes_path)),

        # Session 2 baseline
        ("S2 status=success",
         r2["status"] == "success"),
        ("S2 summary.txt written",
         os.path.exists(summary_path)),
        ("S2 output references prior work",
         any(kw in s2_file_text for kw in
             ["notes", "day one", "initial", "project started", "created"])),

        # Session 3 — M3 core property
        ("S3 status=success",
         r3["status"] == "success"),
        ("S3 task_type=research",
         r3.get("task_type") == "research"),
        ("S3 response is non-empty",
         bool(response3)),
        ("S3 workspace_context was injected",
         bool(ws_ctx)),
        ("S3 response mentions notes.txt",
         "notes" in combined_text),
        ("S3 response mentions summary.txt",
         "summary" in combined_text),
        ("S3 response references file content or workspace history",
         any(kw in combined_text for kw in
             ["day one", "initial", "created", "project started",
              "session", "task", "artifact", "workspace"])),
    ]

    all_pass = True
    for label, passed in checks:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {label}")
        if not passed:
            all_pass = False

    hr()
    result = "PASS — M3 validation complete" if all_pass else "FAIL — see above"
    print(f"M3 VALIDATION: {result}")
    hr()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(run())
