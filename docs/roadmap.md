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

**V2 leaves this gap:** after three milestones the agent can synthesise evidence
from multiple sources, but every answer is retrospective — it describes what has
been done. It has no ability to reason forward: "given what I know, here is a
concrete next step." Users who ask "what should I do next?" receive a general
research response, not an actionable recommendation.

**V3 closes that gap** at the prompt level, without new infrastructure.

---

### Milestone 1 — Next-step synthesis — *Not yet started*

**Problem M3.1 addresses:**

Research synthesis produces `evidence`, `citations`, `confidence`, and
`open_questions`. When the query is about project state or progress, the model
has all the information needed to suggest a next step — but there is no field
for it in the output and no instruction to produce one. The result is that
`open_questions` lists what is unknown but nothing surfaces what to *do* about it.

**Planned deliverables:**

1. `app/graphs/main/state.py` — add `suggested_next_step: Optional[str]` to
   `ResearchResult`. Populated only when the synthesis has sufficient evidence
   to make a concrete recommendation; `None` otherwise.

2. `app/graphs/research/prompts/templates.py` — extend
   `SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT` to instruct the model to produce a
   `suggested_next_step` in the JSON output when the query is about project
   state, progress, or "what's next". The instruction should be:
   - grounded in evidence (not generic advice)
   - concrete and actionable (a specific task, not a vague direction)
   - absent (`null`) when evidence is insufficient to suggest anything specific

3. `app/graphs/research/nodes/synthesize_evidence.py` — extract and pass through
   `suggested_next_step` from the LLM response.

4. `app/graphs/main/nodes.py` — `_compose_response` appends the suggestion to
   the response when present:
   `"Suggested next step: <suggestion>"` — clearly labelled, at the end.

5. Tests — one targeted test: when synthesis returns a `suggested_next_step`,
   it appears in the final response.

**Scope boundary:** no new graph nodes, no new WorkspaceStore record types, no
new tools. The suggestion is produced by the existing synthesis LLM call — not
a separate step. Changes to four files maximum.

**Why M1 before any tool expansion:** next-step reasoning is the highest-value
addition the agent can make with its current knowledge. It requires no new data
sources — the synthesis already has all the inputs it needs. Adding it before
expanding the tool surface ensures V3 is grounded in the current architecture
rather than in new infrastructure.

**Acceptance criterion:**

After two sessions (create files, append content), a third session asking
"What should I do next to make progress on this project?" produces a response
that names a specific artifact or task, not generic advice like "continue
documenting". The suggestion must be traceable to evidence in the response.

---

### Milestone 2 — Project state persistence — *Not yet started*

After M1, the agent can suggest a next step but cannot remember what it
previously suggested. Repeated "what's next?" queries synthesise from scratch
each time, producing potentially inconsistent recommendations across sessions.

**Problem:** there is no durable record of the agent's own forward-looking
reasoning — only retrospective task records. A user who acted on a suggestion
in Session 2 has no way to ask "what was the last thing you recommended?" in
Session 3.

**Planned deliverables:**

1. `app/memory/workspace.py` — new record type `project_state`:
   ```json
   {
     "type": "project_state",
     "timestamp": "...",
     "task_id": "...",
     "state_summary": "...",
     "suggested_next_step": "...",
     "source_task_ids": ["..."]
   }
   ```
   Written after a research turn that produced a `suggested_next_step`.

2. `WorkspaceStore.read_context()` — include the most recent `project_state`
   record in the injected context block.

3. Tests — one targeted test: after a research turn with a suggestion, the
   project_state record is readable from the store in the next session's context.

**Scope boundary:** no graph changes, no prompt changes beyond what M1 already
added. Only `workspace.py` and `WorkspaceStore` unit tests.

---

### Milestone 3 and beyond — *Not yet scoped*

Potential themes after M1 and M2 are stable:

- HTTP retrieval (web search adapter for research)
- Preference detection and recall across sessions
- Broader tool set (evaluated after V3 M1/M2 learnings)
