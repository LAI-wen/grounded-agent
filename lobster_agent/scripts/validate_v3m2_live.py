"""V3 M2 live validation — project_state cross-session recall.

Usage:
    ANTHROPIC_API_KEY=sk-... python3 scripts/validate_v3m2_live.py

Sessions:
  Setup:    Create progress.txt so there is file content to research.
  Session 1: "What should I do next to make progress on this project?"
             → produces suggestion → project_state record written
  Session 2: "What did you recommend last time?"
             → workspace_context contains 'Most recent suggestion: ...'
             → response references the stored suggestion, not a re-synthesised one

V3 M2 properties under test:
  - project_state record present in store after Session 1
  - Session 2 workspace_context contains 'Most recent suggestion:'
  - Session 2 response references the stored suggestion text
  - Response does NOT re-synthesise a completely different recommendation
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
from app.memory.workspace import WorkspaceStore


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
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return ""


def run() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("Usage: ANTHROPIC_API_KEY=sk-... python3 scripts/validate_v3m2_live.py")
        return 1

    project_root = tempfile.mkdtemp(prefix="lobster_v3m2_")
    print(f"Project root : {project_root}")
    os.chdir(project_root)
    store = WorkspaceStore(project_root)

    # ── Setup — give the research session something to work with ─────
    hr("SETUP — Create progress.txt  (execution)")
    cp0 = create_checkpointer()
    r0 = run_lobster_agent(
        "Create progress.txt with the content: "
        "'V3 M1 complete. Next-step synthesis implemented and live-validated. "
        "V3 M2 in progress: project_state persistence.'",
        thread_id="v3m2-setup",
        checkpointer=cp0,
    )
    show("status",    r0["status"])
    show("task_type", r0["task_type"])
    progress_path = os.path.join(project_root, "progress.txt")
    if os.path.exists(progress_path):
        show("progress.txt", open(progress_path).read().strip())
    else:
        print("  progress.txt NOT written — setup failed")
        return 1

    # ── Session 1 — next-step query, should write project_state ─────
    hr("SESSION 1 — next-step query  (project_state should be written)")
    cp1 = create_checkpointer()
    r1 = run_lobster_agent(
        "What should I do next to make progress on this project?",
        thread_id="v3m2-s1",
        checkpointer=cp1,
    )
    show("status",    r1["status"])
    show("task_type", r1["task_type"])

    resp1 = last_message(r1)
    research1 = r1.get("research_result") or {}
    suggestion1 = research1.get("suggested_next_step")
    show("suggested_next_step", repr(suggestion1))
    show("response", resp1)

    # Inspect store directly — project_state record should now be present
    records = store._load()
    ps_records = [r for r in records if r.get("type") == "project_state"]
    print(f"\n  store: {len(ps_records)} project_state record(s)")
    for ps in ps_records:
        print(f"    task_id  : {ps.get('task_id', '')}")
        print(f"    timestamp: {ps.get('timestamp', '')[:19]}")
        print(f"    suggestion: {ps.get('suggested_next_step', '')}")

    # ── Session 2 — recall query, new thread, same project root ─────
    hr("SESSION 2 — 'What did you recommend last time?'  (recall expected)")
    cp2 = create_checkpointer()
    r2 = run_lobster_agent(
        "What did you recommend last time?",
        thread_id="v3m2-s2",
        checkpointer=cp2,
    )
    show("status",    r2["status"])
    show("task_type", r2["task_type"])

    resp2 = last_message(r2)
    research2 = r2.get("research_result") or {}
    ws_ctx2 = r2.get("workspace_context", "")

    show("workspace_context injected", ws_ctx2 if ws_ctx2 else "(empty)")
    show("response", resp2)

    evidence2 = research2.get("evidence", [])
    if evidence2:
        print("  evidence claims:")
        for item in evidence2[:6]:
            print(f"      - {str(item)[:120]}")

    suggestion2 = research2.get("suggested_next_step")
    show("suggested_next_step (Session 2)", repr(suggestion2))

    # ── Checks ───────────────────────────────────────────────────────
    hr("CHECKS")

    stored_suggestion = ps_records[-1].get("suggested_next_step", "") if ps_records else ""
    # Key words from the stored suggestion that should appear in Session 2's response
    suggestion_words = [
        w.strip(".,;:\"'()").lower()
        for w in stored_suggestion.split()
        if len(w) > 4
    ]
    resp2_lower = resp2.lower()
    ws_ctx2_lower = ws_ctx2.lower()
    evidence2_text = " ".join(str(e) for e in evidence2).lower()

    # Session 2 response or evidence references the stored suggestion
    suggestion_referenced = any(w in resp2_lower or w in evidence2_text
                                for w in suggestion_words[:5])

    checks = [
        # Setup
        ("Setup progress.txt written",
         os.path.exists(progress_path)),

        # Session 1: suggestion produced and persisted
        ("S1 status=success",
         r1["status"] == "success"),
        ("S1 suggested_next_step is present",
         bool(suggestion1)),
        ("S1 project_state record written to store",
         len(ps_records) == 1),
        ("S1 stored suggestion matches response suggestion",
         stored_suggestion == suggestion1),

        # Session 2: workspace context carries prior suggestion
        ("S2 status=success",
         r2["status"] == "success"),
        ("S2 workspace_context was injected",
         bool(ws_ctx2)),
        ("S2 workspace_context contains 'Most recent suggestion:'",
         "Most recent suggestion:" in ws_ctx2),
        ("S2 workspace_context contains stored suggestion text",
         bool(stored_suggestion) and stored_suggestion[:40].lower() in ws_ctx2_lower),
        ("S2 response references the stored suggestion",
         suggestion_referenced),
    ]

    all_pass = True
    for label, passed in checks:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {label}")
        if not passed:
            all_pass = False

    # ── Observable difference summary ────────────────────────────────
    hr("PRE-M2 vs POST-M2 OBSERVABLE DIFFERENCE")
    print("  Pre-M2:")
    print("    workspace_context has no project_state entry")
    print("    Session 2 would re-synthesise from file sources only")
    print("    'What did you recommend last time?' → no prior recommendation visible")
    print()
    print("  Post-M2 (this run):")
    ctx_line = next(
        (line for line in ws_ctx2.splitlines() if "Most recent suggestion:" in line), ""
    )
    print(f"    workspace_context carries: {ctx_line.strip()!r}")
    print(f"    Session 2 evidence can reference it directly")
    if suggestion_referenced:
        print(f"    Response references stored suggestion: YES")
    else:
        print(f"    Response references stored suggestion: NO (check above)")

    hr()
    verdict = "PASS — V3 M2 live validation complete" if all_pass else "FAIL — see above"
    print(f"V3 M2 VALIDATION: {verdict}")
    hr()

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(run())
