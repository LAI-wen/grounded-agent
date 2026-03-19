"""Checkpointer for thread-scoped memory using LangGraph.

V1 uses LangGraph Checkpointer for thread-scoped memory to:
- Store conversation state within a thread
- Enable workflow recovery on interruption
- Allow debugging and state inspection
- Support human-in-the-loop workflows
"""

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    SQLITE_AVAILABLE = True
except ImportError:
    try:
        from langgraph.checkpoint.memory import MemorySaver
        SQLITE_AVAILABLE = False
    except ImportError:
        # Fallback for older versions
        from langgraph.checkpoint import MemorySaver
        SQLITE_AVAILABLE = False

from pathlib import Path


def create_checkpointer(db_path: str = "checkpoints.db"):
    """Create a checkpointer for state persistence.

    Args:
        db_path: Path to SQLite database file (only used if SQLite available)

    Returns:
        Checkpointer instance (SqliteSaver if available, MemorySaver otherwise)
    """
    if SQLITE_AVAILABLE:
        # Ensure directory exists
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        # Create and return SQLite checkpointer
        return SqliteSaver.from_conn_string(db_path)
    else:
        # Fallback to in-memory checkpointer for Phase 1
        return MemorySaver()


# For future versions (out of scope for V1):
# - persistent user profile memory
# - cross-session semantic memory
# - project knowledge retrieval
# - long-term personalization
