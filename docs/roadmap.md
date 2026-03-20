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

### Milestone 2 — Memory-aware execution planning — *Not yet started*

The execution planner currently has no knowledge of prior artifacts. If the agent
created `notes.txt` in Session 1 and is asked to "append to notes.txt" in
Session 2, the planner does not know the file already exists. M2 will close this
gap by passing `workspace_context` into `create_execution_plan` so the LLM
planner can make informed decisions about existing files.

**Planned deliverables:**

- Pass `workspace_context` to `ExecutionState.context` via `wrappers.py`
- `create_execution_plan` includes prior artifact list in its system prompt
- Targeted test: planner uses `file_read` before `file_write` when a file
  already exists in workspace history
- Live demo: Session 1 creates `notes.txt`; Session 2 "add a line to notes.txt"
  produces a plan that reads before writing

No preference detection in M2. That is a separate milestone.

---

## V3 — Not yet scoped

V3 work will be planned after V2 is stable and validated. Likely themes
(not committed):

- HTTP retrieval (web search adapter)
- Append / patch file operations (beyond full overwrite)
- Broader tool set (evaluated based on V2 workspace memory learnings)

No V3 spec exists. It will not be written until V2 is complete.
