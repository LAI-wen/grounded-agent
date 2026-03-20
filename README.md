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

**Workspace memory store** (`app/memory/workspace.py`)
- Append-only JSONL file at `<project_root>/.lobster/workspace_memory.jsonl`
- Two record types: `task_summary` (one per completed task) and `artifact`
  (one per file written by the execution subgraph)
- Scoped strictly to the current project root — no cross-project sharing
- Human-readable and user-deletable; removing the file resets memory with no
  other effect on the agent

**Read path — start of each turn**
- `WorkspaceStore.read_context()` reads the last 5 task summaries and formats
  them into a compact `workspace_context` block
- Capped at 1500 characters to protect token budget
- Injected into `MainState.workspace_context` before the graph runs

**Context injection**
- `normalize_task` receives `workspace_context` so task classification knows
  what the agent has previously done in this project
- `synthesize_evidence` receives `workspace_context` so research synthesis can
  answer questions like "what files have you created?" even when no matching
  local files are found by the file walk

**Write path — end of each turn**
- Written only on `status == "success"` or review `verdict == "pass"`
- Artifact paths normalised relative to project root; paths outside root are
  silently dropped
- Retention limits: 50 task summaries, 100 artifact records (older entries
  trimmed on write)

**What is not stored**
- File contents (paths and metadata only)
- API keys or credentials
- Raw conversation messages (those belong to the thread checkpointer)
- Data from paths outside `project_root`

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

V2 M1 two-session demo (live-validated):

| Session | Request | Result |
|---|---|---|
| 1 | Create hello.txt | File written, store records `task_summary` + `artifact` |
| 2 (new thread) | What files have you created? | Answers `hello.txt` from workspace history, 0.95 confidence |

V2 M2 two-session demo (live-validated):

| Session | Request | Result |
|---|---|---|
| 1 | Create notes.txt with initial content | File written, store records written |
| 2 (new thread) | Append new line to notes.txt | `file_read` before `file_write`; original content preserved; new line appended |

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

172 tests, no external dependencies required.

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
│   └── validate_live.py        # Live validation (requires API key)
└── tests/
    ├── graphs/                 # Subgraph and integration tests
    └── memory/                 # WorkspaceStore unit tests
```

---

## Known Limitations

- **Tools**: `file_read` and `file_write` only — no shell commands, no web
  requests, no API calls from within execution
- **Retrieval**: local file walk only — no HTTP sources
- **File writes**: full overwrite only — no append semantics
- **Workspace memory**: task summaries and artifact paths only — no preference
  detection, no research/workspace integration when both file sources and
  workspace history are present (planned V2 M3)
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
- pytest — 172 unit and integration tests
