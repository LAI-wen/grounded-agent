"""Main entry point for Lobster Agent.

Phase 1: Basic skeleton with minimal functionality.
Phase 2+: Enhanced with real LLM integration, tools, and features.
"""

import os
from typing import Optional
import uuid

from app.graphs.main import create_main_graph, create_initial_state, MainState
from app.memory.checkpointer import create_checkpointer
from app.memory.workspace import WorkspaceStore


def run_lobster_agent(
    user_request: str,
    thread_id: Optional[str] = None,
    checkpointer=None,
) -> MainState:
    """Run the Lobster Agent workflow.

    Args:
        user_request: User's input request
        thread_id: Thread ID for conversation continuity (generates UUID if None)
        checkpointer: LangGraph checkpointer instance. When provided, prior
            conversation messages are loaded from the checkpoint and carried
            into the new turn so the agent retains session context.
            Pass the same instance on every turn of a session.

    Returns:
        Final MainState after workflow completion
    """
    graph = create_main_graph()
    thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    if checkpointer is not None:
        compiled_graph = graph.compile(checkpointer=checkpointer)
        # Load prior messages so the agent retains conversation context
        prior = compiled_graph.get_state(config)
        prior_messages = (prior.values or {}).get("messages", [])
    else:
        compiled_graph = graph.compile()
        prior_messages = []

    # Read workspace context (silent no-op when .lobster/ is absent)
    workspace_store = WorkspaceStore(os.getcwd())
    workspace_context = workspace_store.read_context()

    initial_state = create_initial_state(user_request)
    if prior_messages:
        initial_state = {**initial_state, "messages": prior_messages}
    if workspace_context:
        initial_state = {**initial_state, "workspace_context": workspace_context}

    result = compiled_graph.invoke(initial_state, config)

    # Persist memory for successfully completed tasks (silent on error)
    workspace_store.write_task_summary(result)

    return result


def main():
    """Main function for CLI usage. Delegates to app.cli."""
    from app.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
