# Lobster Agent Specification v1.3

## 1. Goal

Build a modular personal AI assistant using **LangGraph** that can:

- interpret user requests
- plan workflows
- perform research
- execute tasks
- review outputs

The system must be:

- modular
- testable
- extensible
- safe by default

The architecture should support future expansion into a fully autonomous assistant while keeping **Version 1 minimal and stable**.

---

## 2. Architecture Overview

The system uses a **Supervisor + Subgraph architecture**.

```
Parent Graph
│
├─ Research Subgraph
├─ Execution Subgraph
└─ Review Subgraph
```

The **Parent Graph** orchestrates the workflow.

Each **Subgraph** is responsible for a bounded domain task.

Subgraphs should encapsulate their internal complexity and expose a clear input/output interface.

---

## 3. System Components

### 3.1 Main Graph (Supervisor)

#### Responsibilities

- receive user request
- normalize user intent into structured task
- determine task type
- route tasks to appropriate subgraph
- manage workflow state transitions
- collect outputs from subgraphs
- coordinate final response assembly

#### Non-responsibilities

- should not perform heavy business logic
- should not run external tools directly
- should not implement domain-specific workflows
- should not make autonomous decisions without routing

#### Outputs

Main Graph should produce:

- `normalized_task` - structured version of user request
- `task_type` - high-level classification for routing
- `required_subgraphs` - ordered execution plan
- `trace` - execution path taken
- `status` - success/failure/partial

#### task_type allowed values

In V1, `task_type` must be one of:

```
research             # research-only task
execution            # execution-only task (may include minimal implicit research)
hybrid               # requires explicit research → execution → review workflow
```

Do not introduce custom task types. These three values are sufficient for V1 routing logic.

#### required_subgraphs allowed identifiers

The `required_subgraphs` field must be an ordered list containing only these allowed subgraph identifiers:

```
research
execution
review
```

Do not use variations like "reviewer", "executor", "validation", etc. Maintain consistent naming.

---

### 3.2 Research Subgraph

#### Purpose

Gather and process information required to complete a task.

#### Responsibilities

- convert user request into research queries
- retrieve relevant information from available sources
- filter unreliable or irrelevant sources
- synthesize structured knowledge from evidence
- produce a research summary with citations

#### Non-responsibilities

- must not execute system side effects
- must not write final user-facing response
- must not modify files directly
- must not decide on execution strategies

#### Workflow

1. interpret research task
2. retrieve sources
3. filter sources by relevance and reliability
4. synthesize evidence into structured knowledge
5. produce structured research output

#### Input

```
user_request
context
task_id
```

#### Output

```
research_result
```

**Structured output requirement**: `research_result` must be a structured object, not a raw string. Recommended fields:

```
summary              # synthesized research summary
evidence             # extracted facts with citations
citations            # source references
confidence           # confidence score (0.0-1.0)
open_questions       # unanswered or unclear aspects
```

This ensures the result can be reliably parsed and used by downstream subgraphs.

---

### 3.3 Execution Subgraph

#### Purpose

Perform concrete actions or generate artifacts.

#### Responsibilities

- transform plans into executable steps
- generate commands or code
- run approved safe tools only
- produce artifacts (files, code, results)
- log all execution steps

#### Non-responsibilities

- must not change system configuration
- must not introduce new research tasks independently
- must not escalate privileges
- must not expand scope beyond approved plan

#### Workflow

1. interpret execution plan
2. generate commands or code
3. simulate execution (dry run)
4. perform safety check
5. run safe execution
6. collect artifacts and logs

#### Output Examples

- generated code
- created/modified files
- structured execution results
- execution logs

**Structured output requirement**: `execution_result` must be a structured object, not a raw string. Recommended fields:

```
actions_taken        # list of commands/operations performed
artifacts            # produced files or outputs
logs                 # execution logs
success              # boolean or status indicator
errors               # any errors encountered during execution
```

This ensures execution results can be validated and reviewed systematically.

---

### 3.4 Review Subgraph

#### Purpose

Validate outputs and assemble final user-facing responses.

#### Responsibilities

- validate output consistency and completeness
- identify formatting issues and missing elements
- check that outputs match expected schemas
- assemble final user-facing response from approved outputs
- provide structured verdict on output quality

#### Non-responsibilities

- must not perform deep content regeneration
- must not introduce new unsupported reasoning or facts
- must not silently overwrite execution or research results
- must not execute new workflows
- must not make autonomous corrections without approval

#### Workflow

