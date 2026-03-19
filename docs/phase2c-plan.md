# Phase 2C Plan — Review Subgraph

> Status: PLANNING — do not implement until this plan is approved.
> Phase 2A: COMPLETE (Research Subgraph LLM integration)
> Phase 2B: COMPLETE (Execution Subgraph real implementation)

---

## 1. Current Limitations

Every node in the Review Subgraph is a Phase 1 placeholder.

| Component | Current behaviour | Problem |
|-----------|-------------------|---------|
| `inspect.py` | Returns `issues=[]` unconditionally | Never reads `review_target`; all inputs look identical |
| `verdict.py` | Logic is structurally correct | Verdict is always `pass` because `inspect` never produces issues |
| `assemble.py` | Returns a hardcoded generic string | Ignores actual results; `approved_artifacts` always `[]` |
| `validator.py` | Both functions always return `True`/`[]` | No real validation logic exists |
| `prompts/templates.py` | Two stub prompt strings | Prompts are defined but never invoked |

**Structural gaps:**

- `review_target` is passed in but never parsed by any node — its contents are invisible to the review
- `approved_artifacts` is never populated regardless of verdict
- `final_response` is a hardcoded template string, not a summary of actual results
- `ValidationCheck` does not exist — there is no record of which rules ran or passed
- The verdict signal sent back to `MainState` is meaningless because it is always `pass`

---

## 2. Target Capabilities for Phase 2C

Phase 2C makes the Review Subgraph a genuine validator and assembler.

**Core constraint (spec §3.4):** Review must NOT regenerate content. It validates what was produced and assembles a presentation of it. The LLM is permitted only in the `inspect` node, and only to assess quality — not to rewrite or extend outputs.

### Inspect node
- **Phase A — deterministic structural checks** (no LLM, always runs):
  - `execution_result` is present and not None
  - `execution_result.success` is `True`
  - `execution_result.errors` is empty
  - At least one artifact was produced (when task implies file output)
  - Each artifact has the required fields (`type`, `name`, `path`)
  - Execution logs are non-empty
- **Phase B — LLM quality check** (optional, only when structural checks pass):
  - Sends artifact content + task description to Claude
  - Claude returns a structured JSON list of issues (no free-text generation)
  - Detects: placeholder/stub content, content mismatched to task, empty files, malformed structure
  - Falls back gracefully when `ANTHROPIC_API_KEY` is absent — only structural checks run

### Verdict node
- Retains existing decision logic (critical issues → fail, non-critical → revise, none → pass)
- Produces a `review_notes` string that enumerates actual findings rather than a generic count

### Assemble node
- Builds `final_response` deterministically from `review_target` content — no LLM
- For `pass`: lists files created/read, summarises execution steps, confirms validation passed
- For `revise`: lists outputs produced + enumerates issues requiring attention
- For `fail`: describes what failed, lists any partial artifacts preserved for inspection
- Populates `approved_artifacts` from `review_target["artifacts"]` when verdict is `pass` or `revise`
- For `fail`: `approved_artifacts` is empty (outputs not approved, but preserved in state)

---

## 3. Schema Changes Needed

### 3.1 New: `ValidationCheck` TypedDict (review/state.py)

Records the result of a single validation rule — pass or fail — for auditability.

```python
class ValidationCheck(TypedDict):
    rule: str                              # rule identifier (e.g. "execution_success")
    passed: bool
    message: str                           # human-readable result
    severity: Literal["critical", "major", "minor"]  # only relevant when passed=False
    location: Optional[str]               # which field or artifact failed
```

### 3.2 Add `validation_checks` to `ReviewState`

```python
class ReviewState(TypedDict):
    ...
    validation_checks: list[ValidationCheck]   # NEW — full audit of all checks run
```

This field records both passing and failing checks. `issues` (already exists) records only failures, formatted as `ReviewIssue`. The two fields serve different audiences: `validation_checks` for debugging, `issues` for verdict logic.

