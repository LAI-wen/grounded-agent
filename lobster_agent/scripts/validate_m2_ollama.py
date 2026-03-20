"""V2 M2 cross-model robustness validation using Ollama.

Tests M2 continuation behavior (read-before-write, content preservation,
replace semantics) against local Ollama models as a robustness check
against over-tuning to Claude.

Usage:
    cd lobster_agent
    python3 scripts/validate_m2_ollama.py
    python3 scripts/validate_m2_ollama.py --models llama3.2:3b
    python3 scripts/validate_m2_ollama.py --scenarios A C

Requirements:
    ollama serve                        (Ollama running locally)
    ollama pull llama3.2:3b             (primary model)
    ollama pull mistral:7b-instruct     (secondary model)
    pip install openai                  (OpenAI-compatible client)

Scenarios:
    A — Append  (explicit): "Append '...' to notes.txt"
    B — Extend  (implicit): "Add a section to notes.txt"   ← stress case
    C — Replace (explicit): "Rewrite notes.txt from scratch"

Checks per scenario × model:
    Plan  — file_read before file_write; write action uses preservation keyword
    E2E   — final file contains original + new content (A/B) or only new (C)

Failure diagnosis: four log buckets saved per run to scripts/logs/
    [1] raw plan LLM response       → prompt fragility vs model weakness
    [2] parsed plan steps           → planning failure vs downstream failure
    [3] raw generate LLM response   → generate mode confusion
    [4] execute combine decisions   → implementation bug vs model issue
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODELS = ["llama3.2:3b", "mistral:7b-instruct"]
OLLAMA_BASE_URL = "http://localhost:11434/v1"

SCENARIOS = [
    {
        "id": "A",
        "label": "Append (explicit)",
        "task": "Append the line 'Second entry.' to notes.txt",
        "expected_mode": "append",
    },
    {
        "id": "B",
        "label": "Extend (implicit)",
        "task": "Add a summary section to notes.txt",
        "expected_mode": "append",   # stress case — implicit preservation intent
    },
    {
        "id": "C",
        "label": "Replace (explicit)",
        "task": "Rewrite notes.txt from scratch with completely new content",
        "expected_mode": "replace",
    },
]

ORIGINAL_CONTENT = "Original entry.\n"

# Matches WorkspaceStore.read_context() output format
WORKSPACE_CONTEXT = (
    "\nWorkspace history (recent tasks):\n"
    '- [2026-03-20] execution: "Create notes.txt with initial content"'
    " → success, artifacts: [notes.txt]\n"
)

# Must match execute.py _APPEND_KEYWORDS exactly
_APPEND_KEYWORDS = frozenset({
    "append", "preserve existing", "preserve prior", "add to existing",
    "based on file_read", "base on file_read", "add new",
})

W = 70
COL = 12  # column width per model×level cell


# ---------------------------------------------------------------------------
# Ollama adapter
# ---------------------------------------------------------------------------

def _make_adapter_class(model_name: str, call_log: list):
    """Return an Anthropic-compatible class that routes calls to Ollama."""

    class _Content:
        def __init__(self, text): self.text = text

    class _Response:
        def __init__(self, text): self.content = [_Content(text)]

    class OllamaAdapter:
        def __init__(self, api_key=None):
            from openai import OpenAI
            self._client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
            self.messages = self  # client.messages.create(...)

        def create(self, *, model=None, max_tokens=None, system=None,
                   messages=None, **_):
            full_msgs = (
                [{"role": "system", "content": system}] if system else []
            ) + (messages or [])
            try:
                resp = self._client.chat.completions.create(
                    model=model_name,
                    messages=full_msgs,
                    max_tokens=max_tokens or 1500,
                )
                text = resp.choices[0].message.content or ""
            except Exception as exc:
                text = f"__OLLAMA_ERROR__: {exc}"
            call_log.append({
                "model": model_name,
                "prompt_messages": full_msgs,
                "response_text": text,
            })
            return _Response(text)

    return OllamaAdapter


# ---------------------------------------------------------------------------
# Workspace seeding
# ---------------------------------------------------------------------------

def _seed_workspace(tmp_dir: Path) -> None:
    """Create notes.txt and a pre-populated .lobster store."""
    (tmp_dir / "notes.txt").write_text(ORIGINAL_CONTENT)
    store_dir = tmp_dir / ".lobster"
    store_dir.mkdir()
    records = [
        {
            "type": "task_summary",
            "task_id": "seed-1",
            "timestamp": "2026-03-20T00:00:00+00:00",
            "task_type": "execution",
            "objective": "Create notes.txt with initial content",
            "status": "success",
            "artifacts": ["notes.txt"],
            "verdict": "pass",
        },
        {
            "type": "artifact",
            "timestamp": "2026-03-20T00:00:00+00:00",
            "path": "notes.txt",
            "operation": "write",
            "task_id": "seed-1",
        },
    ]
    (store_dir / "workspace_memory.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )


# ---------------------------------------------------------------------------
# Planning-level check  (plan node only)
# ---------------------------------------------------------------------------

def run_plan_check(scenario: dict, model_name: str, tmp_dir: Path,
                   call_log: list) -> tuple:
    """
    Invoke create_execution_plan with the Ollama adapter.
    Return (passed: bool, parsed_steps: list, notes: list[str]).
    """
    from app.graphs.execution.graph import create_execution_input
    from app.graphs.execution.nodes.plan import create_execution_plan

    state = create_execution_input(
        task_description=scenario["task"],
        context={
            "project_root": str(tmp_dir),
            "workspace_context": WORKSPACE_CONTEXT,
        },
        task_id=f"plan-{scenario['id']}",
    )

    adapter_cls = _make_adapter_class(model_name, call_log)
    with (
        patch("app.graphs.execution.nodes.plan.Anthropic", adapter_cls),
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "ollama-test"}),
    ):
        result = create_execution_plan(state)

    steps = result["execution_plan"]
    parsed = [
        {"step_id": s.get("step_id"), "tool": s.get("tool"),
         "action": s.get("action", "")}
        for s in steps
    ]
    notes = []
    passed = _evaluate_plan(scenario["expected_mode"], parsed, notes)
    return passed, parsed, notes


def _evaluate_plan(expected_mode: str, steps: list, notes: list) -> bool:
    tools = [s["tool"] for s in steps]
    write_actions = [
        s["action"].lower() for s in steps if s["tool"] == "file_write"
    ]

    if expected_mode == "append":
        has_read = "file_read" in tools
        has_write = "file_write" in tools
        read_before_write = False
        if has_read and has_write:
            ri = next(i for i, s in enumerate(steps) if s["tool"] == "file_read")
            wi = next(i for i, s in enumerate(steps) if s["tool"] == "file_write")
            read_before_write = ri < wi

        matched_kws = [
            kw for kw in _APPEND_KEYWORDS
            if any(kw in a for a in write_actions)
        ]
        has_preserve_kw = bool(matched_kws)

        if not has_read:
            notes.append("FAIL: no file_read step in plan")
        elif not read_before_write:
            notes.append("FAIL: file_read does not precede file_write")
        else:
            notes.append("PASS: file_read precedes file_write")

        if has_preserve_kw:
            notes.append(f"PASS: preservation keyword(s) in write action: {matched_kws}")
        else:
            notes.append(
                f"FAIL: write action lacks preservation keyword "
                f"(actions: {write_actions})"
            )

        return has_read and read_before_write and has_preserve_kw

    else:  # replace
        false_positives = [
            kw for kw in _APPEND_KEYWORDS
            if any(kw in a for a in write_actions)
        ]
        if false_positives:
            notes.append(
                f"FAIL: replace scenario has preservation keyword in write action "
                f"— would trigger unwanted combine: {false_positives}"
            )
            return False
        notes.append("PASS: write action has no preservation keyword (replace semantics intact)")
        return True


# ---------------------------------------------------------------------------
# End-to-end check  (full execution graph)
# ---------------------------------------------------------------------------

def run_e2e_check(scenario: dict, model_name: str, tmp_dir: Path,
                  call_log: list) -> tuple:
    """
    Run the full plan → generate → simulate → safety_check → execute pipeline.
    Return (passed: bool, final_content: str|None, notes: list[str]).
    """
    from app.graphs.execution.graph import create_execution_graph, create_execution_input

    state = create_execution_input(
        task_description=scenario["task"],
        context={
            "project_root": str(tmp_dir),
            "workspace_context": WORKSPACE_CONTEXT,
        },
        task_id=f"e2e-{scenario['id']}",
    )

    adapter_cls = _make_adapter_class(model_name, call_log)
    with (
        patch("app.graphs.execution.nodes.plan.Anthropic", adapter_cls),
        patch("app.graphs.execution.nodes.generate.Anthropic", adapter_cls),
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "ollama-test"}),
    ):
        result = (create_execution_graph().compile()).invoke(state)

    notes_path = tmp_dir / "notes.txt"
    if not notes_path.exists():
        return False, None, ["FAIL: notes.txt not found after execution"]

    final_content = notes_path.read_text()
    exec_result = result.get("execution_result") or {}
    exec_logs = exec_result.get("logs", [])
    exec_status = exec_result.get("status", "unknown")

    notes = [f"execution status: {exec_status}"]

    # Log bucket [4]: combine decision (from execute.py log messages)
    combined = any("combining file_read result" in ln for ln in exec_logs)
    notes.append(f"execute combined read+write: {combined}")

    passed, content_notes = _evaluate_e2e(scenario["expected_mode"], final_content, combined)
    notes.extend(content_notes)
    return passed, final_content, notes


def _evaluate_e2e(expected_mode: str, final_content: str, combined: bool) -> tuple:
    notes = []

    if expected_mode == "append":
        original_present = "Original entry." in final_content
        content_added = final_content.strip() != ORIGINAL_CONTENT.strip()

        notes.append(
            "PASS: original content preserved" if original_present
            else "FAIL: original content was overwritten"
        )
        notes.append(
            "PASS: new content was added" if content_added
            else "FAIL: file unchanged after execution"
        )
        return original_present and content_added, notes

    else:  # replace
        original_absent = "Original entry." not in final_content
        has_any_content = bool(final_content.strip())

        if not original_absent:
            notes.append(
                "FAIL: original content still present after explicit rewrite "
                "(over-combine — preservation triggered incorrectly)"
            )
        else:
            notes.append("PASS: original content correctly replaced")

        if not has_any_content:
            notes.append("FAIL: file is empty after execution")

        return original_absent and has_any_content, notes


# ---------------------------------------------------------------------------
# Per-scenario runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict, model_name: str, log_dir: Path) -> dict:
    """Run plan + e2e checks for one scenario/model. Return results dict."""
    results = {}

    for level in ("plan", "e2e"):
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"lobster_m2_{model_name.split(':')[0]}_{scenario['id']}_"))
        try:
            _seed_workspace(tmp_dir)
            call_log = []

            if level == "plan":
                passed, plan_steps, notes = run_plan_check(
                    scenario, model_name, tmp_dir, call_log
                )
                final_content = None
            else:
                passed, final_content, notes = run_e2e_check(
                    scenario, model_name, tmp_dir, call_log
                )
                plan_steps = None

            log_file = _save_log(
                log_dir, model_name, scenario, level,
                call_log, plan_steps, final_content, passed, notes,
            )
            results[level] = {"passed": passed, "notes": notes, "log_file": str(log_file)}

        except Exception as exc:
            results[level] = {
                "passed": False,
                "notes": [f"ERROR: {exc}"],
                "log_file": None,
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# ---------------------------------------------------------------------------
# Log saving — four buckets
# ---------------------------------------------------------------------------

def _save_log(log_dir: Path, model: str, scenario: dict, level: str,
              call_log: list, plan_steps, final_content, passed, notes) -> Path:
    """
    Save structured log with the four diagnostic buckets:
      [1] raw_plan_llm_response    — prompt fragility / model weakness
      [2] parsed_plan_steps        — planning failure vs downstream
      [3] raw_generate_llm_response — generate mode confusion
      [4] execute_combine_decisions — extracted from notes
    """
    # Split call_log into plan call (index 0) and generate calls (index 1+)
    plan_call = call_log[0] if call_log else None
    generate_calls = call_log[1:] if len(call_log) > 1 else []

    combine_decisions = [n for n in (notes or []) if "combin" in n.lower()]

    payload = {
        "model": model,
        "scenario_id": scenario["id"],
        "scenario_label": scenario["label"],
        "level": level,
        "passed": passed,
        "notes": notes or [],
        # Bucket [1]: raw plan LLM response
        "raw_plan_llm_response": plan_call["response_text"] if plan_call else None,
        # Bucket [2]: parsed plan steps
        "parsed_plan_steps": plan_steps,
        # Bucket [3]: raw generate responses
        "raw_generate_llm_responses": [c["response_text"] for c in generate_calls],
        # Bucket [4]: execute combine decisions
        "execute_combine_decisions": combine_decisions,
        # Final file state
        "final_content": final_content,
        # Full prompt/response pairs for deep inspection
        "llm_calls_full": call_log,
    }

    slug = model.replace(":", "_").replace(".", "_")
    fname = log_dir / f"{slug}_{scenario['id']}_{level}.json"
    fname.write_text(json.dumps(payload, indent=2, default=str))
    return fname


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def print_summary(all_results: dict, models: list, scenarios: list) -> None:
    def sym(r):
        if r is None:
            return "  ---  "
        if r.get("passed"):
            return " PASS  "
        is_err = any("ERROR" in n for n in r.get("notes", []))
        return " ERROR " if is_err else " FAIL  "

    print(f"\n{'=' * W}")
    print("  V2 M2 OLLAMA ROBUSTNESS VALIDATION — SUMMARY")
    print(f"{'=' * W}")
    print(f"  Baseline: Claude (production). Ollama = robustness check.\n")

    # Header rows
    model_header = f"  {'Scenario':<26}"
    level_header = f"  {'':26}"
    for m in models:
        short = m.split(":")[0][:11]
        model_header += f"  {short:^{COL * 2}}"
        level_header += f"  {'Plan':^{COL}}{'E2E':^{COL}}"
    print(model_header)
    print(level_header)
    print(f"  {'─' * 26}" + "".join(f"  {'─' * COL}{'─' * COL}" for _ in models))

    for sc in scenarios:
        row = f"  {sc['id']}  {sc['label']:<23}"
        for m in models:
            mr = all_results.get((m, sc["id"]), {})
            row += f"  {sym(mr.get('plan')):^{COL}}{sym(mr.get('e2e')):^{COL}}"
        print(row)

    # Failure detail
    failures = [
        (m, sc_id, level, r)
        for (m, sc_id), levels in all_results.items()
        for level, r in levels.items()
        if not r.get("passed")
    ]

    if failures:
        print(f"\n  FAILURE DETAILS ({len(failures)} check(s) failed):")
        for m, sc_id, level, r in failures:
            sc_label = next(s["label"] for s in scenarios if s["id"] == sc_id)
            print(f"\n  [{m}] Scenario {sc_id} ({sc_label}) — {level.upper()}")
            for note in r.get("notes", []):
                tag = "  FAIL " if "FAIL" in note else "  INFO "
                print(f"    {tag}: {note}")
            if r.get("log_file"):
                print(f"    LOG  : {r['log_file']}")

        print(f"\n  Diagnosis guide:")
        print(f"    Bucket [1] raw_plan_llm_response   → prompt fragility or JSON rejection")
        print(f"    Bucket [2] parsed_plan_steps        → wrong tool order or missing keyword")
        print(f"    Bucket [3] raw_generate_responses   → generate mode confusion")
        print(f"    Bucket [4] execute_combine_decisions→ implementation or keyword mismatch")
    else:
        print(f"\n  All checks passed across all models and scenarios.")

    print(f"\n{'=' * W}\n")


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def _check_openai() -> bool:
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


def _check_ollama() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434", timeout=2)
        return True
    except Exception:
        return False


def _check_model(model_name: str) -> bool:
    """Return True if the model is available in the local Ollama registry."""
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
            data = _json.loads(r.read())
        names = [m["name"] for m in data.get("models", [])]
        # Ollama stores e.g. "llama3.2:3b" — match on prefix to handle digest variants
        return any(model_name in n or n.startswith(model_name.split(":")[0]) for n in names)
    except Exception:
        return False  # can't confirm but don't block


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="V2 M2 Ollama cross-model robustness validation"
    )
    parser.add_argument(
        "--models", nargs="+", default=DEFAULT_MODELS,
        metavar="MODEL",
        help="Ollama model tags (default: llama3.2:3b mistral:7b-instruct)",
    )
    parser.add_argument(
        "--scenarios", nargs="+", choices=["A", "B", "C"], default=["A", "B", "C"],
        metavar="ID",
        help="Scenario IDs to run: A (append) B (extend) C (replace) — default: all",
    )
    args = parser.parse_args()

    # Pre-flight
    if not _check_openai():
        print("ERROR: openai package not installed.")
        print("       pip install openai")
        sys.exit(1)

    if not _check_ollama():
        print("ERROR: Ollama not reachable at http://localhost:11434")
        print("       Start it with: ollama serve")
        sys.exit(1)

    for m in args.models:
        if not _check_model(m):
            print(f"WARNING: model '{m}' may not be pulled locally.")
            print(f"         Pull with: ollama pull {m}")

    active_scenarios = [s for s in SCENARIOS if s["id"] in args.scenarios]

    # Log directory
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "scripts" / "logs" / f"validate_m2_ollama_{ts}"
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nModels    : {args.models}")
    print(f"Scenarios : {[s['id'] + ' ' + s['label'] for s in active_scenarios]}")
    print(f"Logs      : {log_dir}\n")
    print("─" * W)

    all_results: dict = {}

    for model in args.models:
        print(f"\nModel: {model}")
        for scenario in active_scenarios:
            print(f"  [{scenario['id']}] {scenario['label']}")
            print(f"       {scenario['task']!r}")
            result = run_scenario(scenario, model, log_dir)
            all_results[(model, scenario["id"])] = result
            for level, r in result.items():
                tag = "PASS" if r["passed"] else "FAIL"
                print(f"       {level.upper():4}: {tag}")
                for note in r.get("notes", []):
                    if any(k in note for k in ("FAIL", "PASS", "ERROR")):
                        print(f"             {note}")
            print()

    print_summary(all_results, args.models, active_scenarios)
    print(f"Logs saved to: {log_dir}")


if __name__ == "__main__":
    main()