1. inspect results from previous subgraphs
2. validate against expected structure and requirements
3. identify issues (formatting, completeness, consistency)
4. generate verdict (pass/revise/fail)
5. assemble final response from validated outputs

---

## 4. State Design

State objects must be explicitly defined and typed.

### State Ownership

Clear ownership prevents conflicting state mutations and maintains system integrity.

**Ownership rules**:
- **MainState** is owned by the Parent Graph orchestration layer
- Each **Subgraph** owns its internal state schema (ResearchState, ExecutionState, ReviewState)
- Subgraphs may produce outputs that are mapped back into MainState fields
- Subgraphs **should not** directly mutate unrelated MainState fields
- State updates should follow a clear contract: subgraph → output → MainState mapping

**Example flow**:
1. Research Subgraph updates its own ResearchState
2. Research Subgraph returns `research_result`
3. Parent Graph maps `research_result` into MainState
4. Other subgraphs access data through MainState, not directly from ResearchState

---

### 4.0 Shared Schemas

These schemas are used across multiple states.

#### Artifact Schema

Artifacts should be structured records containing:

```
type              # artifact type (file/code/data/image/document)
name              # artifact name or title
path              # file path (if applicable) or None
value             # artifact content (if inline) or None
source_subgraph   # which subgraph produced this
created_at        # timestamp
metadata          # additional context
```

#### Error Schema

Errors should be structured records containing:

```
code              # error code or type
severity          # critical/error/warning
message           # human-readable error message
source            # which component raised the error
timestamp         # when the error occurred
stack_trace       # full stack trace (if available)
```

#### SafetyFlag Schema

Safety flags should be structured warnings containing:

```
code              # flag identifier (e.g., "path_violation", "destructive_op")
severity          # high/medium/low
message           # description of the safety concern
source            # which component raised the flag
context           # relevant context (file path, command, etc.)
```

#### TraceEntry Schema

Each trace entry should include:

```
step_id           # unique identifier for this step
node_name         # which node executed
subgraph          # which subgraph this belongs to
timestamp         # when this step occurred
input_summary     # brief summary of inputs (not full payload)
output_summary    # brief summary of outputs (not full payload)
status            # success/failure/partial
```

**Important**: `trace` captures workflow-level progression and routing decisions. For detailed execution logs, use node-level `logs`.

---

### 4.1 MainState

Contains:

```
user_request          # original user input
normalized_task       # structured/parsed task
task_id              # unique identifier
task_type            # high-level classification (research/execution/review/hybrid)
required_subgraphs   # ordered list of subgraphs to execute
current_subgraph     # which subgraph is currently active
next_step            # routing decision for next node
plan                 # high-level workflow plan
research_result      # output from research subgraph
execution_result     # output from execution subgraph
review_result        # output from review subgraph
artifacts            # structured artifact records (see Artifact Schema)
messages             # conversation history (see message format below)
errors               # captured errors and exceptions (see Error Schema)
trace                # workflow execution trace (see TraceEntry Schema)
safety_flags         # security/safety warnings (see SafetyFlag Schema)
status               # current workflow status
```

#### status values

The `status` field must use one of these predefined values:

```
pending              # task received, not yet started
running              # workflow currently executing
awaiting_review      # execution complete, waiting for review verdict
success              # workflow completed successfully
partial              # workflow completed with partial success
failed               # workflow failed
```

Do not introduce custom status values like "completed", "done", "error", etc.

#### normalized_task minimal schema

The `normalized_task` should be a structured object (not a plain string) containing at minimum:

```
objective            # what the user wants to accomplish
constraints          # any explicit limitations or requirements
requested_outputs    # what artifacts or responses are expected
assumptions          # inferred context or implicit requirements
priority             # task urgency or importance (optional in V1)
```

This ensures downstream subgraphs receive structured, parseable task definitions.

#### plan vs execution_plan distinction

**Important distinction**:

- `plan` (in MainState) - workflow-level high-level plan describing overall strategy and subgraph sequencing
- `execution_plan` (in ExecutionState) - execution subgraph internal actionable steps with concrete commands/actions

These serve different purposes and should not contain duplicate information. The workflow plan guides routing; the execution plan guides tool invocation.

#### task_type and required_subgraphs

**Relationship**:
- `task_type` is a high-level classification used for initial routing and planning decisions
- `required_subgraphs` is the concrete ordered execution plan derived from `task_type`
- The Main Graph uses `task_type` to determine `required_subgraphs`

