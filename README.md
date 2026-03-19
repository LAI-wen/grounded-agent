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

## Validated Workflows

All six scenarios pass live validation (`lobster_agent/scripts/validate_live.py`):

| Scenario | Workflow type | Result |
|---|---|---|
| Create a file | execution | File written, verdict `pass` |
| Path traversal attempt | execution | Blocked before execution, verdict `blocked` |
| Research + write to file | hybrid | Research runs, file written, verdict `pass` |
| Pure research query | research | Evidence synthesised, confidence reported |
| Ambiguous request | hybrid | Routes to hybrid, partial result |
| Multi-turn contextual follow-up | research | Answer grounded in prior conversation |

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

150 tests, no external dependencies required.

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
│   ├── memory/                 # SQLite checkpointer
│   ├── schemas/                # Shared types: Artifact, Error, TraceEntry
│   └── tools/                  # file_read / file_write (project-scoped)
├── scripts/
│   └── validate_live.py        # Live validation (requires API key)
└── tests/
```

---

## Known Limitations (V1)

- **Tools**: `file_read` and `file_write` only — no shell commands, no web
  requests, no API calls from within execution
- **Retrieval**: local file walk only — no HTTP sources
- **File writes**: full overwrite only — no append semantics
- **Memory**: conversation history within a thread only — no cross-thread
  or long-term user memory
- **Output**: blocking, synchronous — no streaming
- **Deployment**: local only — no server or authentication layer

---

## Tech Stack

- Python 3.9+
- [LangGraph](https://github.com/langchain-ai/langgraph) — graph orchestration and checkpointing
- [Anthropic API](https://docs.anthropic.com) — `claude-haiku-4-5-20251001` for all LLM calls
- SQLite — conversation thread persistence via LangGraph MemorySaver
- pytest — 150 unit and integration tests
