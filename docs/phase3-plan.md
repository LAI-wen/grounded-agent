# Phase 3 Plan — Lobster Agent

> Status: Planning only. No implementation has started.
> Prerequisites: Phase 2A, 2B, 2C complete. 119 tests passing.

---

## 1. What the System Can Already Do

The system has a complete, tested three-subgraph pipeline:

**Entry point**
- `run_lobster_agent(user_request, thread_id, use_checkpointer)` in `app/main.py`
- Compiles the Parent Graph with optional SQLite or in-memory checkpointer
- Thread-scoped state persistence via `thread_id`

**Parent Graph**
- `normalize_task`: classifies request as `research` / `execution` / `hybrid` via keyword heuristic; populates `NormalizedTask` (objective, constraints, requested_outputs, assumptions, priority)
- `router`: maps `task_type` → ordered `required_subgraphs` list; advances through list step by step
- `finalize_response`: marks `status` as `success` / `partial` based on which subgraph results are present
- `handle_error`: catches `status == "failed"` and writes a trace entry

**Research Subgraph** (Phase 2A)
- `normalize_query`: LLM-backed query decomposition → structured sub-queries
- retrieval adapters: stub implementations (no real web/doc retrieval)
- `synthesize_evidence`: LLM-backed synthesis → `ResearchResult` with summary, evidence, citations, confidence, open_questions

**Execution Subgraph** (Phase 2B)
- `plan`: LLM produces ordered `ExecutionStep` list (file_read / file_write only)
- `generate`: LLM fills `tool_input` per step
- `simulate`: dry-run — checks file existence, estimates write size, no writes
- `safety_check`: path boundary enforcement (`relative_to`), tool allowlist, protected-file patterns; populates `SafetyFlag` list; `passed=False` routes to END via conditional edge (aborting execute)
- `execute`: real file I/O via `file_ops.py`; produces typed `ExecutionOutput` with `actions_taken`, `artifacts`, `logs`, `success`, `errors`

**Review Subgraph** (Phase 2C)
- `inspect`: deterministic structural rules (execution_result_present, execution_success, no_errors, artifacts_present, artifact_fields_complete, logs_non_empty) + LLM quality check (skipped on critical failures, skipped without API key)
- `verdict`: deterministic — critical→fail, major→revise, minor/none→pass; enumerates issue descriptions in `review_notes`
- `assemble`: deterministic — builds `final_response` string from validated content; `approved_artifacts` = all (pass), structurally complete only (revise), none (fail)

**Test coverage**: 119 tests passing. All subgraph internals are unit-tested. Integration tests cover each subgraph in isolation.

---

## 2. Gaps vs. a Genuinely Useful Personal Assistant

### 2.1 Keyword-based task classification breaks on natural language

`normalize_task` in the Parent Graph is a stub. It uses simple word-matching: "research"/"find"/"search" → research; "execute"/"run"/"create"/"implement"/"build" → execution. Everything else defaults to `hybrid`.

