# Lobster Agent — Roadmap

---

## V1 — Complete

**Theme**: End-to-end multi-agent pipeline, locally validated.

**Status**: Complete. 150 tests passing. Live-validated across 6 workflow types.

### What V1 delivers

- Parent graph + research / execution / review subgraphs
- LLM task classification (`normalize_task`) with keyword heuristic fallback
- Local file retrieval: ranked by query-term frequency across the project directory
- LLM-backed execution planning (`file_read` / `file_write`, safety-enforced)
- Deterministic review verdict: `pass` / `revise` / `fail` / `blocked`
- Thread continuity: SQLite checkpointer, messages carried across turns
- Conversation history injected into task classification and research synthesis
- Interactive CLI (`python3 -m app.cli`) with `--project-root` and `--thread-id`

### V1 boundaries (intentional)

- Tools limited to `file_read` and `file_write` — no shell, no web
- Retrieval limited to local file walk — no HTTP sources
- Memory limited to the current conversation thread — no cross-session persistence
- Single-user, local only

---

## V2 — Workspace Memory

**Theme**: The agent remembers what happened in a project across sessions.

### Problem V2 addresses

V1 carries context within a single conversation thread. But every new session
starts blind. If the agent created `hello.txt` in a prior session, a new session
has no knowledge of it. If the user expressed a preference ("I prefer TypedDict
over Pydantic") in a prior thread, the agent cannot act on it the next day.

The agent needs a lightweight durable record of its workspace: what it has
created, what preferences have been expressed, and what recent tasks have done.
This is the foundation for genuinely contextual behaviour across sessions.

### V2 spec

See [`spec-v2-workspace-memory.md`](spec-v2-workspace-memory.md).

---

### Milestone 1 — Read/write loop — **Complete**

**Status**: Complete. 165 tests passing. Live-validated with two-session demo.

**What M1 delivers:**

- `app/memory/workspace.py` — `WorkspaceStore` with `read_context()` and `write_task_summary()`
- Append-only JSONL at `<project_root>/.lobster/workspace_memory.jsonl`
- Two record types: `task_summary` and `artifact`
- Read path: `workspace_context` injected into `MainState` before graph runs
- `normalize_task` receives `workspace_context` — cross-session task classification
- `synthesize_evidence` receives `workspace_context` via research context dict — can answer "what files have you created?" from memory even when file walk returns nothing
- Write path: fires only on `status == "success"` or verdict `== "pass"`
- Guards: 1500-char token cap, path normalisation, retention trimming (50 tasks / 100 artifacts)

**Live demo result:**

```
Session 1: "Create hello.txt" → success, 2 store records written
Session 2: "What files have you created?" → "hello.txt" named explicitly, 0.95 confidence
```

---

### Milestone 2 — Memory-aware execution planning — **Complete**

**Status**: Complete. 172 tests passing. Live-validated with two-session demo.

**What M2 delivers:**

- `wrappers.py` — `workspace_context` passed into `ExecutionState.context`
- `plan.py` — formats workspace context as a "Known existing artifacts" block
  in the planner user prompt; excluded from the generic context string
- `PLAN_EXECUTION_SYSTEM_PROMPT` — added rules: prefer `file_read` before
  `file_write` for known artifacts; write step `action` must signal preservation
  semantics ("Append X to existing Y", "preserve prior content and add …")
- `GENERATE_ACTIONS_SYSTEM_PROMPT` — distinguishes APPEND mode (generate new
  content only; execute combines) from REPLACE mode (generate full content)
- `execute.py` — `read_cache` tracks `file_read` results per path; for
  `file_write` steps whose action contains an append keyword, combines
  `read_result + new_content` before writing; plain replace semantics unaffected
- 7 new tests: workspace context in prompt, artifacts block absent without
  context, preserve/replace prompt assertions, unit tests for combine and
  no-combine paths, full graph integration

**Live demo result:**

```
Session 1: "Create notes.txt with 'Project started. Initial notes.'"
  → success, notes.txt written, 2 store records

Session 2: "Append 'V2 milestone reached.' to notes.txt"
  → file_read before file_write (workspace artifact awareness)
  → execute combines: original content preserved + new line appended
  → notes.txt contains both lines
```

---

### Milestone 3 — Workspace-aware research continuation — *Not yet started*

M1 and M2 address the write side: what was created and how to continue it.
M3 addresses the read side: when the agent is asked to research or summarise
the project's current state, it should produce answers that are grounded in
both the file system and the workspace history, not just whichever one happens
to have content.

**Problem M3 addresses:**

Research synthesis currently falls into two separate modes with no integration:
- When `filtered_sources` has content, synthesis ignores `workspace_context`
- When `filtered_sources` is empty, synthesis uses only `workspace_context`

The result is that a query like "Summarise what this project has done so far"
produces either a file-walk answer or a memory answer, but never both together.
A user who created `notes.txt` and also ran several tasks sees only one half
of the picture per session.

**Planned deliverables:**

- `synthesize_evidence` always incorporates `workspace_context` when present,
  regardless of whether `filtered_sources` is empty or populated
- The synthesis prompt positions workspace history as complementary context
  to file sources, not a fallback for when they are absent
- Targeted test: synthesis with both `filtered_sources` and `workspace_context`
  produces evidence from both sources in the same response
- Live demo: after two sessions of file creation, a "summarise the project"
  query names both files and their creation dates from workspace history

**Scope boundary:** no new record types, no new graph nodes, no changes to
`WorkspaceStore`. Only the synthesis prompt and early-return logic.

---

## V3 — Not yet scoped

V3 work will be planned after V2 is stable and validated. Likely themes
(not committed):

- HTTP retrieval (web search adapter)
- Append / patch file operations (beyond full overwrite)
- Broader tool set (evaluated based on V2 workspace memory learnings)

No V3 spec exists. It will not be written until V2 is complete.
