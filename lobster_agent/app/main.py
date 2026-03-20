"""Main entry point for Lobster Agent.

Phase 1: Basic skeleton with minimal functionality.
Phase 2+: Enhanced with real LLM integration, tools, and features.
"""

import os
import re
from typing import Optional
import uuid

from app.graphs.main import create_main_graph, create_initial_state, MainState
from app.memory.checkpointer import create_checkpointer
from app.memory.workspace import WorkspaceStore


# ---------------------------------------------------------------------------
# Preference detection (P1)
# ---------------------------------------------------------------------------

# Whitelist of explicit trigger phrases per preference key and value.
# Detection is case-insensitive substring match on the quote-stripped message.
# Only add phrases that are unambiguous statements of preference — not
# phrases that could appear in general content or query text.
_PREFERENCE_TRIGGERS: dict = {
    "response_length": {
        "concise": [
            "too long",
            "be more concise",
            "more concise",
            "keep it brief",
            "be brief",
            "shorter please",
            "less detail",
            "don't need so much detail",
        ],
        "detailed": [
            "more detail",
            "expand on that",
            "too brief",
            "not enough detail",
            "give me more detail",
            "elaborate please",
            "more thorough",
        ],
    },
}


def _strip_quoted(text: str) -> str:
    """Remove content that is likely quoted or referenced rather than expressed.

    Strips:
      - Double-quoted strings up to 200 chars  ("...")
      - Backtick-quoted strings up to 200 chars (`...`)
      - Markdown block-quote lines             (> ...)

    Single-quoted strings are left intact: stripping them would remove
    contractions and common direct speech ("don't", "it's", 'hello').
    """
    # Remove double-quoted content
    text = re.sub(r'"[^"]{1,200}"', "", text)
    # Remove backtick-quoted content (inline code)
    text = re.sub(r"`[^`]{1,200}`", "", text)
    # Remove block-quote lines
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return " ".join(lines)


def _detect_preference(user_request: str) -> Optional[tuple]:
    """Scan user_request for an explicit preference trigger phrase.

    Returns (key, value, trigger_phrase) on the first match, None otherwise.
    Strips quoted/referenced content before scanning so phrases embedded in
    quoted text do not register as expressed preferences.

    Phrases are checked in definition order; first match wins.
    """
    cleaned = _strip_quoted(user_request).lower()
    for key, value_map in _PREFERENCE_TRIGGERS.items():
        for value, phrases in value_map.items():
            for phrase in phrases:
                if phrase in cleaned:
                    return (key, str(value), phrase)
    return None


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

    # Persist the most recent suggestion when research produced one (V3 M2)
    research_result = result.get("research_result") or {}
    suggestion = research_result.get("suggested_next_step")
    if suggestion:
        workspace_store.write_project_state(
            task_id=result.get("task_id", ""),
            suggested_next_step=suggestion,
        )

    # Detect and persist explicit preference expressed in this request (P1)
    # Only stored on successful turns so error messages don't register as prefs.
    if result.get("status") == "success":
        detected = _detect_preference(user_request)
        if detected:
            pref_key, pref_value, trigger_phrase = detected
            workspace_store.write_preference(
                task_id=result.get("task_id", ""),
                key=pref_key,
                value=pref_value,
                trigger_phrase=trigger_phrase,
            )

    return result


def main():
    """Main function for CLI usage. Delegates to app.cli."""
    from app.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
