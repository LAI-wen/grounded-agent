# Phase 2B Plan — Execution Subgraph

> Status: PLANNING — do not implement until this plan is approved.
> Phase 2A: COMPLETE (Research Subgraph LLM integration)

---

## 1. Current Limitations

Every node in the Execution Subgraph is a Phase 1 placeholder. Concretely:

| Node | Current behaviour | Problem |
|------|-------------------|---------|
| `plan.py` | Hardcoded 2-step mock plan | Ignores task content; plan is always identical |
| `generate.py` | Returns `# Action for <action>` strings | Not real code or commands; unusable by tools |
| `simulate.py` | Always returns `success: True` | No actual dry-run; safety signal is meaningless |
| `safety_check.py` | Always passes | Zero protection; spec §7 requires real validation |
| `execute.py` | Returns `["Output 1", "Output 2"]` | Never invokes a real tool |
| `executor.py` | `execute_safe_action` returns mock dict | Tool dispatch layer is unimplemented |
| `file_ops.py` | `read_file` returns mock string; `write_file` always `True` | File I/O does not touch the filesystem |
| `prompts/templates.py` | Two stub prompt strings | Prompts exist but are never sent to an LLM |

**Structural gaps:**
- `ExecutionStep.tool` is always `None` — no node populates or resolves it
- `SafetyCheckResult.violations` is always empty — no rule engine exists
- No routing exists inside the subgraph; graph is linear with no abort path
- No conditional edge on safety check failure (the abort today only sets `success=False`, graph still reaches `execute`)
- `artifacts` list is never populated by any node
- `execution_result` is a free-form `dict` — not the structured schema defined in spec §3.3

---

## 2. Target Capabilities for Phase 2B

Phase 2B makes the Execution Subgraph functional for the two allowed safe tool types:
- **project-scoped file read** — read any file under `project_root`
- **project-scoped file write** — write/create any file under `project_root`

Additional targets:
- LLM-driven planning: `plan` node calls Claude to decompose the task into typed `ExecutionStep` objects with `tool` populated
- LLM-driven code/action generation: `generate` node calls Claude to produce real, reviewable action payloads
- Real safety check: `safety_check` node validates paths and detects forbidden operations using a rule engine
- Conditional routing: graph aborts to END (with error state) if safety check fails, skipping `execute`
- Real tool dispatch: `executor.py` resolves `ExecutionStep.tool` to the correct tool function and invokes it
- Real file I/O: `file_ops.py` implements actual read/write with path validation
- Structured `execution_result`: output conforms to the spec §3.3 schema (`actions_taken`, `artifacts`, `logs`, `success`, `errors`)
- Artifact records: every file produced is recorded in `artifacts` using the shared `Artifact` schema

**Explicitly excluded from Phase 2B** — see §8.

---

## 3. Schema Changes Needed

### 3.1 `ExecutionStep` (state.py)

Add `tool_input` to carry the validated payload the tool will receive:

```python
class ExecutionStep(TypedDict):
    step_id: str
    action: str
    tool: Optional[str]          # "file_read" | "file_write" | None
    params: dict[str, Any]
    tool_input: Optional[dict[str, Any]]   # NEW — resolved, validated input for tool
    status: Literal["pending", "running", "success", "failed"]
```

### 3.2 `SafetyCheckResult` (state.py)

Add `flags` to carry structured `SafetyFlag` records (uses existing shared schema):

```python
class SafetyCheckResult(TypedDict):
    passed: bool
    violations: list[str]
    warnings: list[str]
    flags: list[SafetyFlag]      # NEW — structured safety flag records
```

### 3.3 `ExecutionState` (state.py)

Add `abort_reason` to carry the reason when execution is halted by safety check:

```python
class ExecutionState(TypedDict):
    ...
    abort_reason: Optional[str]  # NEW — set when execution is aborted
```

### 3.4 `execution_result` type

Currently `Optional[dict[str, Any]]`. Replace with a typed `ExecutionResult` TypedDict matching spec §3.3:

```python
class ExecutionResult(TypedDict):
    actions_taken: list[str]
    artifacts: list[Artifact]
    logs: list[str]
    success: bool
    errors: list[Error]
```

Update `ExecutionState.execution_result` from `Optional[dict[str, Any]]` to `Optional[ExecutionResult]`.

---

## 4. Tool Safety Constraints

These constraints must be enforced in both `safety_check.py` (pre-execution) and `file_ops.py` (at call time).

### Allowed operations
- `file_read`: Read any file where resolved path is under `project_root`
- `file_write`: Write any file where resolved path is under `project_root`, and the path does not overwrite a protected file (e.g. `pyproject.toml`, `.env`, `*.lock`)

### Forbidden operations (must raise `SafetyFlag` with `severity="high"`)
- Any path that resolves outside `project_root` (symlink traversal included)
- Writes to paths matching: `**/.env*`, `**/pyproject.toml`, `**/*.lock`, `**/*.cfg`
- Deletions of any kind (not in allowed tool list)
- Shell execution (not in allowed tool list)
- Network requests (not in allowed tool list)

### Safety check rule engine (safety_check.py)
The rule engine must:
1. Iterate over each `ExecutionStep`
2. For each step with a `tool`, resolve the path from `params`
3. Apply path boundary check using `pathlib.Path.resolve()`
4. Apply protected-file pattern checks
5. Populate `SafetyCheckResult.violations` (blocking) and `warnings` (non-blocking)
6. Set `passed = len(violations) == 0`

If `passed` is `False`, the graph must route to END and must NOT call `execute`.

### Conditional edge in graph.py

```
safety_check --> [passed] --> execute --> END
             --> [failed] --> END
```

Use `add_conditional_edges` on `"safety_check"` with a router function that reads `state["safety_check"]["passed"]`.