### 3.3 No other schema changes

`ReviewIssue`, `verdict`, `review_notes`, `approved_artifacts`, `final_response`, and `logs` are all adequate as-is. The existing `ReviewIssue.severity` values (`critical`, `major`, `minor`) map cleanly to the verdict tiers.

---

## 4. Validation Rules

### 4.1 Structural rules (deterministic, always run, no LLM)

All rules are implemented in `validator.py` as individual functions, each returning a `ValidationCheck`.

| Rule ID | What it checks | Failure severity |
|---------|---------------|-----------------|
| `execution_result_present` | `review_target["execution_result"]` is not None | critical |
| `execution_success` | `execution_result["success"]` is `True` | critical |
| `no_execution_errors` | `execution_result["errors"]` is empty | critical |
| `artifacts_present` | `review_target["artifacts"]` is non-empty | major |
| `artifact_fields_complete` | Each artifact has `type`, `name`, `path` set | major |
| `logs_non_empty` | `execution_result["logs"]` is non-empty | minor |

**Failure severity rationale:**
- `critical`: task fundamentally did not complete; `fail` verdict warranted
- `major`: task completed but produced suspect output; `revise` warranted
- `minor`: task completed, minor observability gap; `pass` with note

### 4.2 Quality rules (LLM, Phase B, only when structural checks pass)

The inspect node sends a constrained LLM prompt to Claude. The prompt instructs Claude to:
- Examine the artifact content against the stated task objective
- Return ONLY a JSON array of issues (not revised content)
- Each issue: `{"rule": "...", "severity": "critical|major|minor", "description": "...", "location": "..."}`

Quality issues detected:
- Placeholder/stub content (e.g. file content is empty or is literal "hello world" for a serious task)
- Content clearly mismatched to the task description
- Malformed structure (e.g. Python file with syntax errors, JSON with invalid syntax)

Quality issues map directly to `ReviewIssue` records and influence verdict.

### 4.3 What is NOT validated

- Research result quality — `review_target` currently only contains `execution_result` + `artifacts`
- Safety flag review — safety flags are already captured in `ExecutionState`; Review does not re-evaluate them
- External correctness — Review cannot test whether code runs or whether written content is factually true

---

## 5. Verdict Design

### Decision table

| Structural checks | LLM quality | Verdict |
|-------------------|-------------|---------|
| Any critical failure | — (skip LLM) | `fail` |
| All pass | Critical quality issue | `fail` |
| All pass | Major quality issue | `revise` |
| All pass | Minor quality issue or no issues | `pass` |
| All pass | LLM unavailable (no API key) | `pass` |

### Rules

- The verdict node reads from `issues` (populated by inspect)
- If any issue has `severity="critical"` → `fail`
- Else if any issue has `severity="major"` → `revise`
- Else → `pass`
- The verdict node does not call the LLM — verdict is fully deterministic given `issues`

### `review_notes` content

`review_notes` is constructed by the verdict node as a human-readable summary:
- `pass`: "All N validation checks passed. [LLM: no quality issues found / LLM: not run]"
- `revise`: "N of M checks flagged issues: [issue 1 description]; [issue 2 description]"
- `fail`: "Critical validation failure: [issue description]. Task output is not usable."

### Parent Graph handling (already defined in spec §4.4, unchanged)

- `pass` → `finalize_response` marks status `success`
- `revise` → `finalize_response` marks status `partial`; user sees issues in `final_response`
- `fail` → `finalize_response` marks status `failed`; partial artifacts preserved in state

No automatic retry in V1. No Parent Graph changes required.

---

## 6. Final Response Assembly Strategy

The `assemble` node is fully deterministic — no LLM. It builds `final_response` by reading `review_target` content.

### Template structure per verdict

**`pass`:**
```
Task completed successfully.

Outputs produced:
  - <artifact.name> → <artifact.path>    (one line per artifact)

Execution summary:
  <last 3 log lines from execution_result["logs"]>

All validation checks passed.
```