**Examples**:
- task_type: `"research"` → required_subgraphs: `["research"]`
- task_type: `"execution"` → required_subgraphs: `["execution", "review"]`
- task_type: `"hybrid"` → required_subgraphs: `["research", "execution", "review"]`

#### messages format

Messages must use a consistent structured format throughout the system.

**Recommended structure**:
- Use LangChain message objects or a typed internal schema
- Required fields: `role`, `content`, `timestamp`, `metadata`

**Important**: Messages should store only necessary structured conversation context, not unbounded full-history payloads. Consider limiting message retention or implementing message summarization for long conversations.

**V1 simplification**: In V1, messages may be minimal and only preserve the structured user request and essential system outputs needed for thread continuity. Heavy message history management is not required for the initial version.

---

### 4.2 ResearchState

Contains:

```
research_query       # structured query for retrieval
retrieval_strategy   # how to search (vector/keyword/hybrid)
sources              # raw retrieved sources
filtered_sources     # sources after filtering
evidence             # extracted facts with citations
summary              # synthesized research summary
confidence           # confidence score (0.0-1.0)
open_questions       # unanswered or unclear aspects
```

#### confidence score

Should reflect internal assessment of:
- source quality and reliability
- coverage of the research query
- consistency across sources

This is NOT an arbitrary model self-rating.
Base confidence on concrete factors like source count, agreement, and completeness.

---

### 4.3 ExecutionState

Contains:

```
execution_plan       # structured plan with steps
generated_actions    # commands/code generated
simulation_result    # dry run results
execution_result     # actual execution results
artifacts            # produced files/outputs
logs                 # execution logs
safety_check         # safety verification results
```

---

### 4.4 ReviewState

Contains:

```
review_target        # what is being reviewed
issues               # detected problems
improvements         # suggested fixes
verdict              # pass/revise/fail
review_notes         # detailed review comments
final_response       # user-facing output
```

**Possible verdict values:**

- `pass` - output is acceptable
- `revise` - needs minor improvements
- `fail` - needs major rework or re-execution

**V1 verdict handling strategy:**

When the Review Subgraph returns a verdict, the Parent Graph should handle it as follows:

- `pass` → finalize response and return to user
- `revise` → return review feedback to user without automatic regeneration
- `fail` → return failure status and preserve partial outputs for inspection

**Important**: V1 does not implement automatic retry loops. If revision or re-execution is needed, the user must explicitly request it in a follow-up interaction.

**Structured output requirement**: `review_result` must be a structured object, not a raw string. Recommended fields:

```
verdict              # pass/revise/fail
issues               # list of detected problems
approved_artifacts   # artifacts that passed validation
final_response       # assembled user-facing output
review_notes         # detailed review comments
```

---

## 5. Memory Strategy

### Version 1 Memory

Use **LangGraph Checkpointer** for thread-scoped memory.

#### Purpose:

- store conversation state within a thread
- enable workflow recovery on interruption
- allow debugging and state inspection
- support human-in-the-loop workflows

This memory is scoped to a conversation thread and is intended for thread continuity, recovery, and debugging rather than long-term personalization.

### V1 does NOT include:

- persistent user profile memory
- cross-session semantic memory
- project knowledge retrieval
- long-term personalization

### Future versions may introduce:

- vector-based semantic memory
- persistent user profile and preferences
- project workspace memory
- cross-session knowledge graphs

---

## 6. Tool Layer

Tools must follow a **strict allowlist policy**.

### Allowed tools (V1)

- **search tool** - retrieve information from approved sources only
  - must use approved adapters/providers
  - must return normalized source records
  - no arbitrary network requests
  - **V1 implementation note**: approved sources may be stubbed or limited to predefined adapters and local test fixtures. Real external network retrieval is not required for Phase 1/2.
- **project-scoped file read** - read files within project directory only
- **project-scoped file write** - write files within project directory only
- **safe python helper functions** - pre-approved utility functions
- **structured parser utilities** - JSON, YAML, markdown parsers

### Forbidden tools

- unrestricted shell execution
- system-wide file modification
- destructive file operations (rm -rf, format, etc.)
- background autonomous loops
- network requests to unapproved endpoints
- privilege escalation operations

### Tool Interface Contract

All tools should expose a consistent interface to ensure predictable integration:

```
input_schema         # structured schema defining expected inputs
validation           # input validation logic
execution            # core tool operation
structured_result    # standardized result format
structured_error     # standardized error format
```

This contract ensures the Execution Subgraph can invoke any tool without custom handling logic for each tool.

