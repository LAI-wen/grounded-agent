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

### What V2 will deliver

A file-backed workspace memory store that:

- persists a record of completed tasks (what ran, what was produced)
- retains workspace-scoped preferences stated by the user
- tracks files the agent has created or read within the project root
- injects relevant prior context into task classification and execution planning

Workspace memory is scoped to a single project directory. It is not a
cross-project or user-profile store. It does not require vector search.

### V2 spec

See [`spec-v2-workspace-memory.md`](spec-v2-workspace-memory.md).

---

## V3 — Not yet scoped

V3 work will be planned after V2 is stable and validated. Likely themes
(not committed):

- HTTP retrieval (web search adapter)
- Append / patch file operations (beyond full overwrite)
- Broader tool set (evaluated based on V2 workspace memory learnings)

No V3 spec exists. It will not be written until V2 is complete.
