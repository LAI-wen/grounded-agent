# Copilot Repository Instructions

This repository implements a modular LangGraph-based multi-agent system.

The goal is to build a personal AI assistant using a parent graph and multiple subgraphs.

---

## Architecture Rules

The system must follow this architecture:

```
Parent Graph
│
├ Research Subgraph
├ Execution Subgraph
└ Review Subgraph
```

Each subgraph must:

- own its own state schema
- define its own internal nodes
- expose a `build_graph()` function
- be independently testable

---

## Graph Design Guidelines

1. Parent graph should only orchestrate workflows.
2. Subgraphs should encapsulate domain logic.
3. Avoid putting complex prompts directly inside graph definitions.
4. Separate prompts, services, and node logic.

---

## Coding Guidelines

- Use Python.
- Prefer typed structures (TypedDict or Pydantic).
- Keep node functions small.
- Avoid large files.
- Avoid hidden global state.

---

## Memory Design

Version 1 uses LangGraph checkpointer only.

This provides thread-level memory.

Do not implement full long-term memory yet.

---

## Tool Safety

Allowed tools:

- search
- safe file operations
- safe python helpers

Forbidden tools:

- unrestricted shell execution
- system-level modifications
- destructive file operations

---

## Development Workflow

When implementing new features:

1. propose architecture first
2. explain design decisions
3. generate code skeleton
4. then implement logic incrementally

Never attempt to generate the entire system in one step.

---

## Testing Requirements

Each subgraph should be:

- independently invokable
- covered by simple tests
- able to run a smoke test