- "Help me think through the design of this module" → hybrid (correct by luck)
- "Can you look at this file and improve it?" → hybrid (correct by accident — "look" isn't in the list)
- "Write a summary of what this project does" → hybrid (no keyword matches "execute" or "research")
- "Make a file called notes.txt" → execution (`create` matches) — correct
- Priority, constraints, and assumptions are never populated from the request

Classification errors corrupt everything downstream: wrong subgraphs run, `requested_outputs` is always `["response"]`, and constraints are never applied.

### 2.2 Research retrieval is a stub

All three retrieval adapters in the Research Subgraph return empty or placeholder data. Research tasks produce structurally valid `ResearchResult` objects but semantically empty ones. The system cannot answer a real research question.

### 2.3 No user-facing response assembly

`finalize_response` marks `status` but does not produce a coherent user-facing answer. `main.py` prints raw dict fields: task ID, status, research summary string, execution success boolean, review `final_response`. There is no composed reply, no conversational output, and no formatting.

### 2.4 No conversation continuity in practice

The checkpointer is wired and `thread_id` is supported, but:
- `messages: list[Message]` in `MainState` is never written by any node
- `finalize_response` does not append an assistant message
- A second call with the same `thread_id` restores graph state but has no memory of what was said
- Thread continuity is structurally plumbed but semantically empty

### 2.5 No memory across sessions

No user profile, no preference store, no prior-task history beyond what LangGraph checkpoints. Every new `thread_id` is a blank slate. The assistant cannot learn "I prefer functional-style Python" or "my project root is ~/projects/myapp".

### 2.6 Execution scope limited to file I/O

The execution subgraph only supports `file_read` and `file_write`. Tasks requiring shell commands, web requests, or API calls cannot be completed. The LLM planner will produce steps with these tools, but the safety check will abort them as `forbidden_tool`.

### 2.7 No integration test for the full pipeline

Subgraphs are tested in isolation. The Parent Graph's `normalize_task → router → [subgraph chain] → finalize` flow has no end-to-end test that verifies subgraph output maps correctly back into `MainState`, routing advances correctly, and `finalize_response` produces a usable result.

### 2.8 Minor structural gaps

- `next_step` field in `MainState` is defined but never written (observability gap)
- `plan` field is `Optional[str]` with no structured schema or population
- `priority` in `NormalizedTask` is typed as `Optional[str]` with a TODO to use `Literal["low", "medium", "high"]`
- `evidence` in `ResearchResult` is `list[str]` with a TODO to upgrade to a structured schema
- `issues` in `ReviewResult` is `list[str]` (not `list[ReviewIssue]`) — loses severity/location

---

## 3. Top 5 Next Priorities

### Priority 1 — Integration hardening

**What**: Verify and repair the full end-to-end pipeline. Run a real request through `normalize_task → router → execution subgraph → review subgraph → finalize`. Confirm:
- Subgraph outputs map correctly into `MainState` (`execution_result`, `review_result`, `artifacts`)
- `finalize_response` composes a user-facing answer from whichever subgraphs ran
- `messages` list is populated with at minimum the user turn and a final assistant response
- The CLI prints something coherent to the user

**Why first**: The subgraphs have been built in isolation. Integration is unverified. All higher-level work (memory, UX, retrieval) is harder to validate and debug if the core pipeline is broken.

**Scope**:
- Fix `finalize_response` to compose a real response from subgraph results
- Populate `messages` field at task start (user turn) and task end (assistant turn)
- Write at least two end-to-end integration tests: one execution task, one research task
- Do not change subgraph internals

### Priority 2 — `normalize_task` LLM upgrade

**What**: Replace the keyword heuristic with an LLM-backed implementation. The LLM should:
- Classify `task_type` from natural language
- Extract `objective`, `constraints`, `requested_outputs`, `assumptions`, and `priority` from the user request
- Return structured JSON; fall back to keyword heuristic if no API key

**Why**: Brittle classification is the root cause of most foreseeable routing failures. Every downstream subgraph relies on what `normalize_task` produces.

**Scope**:
- Replace keyword logic in `nodes.py`
- Add prompt template in `app/graphs/main/prompts/` (create directory)
- Preserve existing fallback path for no-API-key operation
- Add unit tests for `normalize_task`; do not change router or subgraphs

### Priority 3 — CLI / UX layer

**What**: Make the system interactive and usable for real manual testing. Minimum:
- Conversation loop: accepts multiple turns with the same `thread_id`
- Formatted output: final response displayed as prose, not raw dict
- `--project-root` flag: lets the user specify the working directory for execution tasks
- `--thread-id` flag: resume a named conversation
- Graceful exit and error display

**Why**: Without a usable interface, testing requires writing Python scripts. This blocks evaluation of everything else.

**Scope**: `app/main.py` and a new `app/cli.py`. No changes to graphs or subgraphs.

### Priority 4 — Research retrieval (at least one real adapter)

**What**: Implement at least one real retrieval adapter. Options:
- **File-based**: search the local project for relevant content (grep-style, reads files under `project_root`)
- **Web search**: integrate a search API (Tavily, Brave, or similar) behind an adapter interface

File-based retrieval is the safer starting point: no external API dependency, works offline, useful for "explain what this project does" tasks.

**Why**: Research tasks currently produce empty results. Hybrid tasks (research → execution) are broken as a result.

**Scope**: Implement one retrieval adapter. Do not change `normalize_query` or `synthesize_evidence`. The adapter interface already exists.

### Priority 5 — Memory layer (user profile + task history)

**What**: A lightweight, file-backed memory store (not the LangGraph checkpointer) that persists:
- User preferences (code style, language, verbosity preference)
- Project context (default `project_root`, known file structure)
- Recent task summaries (last N task IDs, verdicts, artifact paths)

Memory should be read at task start (injected into context) and written at task end (after `finalize_response`).

**Why**: Without memory, the assistant starts from zero every session. Even simple preferences ("I use Python 3.11") cannot be retained.

**Scope**: New `app/memory/store.py`. Read in `normalize_task`, write in `finalize_response`. Do not couple to subgraphs.

---

## 4. Proposed End-to-End Demo Scenarios

These are concrete requests that should work after the top 5 priorities are complete.

### Demo A — Simple file execution (after Priority 1)
```
Request: "Create a file called hello.txt with the content 'Hello from Lobster Agent'"
Expected path: execution → review
Expected outcome: file created, review passes, final_response names the artifact
```
Validates: execution subgraph → review subgraph → finalize → user sees coherent output.

### Demo B — Natural language classification (after Priority 2)
```
Request: "I need you to help me organize my notes into a summary document"
Expected: task_type = "execution", constraints include "organize", requested_outputs = ["summary document"]
```
Validates: LLM-backed normalization correctly identifies an execution task from natural language.

### Demo C — Interactive conversation (after Priority 3)
```
Turn 1: "Create a Python script that prints the Fibonacci sequence"
Turn 2: "Now add a command-line argument to control how many numbers to print"
```
Both turns share the same `thread_id`. Turn 2 refers to the artifact from Turn 1.
Validates: conversation continuity, CLI loop, thread state restoration.

### Demo D — Hybrid task with real research (after Priority 4)
```
Request: "Research how this project uses LangGraph and write a one-page summary to docs/architecture-summary.md"
Expected path: research → execution → review
```
Validates: file-based retrieval produces real content, synthesis is coherent, execution writes the file, review passes.

### Demo E — Memory-aware task (after Priority 5)
```
Session 1: "Remember that I prefer all output files in the outputs/ directory"
Session 2 (new thread_id): "Create a script that lists all Python files in the project"
Expected: execution plan targets outputs/ without being told
```
Validates: user preference is stored and injected into context on a fresh session.

---

## 5. What to Stabilize Before Adding Memory, Automation, or Computer Control

Before any of the following can be reliably built:
- persistent memory that refers to prior task artifacts
- automated task queues that chain requests
- computer control (browser, GUI, shell execution)

The following must be stable:

**5.1 Output contract**
`finalize_response` must produce a deterministic, user-facing answer. Currently it is a stub. Memory and automation features will read from `messages` and `review_result`; if those are empty, they cannot function.

**5.2 Subgraph output mapping**
`execution_result` and `review_result` must be correctly mapped back into `MainState` in all routing paths (execution-only, hybrid, research-only). One missed mapping will silently produce empty outputs.

**5.3 End-to-end test coverage**
At least two integration tests that exercise the full pipeline (not just individual subgraphs). These are the regression anchors for all future work.

**5.4 Thread continuity**
`messages` must be written and restored. Any feature that refers to "what we discussed last time" depends on this. Without it, `thread_id` is meaningless above the graph-state level.

**5.5 `normalize_task` reliability**
Automation and memory features will pass structured task objects to subgraphs. If `normalize_task` produces wrong `task_type` or empty `constraints`/`requested_outputs`, automated workflows will route incorrectly and silently.

---

## 6. Explicit Out-of-Scope for Phase 3

The following will **not** be addressed in Phase 3, regardless of how useful they might be:

| Area | Reason deferred |
|---|---|
| Shell / subprocess execution | Significant safety surface; requires sandboxing design not yet specified |
| Web requests as execution tool | External API dependency; auth/rate-limit handling not designed |
| Browser / computer control | Completely separate toolchain (Playwright, etc.); not in V1 scope |
| Multi-user support | Single-user personal assistant per spec §1 |
| Cloud / server deployment | No deployment target defined |
| Agent-to-agent delegation | Not in spec; adds orchestration complexity |
| Real-time streaming output | LangGraph streaming is orthogonal; deferred until UX is stable |
| Plugin / extension system | Premature abstraction; no use case yet requires it |
| Research → execution feedback loop | Review verdict does not currently feed back to re-plan; deferred |
| LLM fine-tuning or prompt optimization | Operational concern, not implementation |

The legacy nodes `research/nodes/interpret.py` and `research/nodes/synthesize.py` remain deprecated but not removed. They will be cleaned up when Research Subgraph undergoes its next planned change.

---

## 7. Recommendation: What to Do Next

**Recommendation: Integration hardening (Priority 1) first.**

Here is the reasoning:

The three subgraphs work correctly in isolation. But they have never been run together as a complete pipeline. `finalize_response` is a stub. `messages` is never written. The CLI prints raw dicts. The Parent Graph's `normalize_task` node is keyword-based.

Every higher-level capability — memory, automation, better UX — depends on this integration being correct:
- Memory reads from `messages` and writes task summaries from `review_result`. If those are empty, memory writes nothing useful.
- The CLI conversation loop depends on `finalize_response` producing a real answer. Without it, the user sees `"final_response": ""`.
- Automation and task queues chain requests; if the first request does not produce a verified output, chaining produces compounding errors.
- End-to-end tests do not exist yet. Adding features without them means future regressions are invisible until manual testing.

The cost of building on an unverified integration is compounding. Each new feature adds more surface area that can mask underlying plumbing failures. The right time to verify and harden the integration is before anything else is added — not after.

**Recommended Phase 3 sequence:**
1. Integration hardening — fix `finalize_response`, populate `messages`, write end-to-end tests
2. `normalize_task` LLM upgrade — the entry point for everything
3. CLI / UX layer — makes the system testable by hand
4. Research retrieval (one real adapter) — unlocks hybrid tasks
5. Memory layer — user preferences + task history

Memory and automation can begin in parallel with step 4 once steps 1–3 are stable, since the memory store is independent of the retrieval implementation.
