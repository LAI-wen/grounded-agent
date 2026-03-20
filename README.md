# Lobster Agent

A modular multi-agent assistant for research and file-based task execution,
built with LangGraph and the Anthropic API.

---

## Quickstart

```bash
cd lobster_agent
pip install -r requirements.txt

ANTHROPIC_API_KEY=sk-... python3 -m app.cli
```

---

## Architecture

```
User request
     │
     ▼
Main Graph
  normalize_task        — LLM classifies task type, extracts objective
  router                — determines which subgraphs to run
     │
     ├─ Research Subgraph        (task_type: research or hybrid)
     │    normalize_query → retrieve_sources → filter_sources
     │    → synthesize_evidence → finalize_research
     │
     ├─ Execution Subgraph       (task_type: execution or hybrid)
     │    create_execution_plan → safety_check → generate_actions
     │    → execute_actions
     │
     └─ Review Subgraph          (runs when execution ran)
          inspect_output → generate_verdict → compose_response
     │
     ▼
  finalize_response     — assembles user-facing reply from subgraph results
```

Each subgraph owns its own state. The main graph handles routing and
composes the final response from whichever subgraphs ran.

---

## V1 Capabilities

**Task classification**
- LLM-backed: classifies requests as `research`, `execution`, or `hybrid`
- Keyword heuristic fallback when API key is absent

**Research**
- Local file retrieval: walks the project directory (max depth 4), ranks
  files by query-term frequency, returns relevant excerpts
- LLM evidence synthesis with access to prior conversation history
- Evidence items surfaced directly in the response

**Execution**
- LLM-generated execution plan (`file_read` / `file_write` steps)
- LLM-generated file content for write steps
- Path boundary enforcement: all paths resolved within project root;
  path traversal and dangerous targets blocked before execution

**Review**
- Deterministic verdict: `pass` / `revise` / `fail` / `blocked`
- `blocked` when execution was safety-aborted (no work attempted)
- `fail` when execution ran and produced errors

**Thread continuity**
- SQLite checkpointer persists conversation across turns
- Prior messages restored at session start
- Conversation history injected into task classification and synthesis

---

## V2 Capabilities (Workspace Memory)

V2 gives the agent durable memory of its own work within a project directory,
so follow-up sessions can be informed by prior tasks without the user repeating
context.

**M1 — Cross-session memory store**

- Append-only JSONL at `<project_root>/.lobster/workspace_memory.jsonl`
- Two record types: `task_summary` (one per completed task) and `artifact`
  (one per file written by execution)
- Read at the start of each turn; last 5 task summaries injected as
  `workspace_context` into `MainState` before the graph runs
- `normalize_task` receives the context so classification is aware of prior work
- `synthesize_evidence` receives the context so research can answer "what files
  have you created?" even when the file walk returns nothing
- Write path fires only on success; retention-capped (50 tasks / 100 artifacts)

**M2 — Memory-aware execution planning**

- `workspace_context` injected into the execution planner so it knows which
  files already exist
- Planner emits `file_read` before `file_write` for known artifacts; write step
  action signals preservation semantics (`"append"`, `"add to existing"`)
- Generator produces only new content in APPEND mode; executor combines
  `read_cache` result with new content at runtime
- Prevents silent overwrites of known files when the task requests an update

**M3 — Unified research synthesis (file sources + workspace history)**

- `synthesize_evidence` treats workspace history as a first-class evidence
  source alongside file sources — both are drawn from in the same synthesis pass
- Previously: when file sources were present, workspace context was silently
  dropped. Now: both appear in the same response
- Evidence items like creation dates and task descriptions come from workspace
  history; file content and structure come from the file walk
- Observable difference: a "summarise the project" query after multiple sessions
  names both file content and when/why each file was created

**Memory store — what is and is not stored**

| Stored | Not stored |
|---|---|
| Task summaries (objective, status, verdict) | File contents |
| Artifact paths and operation type | API keys or credentials |
| Timestamps | Raw conversation messages |
| Most recent `suggested_next_step` (project_state) | Data from paths outside `project_root` |

The store is human-readable. Delete `<project_root>/.lobster/workspace_memory.jsonl`
to reset memory for that workspace; nothing else is affected.

---

## V3 Capabilities (Project-state awareness)

V3 gives the agent forward-looking reasoning: it can suggest a concrete next
step and remember what it previously recommended across sessions.

