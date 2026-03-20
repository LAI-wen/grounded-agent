# Spec: V2 Workspace Memory

**Status**: Milestones 1, 2, and 3 complete. All live-validated 2026-03-20.
**Depends on**: V1 complete (150 tests passing, live-validated).

---

## Goal

Give the agent durable memory of what has happened inside a specific project
directory, so that follow-up sessions and new tasks can be informed by prior
work without relying on the user to re-state context.

The agent should be able to answer questions like:
- "What files have you created in this project?"
- "What did we decide about the output directory last time?"
- "I asked you to create a summary document — can you update it?"

without requiring the user to repeat prior conversation history.

---

## Scope

- A file-backed workspace memory store (`app/memory/workspace.py`)
- Written at the end of each completed task (after `finalize_response`)
- Read at the start of each new task (before `normalize_task`)
- Injected as context into task classification and execution planning
- Scoped strictly to the current `project_root` directory
- JSON lines or similar flat format — no database, no vector store

The store is a single file per workspace, located at
`<project_root>/.lobster/workspace_memory.jsonl` (or similar).

---

## Non-goals

- **No cross-workspace memory** — each project root has its own isolated store.
  The agent does not share knowledge between different directories.
- **No user profile or preferences across projects** — preferences noted in one
  workspace do not carry to another.
- **No semantic / vector search** — memory is injected as compact structured
  text, not retrieved by embedding similarity. V2 does not require a vector store.
- **No background or autonomous memory updates** — memory is only written when
  a task completes via the normal workflow. The agent does not observe or index
  the file system independently.
- **No memory UI** — the user cannot browse or edit memory through the CLI in
  V2. Inspection is via the raw file; clearing is by deleting it.
- **No long-term user profile** — cross-session user preferences are out of
  scope for V2.

---

## Memory types

Three record types, each stored as a JSON object per line:

### 1. Task summary

Written once per completed task, after `finalize_response`.

```json
{
  "type": "task_summary",
  "task_id": "...",
  "timestamp": "2026-03-20T10:00:00Z",
  "task_type": "execution",
  "objective": "Create hello.txt with content Hello World",
  "status": "success",
  "artifacts": ["hello.txt"],
  "verdict": "pass"
}
```

### 2. Artifact record

Written once per file successfully written by the execution subgraph.

```json
{
  "type": "artifact",
  "timestamp": "2026-03-20T10:00:00Z",
  "path": "hello.txt",
  "operation": "write",
  "task_id": "..."
}
```

### 3. Preference

Written when the user states an explicit preference during a task.
Detection is best-effort in V2: `normalize_task` looks for preference-signalling
phrases ("I prefer", "always use", "from now on").

```json
{
  "type": "preference",
  "timestamp": "2026-03-20T10:00:00Z",
  "content": "User prefers output files in the outputs/ directory",
  "source_task_id": "..."
}
```

---

## Integration points

### Read path — start of each turn

`run_lobster_agent` reads the last N entries from the workspace memory file
(default: 5 task summaries + all preferences + last 10 artifact records)
and composes a compact context block. This block is injected into:

1. **`normalize_task`** — so classification knows what files already exist and
   what preferences have been noted. Passed as an additional field in state
   (`workspace_context: str`), formatted alongside `conversation_history`.

2. **`create_execution_plan`** — so the planner knows which files already exist
   and avoids overwriting or duplicating work. Passed through the existing
   `context` dict in `ExecutionState`.

### Write path — end of each turn

After `finalize_response` returns, `run_lobster_agent` writes to the store:
- one `task_summary` record
- one `artifact` record per file written (from `execution_result.artifacts`)
- zero or more `preference` records (if detected by `normalize_task`)

The write is append-only. Errors in the write path are logged but do not
fail the task response.

### Files touched