**`revise`:**
```
Task completed with issues requiring review.

Outputs produced:
  - <artifact.name> → <artifact.path>

Issues to address:
  [major] <issue description>
  [minor] <issue description>

Review these issues before relying on the outputs.
```

**`fail`:**
```
Task failed validation and outputs are not approved.

Reason: <critical issue description>

Partial outputs (preserved for inspection):
  - <artifact.name> → <artifact.path>    (if any artifacts exist)

Check the execution error log for details.
```

### `approved_artifacts` population

```
verdict == "pass"   → approved_artifacts = all artifacts from review_target["artifacts"]
verdict == "revise" → approved_artifacts = artifacts that passed artifact_fields_complete check
verdict == "fail"   → approved_artifacts = []
```

---

## 7. Files to Modify

| File | Change |
|------|--------|
| `review/state.py` | Add `ValidationCheck` TypedDict; add `validation_checks: list[ValidationCheck]` to `ReviewState` |
| `review/prompts/templates.py` | Write real inspection prompt for LLM quality check |
| `review/services/validator.py` | Implement each structural rule as a function returning `ValidationCheck`; implement `run_structural_checks()` helper |
| `review/nodes/inspect.py` | Phase A: call `run_structural_checks()`; Phase B: LLM quality check if API key present and structural checks passed; populate `issues` and `validation_checks` |
| `review/nodes/verdict.py` | Enhance `review_notes` to enumerate actual findings rather than a count |
| `review/nodes/assemble.py` | Deterministic assembly from `review_target`; populate `approved_artifacts` |
| `tests/graphs/test_review.py` | Extend with Phase 2C test cases (see §8) |

**Do not modify:**
- `app/graphs/main/` — Parent Graph is out of scope
- `app/graphs/research/` — Research Subgraph is not touched
- `app/graphs/execution/` — Execution Subgraph is not touched
- `app/schemas/` — Shared schemas are stable
- `review/graph.py` — Graph topology is unchanged (linear: inspect → verdict → assemble → END)
- `review/nodes/__init__.py` — No new nodes are added

---

## 8. Test Plan

All tests go in `tests/graphs/test_review.py`. Do not modify other test files.

### Phase 1 smoke tests (preserve, may need minor updates)

| Test | Update needed |
|------|--------------|
| `test_review_graph_creation` | Keep as-is |
| `test_review_input_creation` | Add `validation_checks=[]` to expected initial state |
| `test_review_workflow` | Keep structure, check `validation_checks` is populated |
| `test_review_verdict_generation` | Phase 1 asserts `verdict == "pass"` — will still hold (real execution_result not passed, structural check for `execution_result_present` will flag it, BUT input is a raw dict `{"result": "..."}` not a structured ExecutionOutput... need to review how this test constructs input) |
| `test_review_logging` | Keep as-is |

> Note: `test_review_verdict_generation` currently passes `review_target={"output": "test output"}` — a raw dict with no `execution_result` key. After Phase 2C, the `execution_result_present` structural check will flag this as a critical failure, changing the verdict from `pass` to `fail`. This test must be updated to reflect the new behaviour or be replaced by a more meaningful scenario.

### Unit tests — structural validator (no LLM)

| Test | What it verifies |
|------|-----------------|
| `test_structural_check_passes_complete_result` | All checks pass for a well-formed `ExecutionOutput` with artifacts |
| `test_structural_check_fails_null_result` | `execution_result_present` fails when `execution_result` is None |
| `test_structural_check_fails_on_execution_error` | `execution_success` and `no_execution_errors` fail for failed execution |
| `test_structural_check_flags_empty_artifacts` | `artifacts_present` fails when artifact list is empty |
| `test_structural_check_flags_incomplete_artifact` | `artifact_fields_complete` fails when artifact has no path |
| `test_structural_check_flags_empty_logs` | `logs_non_empty` minor flag when logs list is empty |

