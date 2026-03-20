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

### Milestone 2 — Memory-aware execution planning — **Complete + robustness validated**

**Status**: Complete. 172 tests passing. Live-validated with two-session demo.
Cross-model robustness check complete (2026-03-20).

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

**M2 robustness findings (cross-model check, 2026-03-20):**

Production defects found and fixed during Ollama validation:
- Over-broad append keywords (`"based on file_read"`, `"preserve prior"`) triggered
  combine logic on explicit replace scenarios — removed from `_APPEND_KEYWORDS`
  and from plan prompt examples.
- Double braces in `GENERATE_ACTIONS_SYSTEM_PROMPT` (Python format escapes applied
  to a string that is never formatted) caused weaker models to mimic `{{` in output,
  producing invalid JSON — corrected to single braces.

Model results:
- `mistral:7b-instruct`: planning passes all scenarios; replace passes end-to-end.
  Append/extend E2E failures are generate-layer model weaknesses (null tool_input
  for read steps), not production defects.
- `llama3.2:3b`: below reliability threshold (path hallucination, poor structured-
  output compliance). Documented as known small-model limitation; no production
  changes planned to accommodate.

Decision boundary: production complexity will not be added to accommodate weak
local models unless the same failure pattern appears on stronger models.

---

### Milestone 3 — Workspace-aware research continuation — **Complete**

M1 and M2 address the write side: what was created and how to continue it.
M3 addresses the read side: when the agent is asked to research or summarise
the project's current state, it should produce answers that are grounded in
both the file system and the workspace history, not just whichever one happens
to have content.

**Problem M3 addresses:**

Research synthesis currently runs in two separate modes with no integration:
- When `filtered_sources` has content, synthesis ignores `workspace_context`
- When `filtered_sources` is empty, synthesis uses only `workspace_context`

A query like "Summarise what this project has done so far" produces either a
file-walk answer or a memory answer — never both. A user who created `notes.txt`
across two sessions and then asks for a summary sees only one half of the picture.

**Planned deliverables:**

- `synthesize_evidence` always incorporates `workspace_context` when present,
  regardless of whether `filtered_sources` is empty or populated
- The synthesis prompt positions workspace history as complementary context
  to file sources, not a fallback for when they are absent
- Targeted test: synthesis with both `filtered_sources` and `workspace_context`
  populated produces evidence drawn from both in the same response
- Live demo: after two sessions of file creation, a "summarise the project"
  query names both files and references workspace history for creation dates

**Scope boundary:** no new record types, no new graph nodes, no changes to
`WorkspaceStore`. Changes limited to `synthesize_evidence.py` and the research
synthesis prompt template. Two files touched maximum.

**Why M3 before V3:** the research/memory integration gap is an existing
architectural seam (noted at M1) that affects answer quality without requiring
new tools or new infrastructure. Closing it rounds out V2 before any expansion
of the tool surface.

**Status**: Complete. 173 tests passing. Live-validated 2026-03-20.

**Live demo result:**

```
Session 3: "Summarise what this project has done so far"
  → research, 7 evidence claims from 3 sources (notes.txt, summary.txt,
    workspace history), confidence 0.45
  → creation date (2026-03-20) and task descriptions drawn from workspace
    history only — not present in any file
  → file content and workspace memory both appear in the same response
```

---

## V3 — Project-state awareness and next-step reasoning

**Theme**: The agent understands the current state of a project and can suggest
what to do next, grounded in both file content and workspace history.

**Status**: Complete. 179 tests passing. Live-validated 2026-03-20.

---

### Milestone 1 — Next-step synthesis — **Complete**

**Status**: Complete. 176 tests passing. Live-validated 2026-03-20.

**What M1 delivers:**

- `app/graphs/research/state.py` — `suggested_next_step: Optional[str]` added to `ResearchState`
- `app/graphs/main/state.py` — `suggested_next_step: Optional[str]` added to `ResearchResult`
- `app/graphs/research/prompts/templates.py` — synthesis prompt extended with trigger
  condition (progress/state/next-step queries only), concreteness rules (artifact-targeted,
  one sentence, grounded in evidence), and `suggested_next_step` in JSON schema
- `app/graphs/research/nodes/synthesize_evidence.py` — extracts and passes through
  `suggested_next_step` from LLM response
- `app/graphs/main/wrappers.py` — maps field into `ResearchResult`
- `app/graphs/main/nodes.py` — `_compose_response` appends `"Suggested next step: ..."`
  when present; absent for factual queries
- 3 new tests: suggestion present for progress query, null for factual query,
  rendering in final response

**Live demo result:**

```
Query A (project-state): "Summarise the current state of this project."
  → suggested_next_step: "Extend progress.txt with details on the next-step
    synthesis implementation plan and live validation requirements."
  → grounded in evidence; not generic; appears in final response

Query B (next-step): "What should I do next to make progress on this project?"
  → suggested_next_step: "Extend progress.txt with the live validation
    requirements and detailed next-step synthesis implementation plan to
    unblock the pending validation phase."

Query C (factual): "How does the safety check in this project work?"
  → suggested_next_step: None  ← correctly absent
  → "Suggested next step:" not in response
```

---

### Milestone 2 — Project state persistence — **Complete**

**Status**: Complete. 179 tests passing. Live-validated 2026-03-20.

**What M2 delivers:**

- `app/memory/workspace.py` — new record type `project_state`; `write_project_state()`
  method; `read_context()` appends `"Most recent suggestion: ... (YYYY-MM-DD)"` when
  a record is present; `_trim()` enforces `MAX_PROJECT_STATE_RECORDS = 1`
- `app/main.py` — calls `write_project_state()` after any research turn where
  `suggested_next_step` is non-None
- 3 new tests: cross-session recall, last-1 retention, absent when no record written

**Record shape:**
```json
{
  "type": "project_state",
  "timestamp": "2026-03-20T12:18:17Z",
  "task_id": "...",
  "suggested_next_step": "Extend progress.txt with a detailed section outlining V3 M2 objectives..."
}
```

**`read_context()` rendering:**
```
Workspace history (recent tasks):
- [2026-03-20] research: "What should I do next..." → success
Most recent suggestion: Extend progress.txt with a detailed section... (2026-03-20)
```

**Live demo result:**

```
Session 1: "What should I do next to make progress on this project?"
  → suggestion produced, project_state record written, confidence: 0.65

Session 2 (new thread): "What did you recommend last time?"
  → workspace_context carries: "Most recent suggestion: Extend progress.txt
    with a detailed section outlining V3 M2 objectives... (2026-03-20)"
  → evidence claim quotes stored suggestion verbatim
  → confidence: 0.95  ← stored record removes re-synthesis uncertainty
  → response references stored suggestion, not a new recommendation
```

**Observable difference vs pre-M2:** before M2, "what did you recommend last
time?" would re-synthesise from file sources only with no record of a prior
recommendation. After M2, the stored suggestion appears directly in evidence,
confidence rises from 0.65 to 0.95, and open questions narrow from
"what should we do?" to "how exactly should we do it?"