| File | Change |
|---|---|
| `app/memory/workspace.py` | New — `WorkspaceStore` with read/write logic and record types |
| `app/main.py` | Read before invoke, write after invoke |
| `app/graphs/main/state.py` | Add `workspace_context: Optional[str]` to `MainState` |
| `app/graphs/main/nodes.py` | Inject `workspace_context` into `normalize_task` prompt |
| `app/graphs/main/prompts/templates.py` | Add `{workspace_context}` to `NORMALIZE_TASK_USER_PROMPT` |
| `app/graphs/main/wrappers.py` | Pass `workspace_context` in research context dict |
| `app/graphs/research/nodes/synthesize_evidence.py` | Read `workspace_context` from context; skip early-return when present |
| `app/graphs/research/prompts/templates.py` | Add `{workspace_context}` to `SYNTHESIZE_EVIDENCE_USER_PROMPT` |

The `wrappers.py` and research synthesis changes were added during implementation
(not in the original spec) after validating that the synthesis layer early-returned
when `filtered_sources` was empty, preventing workspace-context-only answers.

---

## Safety and retention rules

### Scope boundary

The store is only created inside the project root (`<project_root>/.lobster/`).
If the agent cannot write to that directory, it logs a warning and continues
without persistence — workspace memory is always optional, never load-bearing.

### What is never stored

- File contents (only paths and metadata)
- API keys, credentials, or environment variables
- Raw conversation messages (those belong to the thread checkpointer)
- Data from outside `project_root`

### Retention

- Task summaries: keep the last 50 entries (older entries are dropped on write)
- Artifact records: keep the last 100 entries
- Preferences: keep all (typically few; user can clear by deleting the file)

The store is human-readable and user-deletable. Deleting
`<project_root>/.lobster/workspace_memory.jsonl` resets memory for that
workspace with no other effect on the agent.

### `.gitignore`

`lobster_agent/.gitignore` should include `.lobster/` so workspace memory
files are not committed.

---

## First milestone — Complete

**Status**: Complete. 165 tests passing. Live-validated 2026-03-20.

**Deliverables as implemented:**

1. `app/memory/workspace.py` — `WorkspaceStore` class with `read_context()`,
   `write_task_summary()`, token guard (1500 chars), path normalisation,
   retention trimming (50 task summaries / 100 artifact records)
2. `app/main.py` — reads context before graph invocation, writes after
3. `MainState` — `workspace_context: Optional[str]` field added
4. `normalize_task` — `workspace_context` injected into LLM prompt
5. `synthesize_evidence` — `workspace_context` received via research context
   dict; early-return bypassed when workspace context is present and
   filtered_sources is empty (added during implementation)
6. `lobster_agent/.gitignore` — `.lobster/` excluded
7. Tests — `tests/memory/test_workspace.py` (13 tests) and 2 synthesis
   behaviour tests added to `tests/graphs/research/test_nodes.py`

**Live demo result:**

```
Session 1:
  Request: Create hello.txt with "Hello from Lobster Agent"
  Status: success
  Store: 2 records written (task_summary + artifact)

Session 2 (new thread_id):
  Request: What files have you created in this project?
  Status: success
  Response: "A file named hello.txt was created during this project session"
  Confidence: 95%
  Source: workspace history, not file walk
```

**Not in first milestone (deferred):** preference detection, execution planner
injection, cross-session preference recall. See roadmap for M2 scope.

---

## Second milestone — Complete

**Status**: Complete. 172 tests passing. Live-validated 2026-03-20.

**Problem addressed:**

M1 gave the planner no knowledge of prior artifacts. A "append to notes.txt"
request in Session 2 would overwrite the file rather than extend it, because
the planner had no way to know the file existed and the generator had no way
to incorporate its contents.

**Deliverables as implemented:**

1. `app/graphs/main/wrappers.py` — `workspace_context` added to
   `ExecutionState.context` in `invoke_execution_subgraph`
2. `app/graphs/execution/nodes/plan.py` — extracts `workspace_context` from
   context dict; formats as "Known existing artifacts from workspace context"
   block in user prompt; excluded from generic context string; log annotated
   when injected
