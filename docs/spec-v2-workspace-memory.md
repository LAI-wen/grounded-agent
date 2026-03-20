# Spec: V2 Workspace Memory

**Status**: Milestone 1 complete. Live-validated 2026-03-20.
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