**M1 — Next-step synthesis**

- Research synthesis produces a `suggested_next_step` for progress/state/next-step
  queries; `None` for factual queries
- Suggestion is artifact-targeted (names a specific file + action), grounded in
  evidence from the same response, and one sentence maximum
- Appears in the final response as `"Suggested next step: ..."` — clearly labelled

**M2 — Project state persistence**

- After any research turn that produced a suggestion, a `project_state` record is
  written to the workspace store
- `read_context()` injects `"Most recent suggestion: ... (YYYY-MM-DD)"` into the
  next session's context block — one record, no accumulation
- Cross-session recall: "What did you recommend last time?" reads the stored
  suggestion directly rather than re-synthesising

**V3 demo transcript** (live-validated 2026-03-20):

```
Session 1 — new thread
  > "What should I do next to make progress on this project?"
  status: success  task_type: research  confidence: 0.65
  suggested_next_step: "Extend progress.txt with a detailed section
    outlining V3 M2 objectives, dependencies, and estimated timeline"
  project_state record written to .lobster/workspace_memory.jsonl

Session 2 — new thread
  > "What did you recommend last time?"
  workspace_context carries:
    "Most recent suggestion: Extend progress.txt with a detailed section
     outlining V3 M2 objectives... (2026-03-20)"
  status: success  task_type: research  confidence: 0.95  ← stored record
  evidence: "The suggested next step from that research task was: 'Extend
    progress.txt with a detailed section...'"  ← quoted verbatim from store
```

Key V3 properties:
- Session 2 confidence rises from 0.65 → 0.95: stored record removes uncertainty
- Open questions narrow from "what should we do?" to "how exactly do we do it?"
- Response references stored suggestion, not a re-synthesised recommendation

---

## V4 Capabilities (External Retrieval)

V4 adds web search to the research pipeline via the Tavily API. When
`TAVILY_API_KEY` is set, `search_sources()` runs both local file retrieval
and web retrieval in parallel (sequentially but always attempted), merging
results into a single ranked list.

**Adapter design**

- `LocalFileRetrievalAdapter` — always runs (max 10 results when combined with web)
- `WebSearchAdapter` — runs when `TAVILY_API_KEY` is set (max 5 results, 8s timeout)
- Web failures are silent: local-only results returned if the Tavily call fails
- Passing an explicit `adapter=` to `search_sources()` preserves backward
  compatibility for tests and the legacy HTTP adapter

**Source schema additions**

Web sources are distinguished from local sources by their metadata:

```python
{
    "url":               "https://example.com/page",   # always populated for web
    "title":             "...",
    "content":           "...",                         # capped at 1000 chars
    "reliability_score": 0.88,                         # from Tavily score or 0.7 default
    "metadata": {
        "adapter":  "web_search",
        "provider": "tavily",
        "rank":     1,
    }
}
```

**V4 demo** (validated 2026-03-20, mocked Tavily response):

```
search_sources("lobster agent research", TAVILY_API_KEY=set)
  → 10 local_file sources  (LocalFileRetrievalAdapter)
  →  2 web_search sources  (WebSearchAdapter, Tavily API)
  combined: 12 sources, ranked by filter_sources

search_sources("lobster agent research", TAVILY_API_KEY=absent)
  → 1 local_file source only
  → requests.post NOT called
```

**Configuration**

```bash
# Enable web search (add alongside ANTHROPIC_API_KEY)
export TAVILY_API_KEY=tvly-...
```

No additional Python dependencies — Tavily is called via `requests` (already in requirements.txt).

---

## Validated Workflows

All six V1 scenarios pass live validation (`lobster_agent/scripts/validate_live.py`):

| Scenario | Workflow type | Result |
|---|---|---|
| Create a file | execution | File written, verdict `pass` |
| Path traversal attempt | execution | Blocked before execution, verdict `blocked` |
| Research + write to file | hybrid | Research runs, file written, verdict `pass` |
| Pure research query | research | Evidence synthesised, confidence reported |
| Ambiguous request | hybrid | Routes to hybrid, partial result |
| Multi-turn contextual follow-up | research | Answer grounded in prior conversation |

**V2 demo transcript** (three sessions, same project root, live-validated):