---

## 5. Files to Modify

| File | Change |
|------|--------|
| `execution/state.py` | Add `ExecutionResult` TypedDict; add `tool_input` to `ExecutionStep`; add `flags` to `SafetyCheckResult`; add `abort_reason` to `ExecutionState`; update `execution_result` type |
| `execution/graph.py` | Add conditional edge on `safety_check` → abort path; update edge list |
| `execution/nodes/plan.py` | Replace mock plan with LLM call using `PLAN_EXECUTION_PROMPT`; populate `tool` field on each `ExecutionStep` |
| `execution/nodes/generate.py` | Replace mock with LLM call using `GENERATE_CODE_PROMPT`; populate `tool_input` on each step |
| `execution/nodes/simulate.py` | Implement real dry-run: resolve paths, check file existence, estimate write sizes |
| `execution/nodes/safety_check.py` | Implement rule engine as described in §4 |
| `execution/nodes/execute.py` | Implement real tool dispatch via `executor.py`; populate `artifacts` using `Artifact` schema; produce typed `ExecutionResult` |
| `execution/services/executor.py` | Implement `execute_safe_action` to dispatch to `file_ops.py` based on `tool` field; implement real `validate_path_safety` |
| `execution/prompts/templates.py` | Write real prompt templates for plan and generate nodes |
| `app/tools/file_ops.py` | Implement real `read_file` and `write_file` with `pathlib` path validation and protected-file checks |
| `tests/graphs/test_execution.py` | Extend with Phase 2B test cases (see §7) |

**Do not modify:**
- `app/graphs/main/` — Parent Graph is out of scope
- `app/graphs/research/` — Research Subgraph is not touched in Phase 2B
- `app/graphs/review/` — Review Subgraph is not touched in Phase 2B
- `app/schemas/` — Shared schemas are stable; new schemas go in `execution/state.py`

---

## 6. LLM Integration Pattern

Follow the same pattern used in Phase 2A (Research Subgraph):
- Use the project's existing LLM client (Ollama or API)
- Prompts live in `prompts/templates.py`; nodes import them
- LLM output is parsed into typed structures before writing to state
- If LLM call fails, catch exception, write to `errors`, set `success=False`, return early

---

## 7. Test Plan

All tests go in `tests/graphs/test_execution.py`. Do not modify other test files.

### Unit tests (no LLM, no filesystem)

| Test | What it verifies |
|------|-----------------|
| `test_execution_graph_creation` | Graph compiles without error (already exists — keep) |
| `test_execution_input_creation` | `create_execution_input` initialises all new fields |
| `test_safety_check_pass` | Rule engine passes clean steps with valid project-scoped paths |
| `test_safety_check_path_traversal` | Rule engine flags `../` or absolute path outside project root |
| `test_safety_check_protected_file` | Rule engine flags write to `.env` or `pyproject.toml` |
| `test_safety_check_failure_aborts_execute` | Graph routes to END without calling `execute` when safety fails |
| `test_file_ops_read_valid` | `read_file` returns real content for a file inside project root |
| `test_file_ops_read_invalid_path` | `read_file` raises `ValueError` for path outside project root |
| `test_file_ops_write_valid` | `write_file` creates file inside project root |
| `test_file_ops_write_outside_root` | `write_file` raises `ValueError` for path outside project root |
| `test_file_ops_write_protected` | `write_file` raises `ValueError` for protected filename |
| `test_execution_result_schema` | `execution_result` is an `ExecutionResult` TypedDict with all required fields |
| `test_artifacts_populated` | After successful file write, `artifacts` contains one `Artifact` record |

### Integration tests (LLM mocked)

| Test | What it verifies |
|------|-----------------|
| `test_full_workflow_file_write` | Full graph run produces a file on disk (temp dir) |
| `test_full_workflow_safety_abort` | Full graph run with unsafe input aborts before `execute` |
| `test_execution_logging` | All nodes contribute to `logs` (already exists — extend) |

### What is NOT tested in Phase 2B
- Multi-step plans with inter-step dependencies
- Real LLM calls (always mock the LLM in tests)
- Review Subgraph integration
- Concurrent tool invocations

---

## 8. Explicit Out of Scope for Phase 2B

The following are **not implemented** in Phase 2B:

| Item | Reason |
|------|--------|
| Shell/command execution | Forbidden in V1 tool list (spec §6) |
| Network requests | Forbidden in V1 tool list |
| Search tool invocation from Execution Subgraph | Execution must not introduce new research (spec §3.3) |
| Retry loops on execution failure | V1 explicitly excludes automatic retries (spec §4.4) |
| Multi-tool parallel dispatch | Not needed for file read/write; deferred to V2 |
| Streaming execution output | V2 candidate (spec §12) |
| Human-in-the-loop approval gate | V2/V3 candidate |
| Review Subgraph modifications | Review is a separate phase |
| Parent Graph routing changes | Parent Graph is frozen for Phase 2B |
| `interpret.py` / `synthesize.py` removal | Deferred to cleanup pass after Phase 2B stability confirmed |
| Long-term memory or cross-session state | V2+ |

---

## 9. Definition of Done

Phase 2B is complete when:

1. All nodes invoke real logic (no placeholder returns)
2. `safety_check` rule engine catches path traversal and protected-file violations
3. Conditional edge in graph aborts execution on safety failure
4. `file_ops.py` performs real filesystem I/O with path validation
5. `execution_result` is a typed `ExecutionResult` — not a free-form dict
6. `artifacts` is populated for every file produced
7. All Phase 2B unit and integration tests pass
8. Existing Phase 1 smoke tests still pass
9. No changes made to Parent Graph, Research Subgraph, or Review Subgraph
