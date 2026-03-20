# Lobster Agent — Developer Reference

See the [project README](../README.md) for architecture, capabilities, and
validated workflow documentation.

---

## Install

```bash
pip install -r requirements.txt
```

## Run the CLI

```bash
ANTHROPIC_API_KEY=sk-... python3 -m app.cli
ANTHROPIC_API_KEY=sk-... python3 -m app.cli --project-root /path/to/project
ANTHROPIC_API_KEY=sk-... python3 -m app.cli --thread-id my-session
```

## Run Tests

```bash
pytest tests/ -q              # all 221 tests, no API key needed
pytest tests/ -v              # verbose output
pytest tests/graphs/test_main.py -v   # specific module
```

## Live Validation

Requires `ANTHROPIC_API_KEY`:

```bash
ANTHROPIC_API_KEY=sk-... python3 scripts/validate_live.py
```

Runs 6 workflow scenarios and prints pass/fail for each.

## Programmatic Usage

```python
from app.main import run_lobster_agent
from app.memory.checkpointer import create_checkpointer

result = run_lobster_agent("Research how this project handles safety checks")
print(result["status"])

# Multi-turn session (thread continuity via checkpointer)
cp = create_checkpointer()
r1 = run_lobster_agent("Create notes.txt", thread_id="t1", checkpointer=cp)
r2 = run_lobster_agent("What file did I just ask you to create?", thread_id="t1", checkpointer=cp)
```

Workspace memory is automatic. Any successful task writes a record to
`<cwd>/.lobster/workspace_memory.jsonl`. On the next call (any thread),
prior task summaries are injected as context before the graph runs.

To use a specific project root rather than the current directory, pass it via
`os.chdir()` before calling `run_lobster_agent`, or use the CLI `--project-root`
flag.