3. `app/graphs/execution/prompts/templates.py`:
   - `PLAN_EXECUTION_SYSTEM_PROMPT` — added "Known existing artifacts" rules:
     prefer `file_read` before `file_write` for known files; write step action
     MUST signal preservation semantics; examples provided for planner
   - `GENERATE_ACTIONS_SYSTEM_PROMPT` — added APPEND mode / REPLACE mode
     distinction: in APPEND mode the generator produces only new content;
     execute combines at runtime
4. `app/graphs/execution/nodes/execute.py` — `read_cache` (path → content)
   tracks `file_read` results; before each `file_write`, if the path was
   previously read AND the step action contains an append keyword, content is
   combined as `existing.rstrip("\\n") + "\\n" + new_content`; replace
   semantics (no keyword) are unaffected
5. Tests — 7 new tests in `tests/graphs/test_execution.py`:
   workspace context in prompt, artifacts block absent without context,
   combine unit test, no-combine unit test, plan/generate prompt assertions,
   full graph integration

**Live demo result:**

```
Session 1: "Create notes.txt with 'Project started. Initial notes.'"
  → success, store written

Session 2: "Append 'V2 milestone reached.' to notes.txt"
  → planner emits file_read → file_write (artifact awareness active)
  → generator produces only new content (APPEND mode)
  → execute combines: read result + new content
  → final file: "Project started. Initial notes.\nV2 milestone reached."
```

**Append keyword set** (triggers combine in execute):
`"append"`, `"preserve existing"`, `"add to existing"`, `"add new"`

**Not in M2:** preference detection, research continuation improvements.
See roadmap M3.

---

## M2 cross-model robustness validation

**Script**: `scripts/validate_m2_ollama.py`
**Models tested**: `mistral:7b-instruct`, `llama3.2:3b`
**Scenarios**:
- A — Append (explicit): "Append the line 'Second entry.' to notes.txt"
- B — Extend (implicit): "Add a summary section to notes.txt"
- C — Replace (explicit): "Rewrite notes.txt from scratch with completely new content"

**Results after all production fixes (2026-03-20):**

| Model | Scenario | Plan | E2E |
|---|---|---|---|
| mistral:7b-instruct | A Append | PASS | FAIL† |
| mistral:7b-instruct | B Extend | PASS | FAIL† |
| mistral:7b-instruct | C Replace | PASS | PASS |
| llama3.2:3b | A Append | FAIL‡ | FAIL‡ |
| llama3.2:3b | B Extend | FAIL‡ | FAIL‡ |
| llama3.2:3b | C Replace | PASS | FAIL‡ |

† Model-level weakness (see below). ‡ Model below reliability threshold (see below).

---

### Fixed production issues (discovered during validation)

These were real code or prompt defects, now corrected:

**1. `"based on file_read"` / `"base on file_read"` over-broad as append keywords**
- Both models used this phrasing in replace-scenario write actions
  (e.g. `"Write notes.txt based on file_read result, replacing all content"`),
  triggering the combine logic on explicit rewrites.
- Fix: removed from `_APPEND_KEYWORDS` in `execute.py` and from plan prompt examples.

**2. Double braces in `GENERATE_ACTIONS_SYSTEM_PROMPT`**
- The prompt used `{{`, `}}` (Python format-string escapes) but was never passed
  to `.format()`. The model received literal `{{` and mimicked it, producing
  invalid JSON (`{{"path": ...}}`). Parse failure → fallback → empty write content.
- Fix: replaced all `{{`/`}}` with single `{`/`}` in the generate system prompt.

**3. `"preserve prior"` over-broad as an append keyword**
- Mistral used `"preserve prior content"` in replace-scenario write actions
  (e.g. `"Rewrite notes.txt: preserve prior content and discard"`), triggering
  combine on explicit rewrites.
- The plan prompt example `"Update <file>: preserve prior content and add …"`
  was teaching models this phrasing.
