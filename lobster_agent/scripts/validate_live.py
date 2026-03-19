"""Live validation script for Lobster Agent.

Run with an ANTHROPIC_API_KEY set:
    cd lobster_agent
    ANTHROPIC_API_KEY=sk-... python3 scripts/validate_live.py

Covers Tasks 1 and 2 from the Priority 2 validation checklist:
  - normalized_task field population
  - task_type classification accuracy
  - requested_outputs artifact-implication
  - review_result.verdict
  - hybrid workflow research + execution composition
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY is not set. Exiting.")
    sys.exit(1)

from app.graphs.main import create_main_graph, create_initial_state
from app.graphs.review.services.validator import task_expects_artifacts

DIVIDER = "─" * 64

DEMOS = [
    ("1 — File creation (execution)",
     "Create a file called hello.txt with the content 'Hello from Lobster Agent'"),
    ("2 — Safety attempt (execution)",
     "Write to ../../.env"),
    ("3 — Hybrid write",
     "Research how this project uses LangGraph and write a summary to docs/architecture-summary.md"),
    ("4 — Pure research",
     "Find out what testing frameworks this project uses"),
    ("5 — Ambiguous request",
     "Help me think through the design of this module"),
    ("6 — Script creation (execution)",
     "Make a Python script that prints Fibonacci numbers"),
]


def _attr(obj, field):
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


def check_prompt_quality(label, nt, task_type):
    """Task 2: flag potential prompt quality issues."""
    issues = []
    if task_type not in ("research", "execution", "hybrid"):
        issues.append(f"MISCLASSIFIED task_type: {task_type!r}")
    if not nt.get("objective") or nt["objective"] == nt.get("_raw_request"):
        issues.append("objective is a verbatim copy of the request (no extraction)")
    if not nt.get("requested_outputs") or nt["requested_outputs"] == ["response"]:
        issues.append("requested_outputs is generic ['response'] — check if file was expected")
    if len(nt.get("assumptions", [])) > 4:
        issues.append(f"suspiciously many assumptions ({len(nt['assumptions'])}): may be hallucinated")
    return issues


def run_demo(label, request):
    print(f"\n{DIVIDER}")
    print(f"  {label}")
    print(f"  Request: {request!r}")
    print(DIVIDER)

    graph = create_main_graph()
    result = graph.compile().invoke(create_initial_state(request))

    nt = result.get("normalized_task") or {}
    task_type = result.get("task_type")
    rr = result.get("review_result")
    arts = result.get("artifacts", [])

    # --- normalized_task ---
    print(f"\n  task_type          : {task_type}")
    print(f"  required_subgraphs : {result.get('required_subgraphs')}")
    print(f"\n  normalized_task:")
    print(f"    objective        : {nt.get('objective', '')[:100]}")
    print(f"    constraints      : {nt.get('constraints')}")
    print(f"    requested_outputs: {nt.get('requested_outputs')}")
    print(f"    assumptions      : {nt.get('assumptions')}")
    print(f"    priority         : {nt.get('priority')}")

    # --- LLM vs fallback ---
    trace = result.get("trace", [])
    nt_entry = next((t for t in trace if t.node_name == "normalize_task"), None)
    path = "FALLBACK" if nt_entry and "fallback" in nt_entry.output_summary.lower() else "LLM"
    print(f"\n  normalize path     : {path}")
    if nt_entry:
        print(f"  normalize trace    : {nt_entry.output_summary}")

    # --- artifact gate ---
    review_target = {"execution_result": result.get("execution_result"), "artifacts": arts}
    expects = task_expects_artifacts(review_target, nt.get("requested_outputs", []))
    print(f"  task_expects_artifacts(): {expects}")

    # --- review ---
    if rr:
        print(f"\n  review_result.verdict : {rr.get('verdict')}")
        print(f"  review_result.issues  : {rr.get('issues')}")
        print(f"  review_result.notes   : {rr.get('review_notes','')[:160]}")

    # --- artifacts ---
    print(f"\n  artifacts ({len(arts)}):")
    for a in arts:
        print(f"    {_attr(a,'type')}: {_attr(a,'name')} → {_attr(a,'path')}")

    # --- final response (first 300 chars) ---
    msgs = result.get("messages", [])
    asst = next((m for m in reversed(msgs) if m.get("role") == "assistant"), None)
    if asst:
        body = asst["content"]
        print(f"\n  final_response (preview):\n    {body[:300].replace(chr(10), chr(10)+'    ')}")

    # --- Task 2: prompt quality flags ---
    quality_issues = check_prompt_quality(label, nt, task_type)
    if quality_issues:
        print(f"\n  ⚠ PROMPT QUALITY FLAGS:")
        for qi in quality_issues:
            print(f"    - {qi}")
    else:
        print(f"\n  ✓ No prompt quality issues detected")

    return {
        "label": label,
        "task_type": task_type,
        "requested_outputs": nt.get("requested_outputs"),
        "expects_artifacts": expects,
        "verdict": rr.get("verdict") if rr else None,
        "n_artifacts": len(arts),
        "path": path,
        "quality_issues": quality_issues,
    }


def print_summary(results):
    print(f"\n{'='*64}")
    print("  VALIDATION SUMMARY")
    print(f"{'='*64}")
    print(f"  {'Demo':<35} {'Path':<8} {'Type':<10} {'Outputs':<22} {'Verdict':<10} {'Artifacts'}")
    print(f"  {'─'*35} {'─'*8} {'─'*10} {'─'*22} {'─'*10} {'─'*9}")
    for r in results:
        outputs = str(r["requested_outputs"])[:20]
        print(f"  {r['label'][:35]:<35} {r['path']:<8} {str(r['task_type']):<10} "
              f"{outputs:<22} {str(r['verdict']):<10} {r['n_artifacts']}")

    total_quality = sum(len(r["quality_issues"]) for r in results)
    file_requests = [r for r in results if "file" in r["label"].lower() or "script" in r["label"].lower() or "write" in r["label"].lower()]
    file_with_artifact_output = [r for r in file_requests
                                  if r["requested_outputs"] and
                                  any(t in str(r["requested_outputs"]).lower()
                                      for t in ("file", "artifact", "script", "code"))]

    print(f"\n  Prompt quality issues total : {total_quality}")
    print(f"  File requests with artifact-implying outputs: "
          f"{len(file_with_artifact_output)}/{len(file_requests)}")
    print(f"\n  Open validation item: real execution success + real safety failure")
    print(f"  (require API key + unsafe path plan from LLM to demonstrate live)")


if __name__ == "__main__":
    results = []
    for label, request in DEMOS:
        r = run_demo(label, request)
        results.append(r)
    print_summary(results)