### Unit tests — inspect node (no LLM)

| Test | What it verifies |
|------|-----------------|
| `test_inspect_with_healthy_execution` | Passes clean ExecutionOutput; issues=[], all checks pass |
| `test_inspect_with_failed_execution` | Failed ExecutionOutput; issues contain critical item |
| `test_inspect_with_no_execution_result` | Missing result; critical issue captured |
| `test_inspect_with_empty_artifacts` | Artifacts absent; major issue captured |
| `test_inspect_validation_checks_recorded` | `validation_checks` field populated with all rules run |

### Unit tests — inspect node (LLM mocked)

| Test | What it verifies |
|------|-----------------|
| `test_inspect_llm_quality_check_adds_issues` | LLM returns quality issue; appears in `issues` |
| `test_inspect_llm_unavailable_fallback` | No API key → only structural checks run, no crash |
| `test_inspect_llm_failure_fallback` | LLM call raises exception → structural checks still returned |

### Unit tests — verdict node

| Test | What it verifies |
|------|-----------------|
| `test_verdict_pass_on_no_issues` | Empty issues → `pass` |
| `test_verdict_fail_on_critical` | Critical issue → `fail` |
| `test_verdict_revise_on_major` | Major issue (no critical) → `revise` |
| `test_verdict_pass_on_minor_only` | Minor-only issues → `pass` with notes |
| `test_verdict_notes_enumerate_issues` | `review_notes` names the actual issue descriptions |

### Unit tests — assemble node

| Test | What it verifies |
|------|-----------------|
| `test_assemble_pass_lists_artifacts` | `final_response` contains artifact names and paths |
| `test_assemble_pass_approved_artifacts_populated` | `approved_artifacts` == all artifacts when pass |
| `test_assemble_revise_lists_issues` | `final_response` contains issue descriptions |
| `test_assemble_revise_approved_artifacts_partial` | `approved_artifacts` contains only field-complete artifacts |
| `test_assemble_fail_no_approved_artifacts` | `approved_artifacts == []` on fail |
| `test_assemble_fail_response_names_reason` | `final_response` includes critical issue description |

### Integration tests (full graph, LLM mocked for quality check)

| Test | What it verifies |
|------|-----------------|
| `test_full_review_workflow_healthy` | Complete graph with healthy execution input; verdict `pass`, final_response references artifacts |
| `test_full_review_workflow_failed_execution` | Failed execution input; verdict `fail`, final_response explains failure |
| `test_full_review_workflow_quality_issue` | Mocked LLM returns quality issue; verdict `revise` |
| `test_full_review_logging` | All three nodes contribute to `logs` |

### What is NOT tested in Phase 2C

- Real LLM calls (always mock Anthropic in tests)
- Research result review (not in `review_target` yet)
- Retry/re-execution flows (out of V1 scope)
- Parent Graph integration changes

---

## 9. Explicit Out of Scope for Phase 2C

| Item | Reason |
|------|--------|
| Automatic re-execution on `revise` or `fail` | V1 explicitly excludes retry loops (spec §4.4) |
| Content regeneration or rewriting by Review | Prohibited by spec §3.4 — Review is validator/assembler only |
| Research result review | `review_target` currently only contains `execution_result` + `artifacts`; adding research review requires wrapper changes (deferred) |
| Parent Graph changes | Router, wrappers, finalize_response — frozen for Phase 2C |
| Execution or Research Subgraph changes | Not in scope |
| `review/graph.py` topology changes | Linear flow is correct for Phase 2C |
| Streaming final response | V2 candidate (spec §12) |
| Human-in-the-loop approval gate | V3 candidate |
| Safety flag re-evaluation | Already handled in Execution Subgraph; Review does not re-check |
| Cross-artifact consistency checks | Deferred; complex and not needed for V1 acceptance criteria |
| Factual correctness verification | Not possible without external knowledge; out of V1 scope |