### Tool Safety Requirements

All tools must:
- validate inputs before execution
- log all operations
- return structured results
- handle errors gracefully
- respect project boundaries

---

## 7. Safety Constraints

The system must:

- avoid destructive operations
- respect tool restrictions
- log all execution steps
- preserve traceability of decisions
- fail safely on errors

**Execution Subgraph must perform a safety check before running any tool.**

Safety checks include:
- path validation (no access outside project)
- command validation (no destructive operations)
- resource limits (file size, execution time)
- approval requirements for high-risk operations

---

## 8. Development Rules

- **nodes must be small and testable** - single responsibility per node
- **avoid large monolithic files** - split into logical modules
- **maintain separation between:**
  - graph definitions
  - state schemas
  - prompt templates
  - service/tool implementations
- **avoid global mutable state** - all state must be in graph state
- **prefer typed schemas** - use Pydantic or TypedDict
- **write code for maintainability** - clear names, documentation, types

---

## 9. Acceptance Criteria

Version 1 is considered successful if the system can:

1. **receive a user request** and store it in MainState
2. **normalize the request** into a structured task
3. **route to appropriate subgraph** based on task type
4. **execute a multi-step workflow** through at least one subgraph
5. **return a structured response** with all required fields
6. **maintain thread state** through LangGraph checkpointer
7. **log intermediate workflow steps** for debugging
8. **pass smoke tests** for parent graph and all three subgraphs
9. **handle basic errors** without crashing
10. **respect safety constraints** and tool restrictions

### Test scenarios to verify:

- Pure research task (no execution)
- Pure execution task (minimal research)
- Combined workflow (research → execution → review)
- Error handling (invalid input, tool failure)
- State persistence (interrupt and resume)

---

## 10. Failure Handling

The system must:

- **capture errors in state** - populate `errors` field
- **avoid silent failures** - always log and report errors
- **return partial results when possible** - don't lose successful work
- **record workflow traces** - preserve execution path for debugging

### When a failure occurs:

1. Capture exception details in `errors`
2. Update `status` to indicate failure type
3. Preserve `trace` of successful steps
4. Return partial results if available
5. Provide clear error message to user

### Failures should update:

```
errors          # exception details
trace           # execution path before failure
status          # failure type and location
```

---

## 11. Out of Scope (V1)

The following features are **intentionally excluded** from Version 1:

- long-term user memory and personalization
- unrestricted computer control
- background autonomous task scheduling
- multi-user collaboration features
- production deployment hardening
- advanced security features (encryption, authentication)
- cross-project knowledge sharing
- real-time collaboration
- plugin/extension system

These may be introduced in later versions based on V1 learnings.

---

## 12. Future Roadmap

Potential features for future versions:

### V2 candidates:
- persistent user memory with vector search
- project workspace memory
- enhanced tool safety and sandboxing
- streaming outputs
- parallel subgraph execution

### V3 candidates:
- autonomous task loops with approval
- safe computer automation
- multi-agent collaboration
- plugin architecture
- advanced planning with tree search

---

## 13. Observability

The system must provide comprehensive observability for debugging and evaluation.

### Required observability features:

- **workflow trace** - complete execution path through graphs and nodes
- **node-level logs** - detailed logs from each node execution
- **state checkpoints** - meaningful state checkpoints for debugging
- **safety warnings** - all safety flags and violations
- **execution errors** - all errors with full context
- **final status** - clear indication of success/failure/partial

### Trace format

The trace should capture:
- node execution order
- timestamps for each step
- state transitions
- routing decisions
- subgraph transitions

See TraceEntry Schema in section 4.0 for the structured format.

### Logging requirements

All nodes must log **sanitized summaries** of:
- inputs received (not full raw payloads)
- actions taken
- outputs produced (not full raw payloads)
- errors encountered

**Important considerations**:
- Logs should be structured and queryable
- Avoid logging sensitive data or tokens
- Log summaries, not unbounded full payloads
- Include context for debugging (node name, subgraph, timestamp)

### State inspection

The system should support inspection of meaningful state checkpoints for debugging.
LangGraph's built-in checkpointer provides this capability.

---

## 14. Implementation Notes

### Recommended project structure:

```
lobster_agent/
├── app/
│   ├── main.py
│   ├── graphs/
│   │   ├── main/
│   │   │   ├── graph.py
│   │   │   ├── state.py
│   │   │   └── router.py
│   │   ├── research/
│   │   │   ├── graph.py
│   │   │   ├── state.py
│   │   │   ├── nodes/
│   │   │   │   ├── interpret.py
│   │   │   │   ├── retrieve.py
│   │   │   │   ├── filter.py
│   │   │   │   └── synthesize.py
│   │   │   ├── prompts/
│   │   │   │   └── templates.py
│   │   │   └── services/
│   │   │       └── retrieval.py
│   │   ├── execution/
│   │   │   ├── graph.py
│   │   │   ├── state.py
│   │   │   ├── nodes/
│   │   │   │   ├── plan.py
│   │   │   │   ├── generate.py
│   │   │   │   ├── simulate.py
│   │   │   │   ├── safety_check.py
│   │   │   │   └── execute.py
│   │   │   ├── prompts/
│   │   │   │   └── templates.py
│   │   │   └── services/
│   │   │       └── executor.py
│   │   └── review/
│   │       ├── graph.py
│   │       ├── state.py
│   │       ├── nodes/
│   │       │   ├── inspect.py
│   │       │   ├── verdict.py
│   │       │   └── assemble.py
│   │       ├── prompts/
│   │       │   └── templates.py
│   │       └── services/
│   │           └── validator.py
│   ├── tools/
│   │   ├── search.py
│   │   ├── file_ops.py
│   │   └── parsers.py
│   ├── memory/
│   │   └── checkpointer.py
│   └── schemas/
│       ├── artifact.py
│       ├── error.py
│       └── safety_flag.py
└── tests/
    ├── graphs/
    │   ├── test_main.py
    │   ├── test_research.py
    │   ├── test_execution.py
    │   └── test_review.py
    └── integration/
        └── test_workflows.py
```

**Key design principles:**

- Each subgraph is a self-contained module with its own nodes, prompts, and services
- Shared tools and schemas are at the top level
- Bounded contexts are reflected in the directory structure
- Tests mirror the graph structure

### Development sequence:

1. Define all state schemas
2. Implement Main Graph with routing only
3. Implement Research Subgraph (simplest)
4. Implement Execution Subgraph with safety checks
5. Implement Review Subgraph
6. Add comprehensive tests
7. Add failure handling
8. Verify acceptance criteria

---

## Version History

- **v1.3** - Implementation contracts and value definitions:
  - Defined explicit `status` enum values (pending/running/awaiting_review/success/partial/failed)
  - Defined explicit `task_type` enum values (research/execution/hybrid)
  - Defined allowed `required_subgraphs` identifiers (research/execution/review)
  - Unified naming: `workflow_trace` → `trace`, `final_status` → `status` (consistent with MainState)
  - Added `normalized_task` minimal schema (objective/constraints/requested_outputs/assumptions/priority)
  - Clarified distinction between `plan` (workflow-level) and `execution_plan` (execution-level)
  - Added structured output requirements for all subgraph results (research_result/execution_result/review_result)
  - Simplified V1 messages to minimal thread continuity requirements
  - Added Tool Interface Contract (input_schema/validation/execution/structured_result/structured_error)
  - Defined V1 review verdict handling strategy (pass/revise/fail without automatic retry loops)
  - Clarified approved sources for V1 (stubbed/predefined adapters/local fixtures)
- **v1.2** - Production-ready refinements:
  - Renamed `workflow_mode` → `required_subgraphs` for clarity
  - Removed `selected_subgraph` to simplify routing state (kept `current_subgraph` and `next_step`)
  - Clarified relationship between `task_type` and `required_subgraphs`
  - Added TraceEntry Schema with structured trace format
  - Distinguished `trace` (workflow-level) from `logs` (node-level)
  - Added message retention guidance (avoid unbounded history)
  - Changed logging to sanitized summaries (not raw payloads)
  - Weakened state snapshots to "meaningful checkpoints"
  - Improved review node naming: `inspect.py`, `verdict.py`, `assemble.py`
  - Added State Ownership section defining mutation boundaries
- **v1.1** - Engineering-focused refinements:
  - Added `workflow_mode` for multi-subgraph orchestration
  - Defined structured schemas for Artifact, Error, and SafetyFlag
  - Clarified Review Subgraph as validator/assembler (not content regenerator)
  - Required consistent message format across system
  - Enhanced project structure with bounded context per subgraph
  - Added Observability section with trace and logging requirements
  - Refined confidence scoring and search tool policies
- **v1.0** - Complete specification with safety, state design, and acceptance criteria
- **v0.1** - Initial draft with basic architecture