- Fix: removed `"preserve prior"` from `_APPEND_KEYWORDS`; replaced the plan
  example with unambiguous append-only phrasings.

---

### Known model limitations (not production defects)

These failures are model-level structured-output weaknesses. The production
implementation (Claude Haiku) handles all three scenarios correctly. No
production changes are planned to accommodate these.

**mistral:7b-instruct — generate produces `null` tool_input for file_read**
- Planning is correct (file_read precedes file_write, append keyword in action).
- In the generate step, mistral emits `"tool_input": null` for file_read steps
  instead of `{"path": "..."}`. Execute calls file_read with path `""` →
  read_cache keyed to `""` → path mismatch with write step → combine does not
  fire → plain overwrite.
- Affects Scenarios A and B end-to-end. Scenario C (replace) is unaffected
  because combine is not expected to fire.
- Mistral passes all three scenarios at the planning layer and passes C
  end-to-end. It is treated as a partial robustness validator for the planning
  layer and replace semantics.

**llama3.2:3b — systematic path hallucination and poor structured-output compliance**
- Consistently generates wrong paths (`src/notes.txt`, `src/output.txt`) despite
  workspace artifacts showing bare filenames.
- Reverses step order in generate output; on replace scenarios invents unrelated
  steps ("Create project directory").
- All failures stem from the same root: the 3b model does not reliably follow
  structured JSON output instructions with multi-step plans.
- llama3.2:3b is below the reliability threshold for this workflow and is not
  treated as a robustness validator. Its results are recorded for reference only.

---

## Third milestone — Complete

**Status**: Complete. 173 tests passing. Live-validated 2026-03-20.

**Problem addressed:**

M1 and M2 gave the synthesis layer two separate operating modes with no
integration: when `filtered_sources` had content, `workspace_context` was
silently dropped; when `filtered_sources` was empty, only `workspace_context`
was used. A research query against a project with both files and task history
produced either a file-walk answer or a memory answer, never both.

**Deliverables as implemented:**

1. `app/graphs/research/prompts/templates.py` — `SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT`
   updated to explicitly name both source types (file sources and workspace history)
   and instruct the model to draw from both when both are present. Prior behaviour
   only mentioned conversation context; workspace history was present in the prompt
   but the model had no instruction to treat it as evidence.
2. `app/graphs/research/nodes/synthesize_evidence.py` — `source_log` entry added
   before the LLM call: `"combined"` when both filtered_sources and workspace_context
   are present; `"file source(s) only"` / `"workspace history only"` otherwise.
   Makes the active evidence path observable without reading the prompt.
3. `tests/graphs/research/test_nodes.py` — `test_synthesize_evidence_both_sources_present_in_prompt`:
   when both `filtered_sources` and `workspace_context` are populated, asserts
   that both file source content and workspace history appear in the same LLM
   prompt, and that the `"combined"` log entry is present.

No graph changes, no new record types, no WorkspaceStore changes.

**Live demo result (three-session validation):**

```
Session 1: "Create notes.txt with initial content"
  → execution, success, workspace record written

Session 2: "Create summary.txt with a summary of what this project has done"
  → execution, success, summary.txt created referencing notes.txt
  → workspace record written

Session 3: "Summarise what this project has done so far"
  → research, 7 evidence claims from 3 sources, confidence 0.45
  → file sources: notes.txt ("Day 1 / initial setup"), summary.txt
  → workspace history: creation date (2026-03-20), task descriptions,
    artifact names for both sessions
  → same response names both files AND references workspace creation
    records including timestamps not present in any file
```

**Observable M3 difference vs pre-M3:**

Pre-M3: with file sources present, workspace context was dropped. Response
would have named file content but had no knowledge of creation dates or task
provenance. Post-M3: both appear in the same answer. The `2026-03-20` date
and task descriptions in the Session 3 response came from workspace memory
only — they are not present in any file.