```
Session 1 — new thread
  > Create notes.txt with the content:
    "Project started. Initial notes added on day one."
  status: success  task_type: execution
  notes.txt written
  workspace record: task_summary + artifact(notes.txt)

Session 2 — new thread
  > Append "V2 milestone reached." to notes.txt
  status: success  task_type: execution
  planner: file_read(notes.txt) → file_write(notes.txt)   ← M2: artifact-aware
  executor: combines read result + new content
  notes.txt: "Project started. Initial notes added on day one.
              V2 milestone reached."
  workspace record: task_summary + artifact(notes.txt)

Session 3 — new thread
  > Summarise what this project has done so far.
  status: success  task_type: research
  synthesis: 7 evidence claims from 3 sources             ← M3: combined evidence
    • "A project was initialized on 2026-03-20"            ← from workspace history
    • "Initial notes added on day one"                     ← from notes.txt content
    • "notes.txt and summary.txt were created"             ← from workspace history
    • "V2 milestone reached"                               ← from notes.txt content
    confidence: 0.45
```

Key V2 properties visible in the transcript:
- Session 2 reads notes.txt before writing, preserving prior content (M2)
- Session 3 names both file content and creation provenance in one response (M3)
- Session 3 workspace context carries creation dates not present in any file (M3)

---

## CLI

```bash
cd lobster_agent
ANTHROPIC_API_KEY=sk-... python3 -m app.cli [--project-root PATH] [--thread-id ID]
```

| Option | Description |
|---|---|
| `--project-root PATH` | Working directory for file operations (default: cwd) |
| `--thread-id ID` | Resume a named conversation thread (default: new session) |

The CLI maintains a single thread across the session. Use `--thread-id` to
resume a previous conversation.

---

## Programmatic Usage

```python
from app.main import run_lobster_agent
from app.memory.checkpointer import create_checkpointer

checkpointer = create_checkpointer()

result = run_lobster_agent(
    "Create hello.txt with the content Hello World",
    thread_id="my-thread",
    checkpointer=checkpointer,
)
print(result["status"])     # success
print(result["task_type"])  # execution
```

---

## Running Tests

```bash
cd lobster_agent
pytest tests/ -q
```

179 tests, no external dependencies required.

---

## Project Structure

```
lobster_agent/
├── app/
│   ├── cli.py                  # Interactive CLI entry point
│   ├── main.py                 # run_lobster_agent()
│   ├── graphs/
│   │   ├── main/               # Parent graph: routing + orchestration
│   │   ├── research/           # Research subgraph
│   │   ├── execution/          # Execution subgraph
│   │   └── review/             # Review subgraph
│   ├── memory/
│   │   ├── checkpointer.py     # SQLite thread checkpointer
│   │   └── workspace.py        # WorkspaceStore — per-project task/artifact log
│   ├── schemas/                # Shared types: Artifact, Error, TraceEntry
│   └── tools/                  # file_read / file_write (project-scoped)
├── scripts/
│   ├── validate_live.py        # V1 live validation — 6 workflow scenarios
│   ├── validate_m2_ollama.py   # M2 cross-model robustness check (Ollama)
│   ├── validate_m3_live.py     # V2 M3 live validation — 3-session demo
│   ├── validate_v3m1_live.py   # V3 M1 live validation — suggestion quality
│   ├── validate_v3m2_live.py   # V3 M2 live validation — cross-session recall
│   └── validate_v4_live.py     # V4 live validation — external retrieval
└── tests/
    ├── graphs/                 # Subgraph and integration tests
    └── memory/                 # WorkspaceStore unit tests
```

---

## Known Limitations

- **Tools**: `file_read` and `file_write` only — no shell commands, no web
  requests, no API calls from within execution
- **Retrieval**: local file walk + optional web search (Tavily) — no other HTTP sources
- **File writes**: full overwrite only — no append semantics
- **Workspace memory**: task summaries, artifact paths, and most recent suggestion —
  no preference detection (not yet implemented)
- **Memory inspection**: no CLI command to view or clear workspace memory —
  inspect via `<project_root>/.lobster/workspace_memory.jsonl` directly
- **Output**: blocking, synchronous — no streaming
- **Deployment**: local only — no server or authentication layer

---

## Tech Stack

- Python 3.9+
- [LangGraph](https://github.com/langchain-ai/langgraph) — graph orchestration and checkpointing
- [Anthropic API](https://docs.anthropic.com) — `claude-haiku-4-5-20251001` for all LLM calls
- SQLite — conversation thread persistence via LangGraph MemorySaver
- pytest — 191 unit and integration tests
