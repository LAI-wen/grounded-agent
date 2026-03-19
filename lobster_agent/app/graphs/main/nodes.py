"""Node functions for Parent Graph.

Priority 2: normalize_task uses LLM-backed classification when
ANTHROPIC_API_KEY is set; falls back to keyword heuristic otherwise
or on any parse/API error.
"""

import json
import os
from datetime import datetime

from anthropic import Anthropic

from app.schemas import TraceEntry
from .prompts.templates import NORMALIZE_TASK_SYSTEM_PROMPT, NORMALIZE_TASK_USER_PROMPT
from .router import determine_required_subgraphs
from .state import MainState, Message, NormalizedTask


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------

def normalize_task(state: MainState) -> MainState:
    """Normalize user request into a structured NormalizedTask.

    LLM path (ANTHROPIC_API_KEY present):
      Calls Claude to classify task_type and extract all NormalizedTask fields.
      Falls back to keyword heuristic on API error or JSON parse failure.

    Fallback path (no API key, or any error):
      Keyword heuristic identical to the Phase 1 behaviour.
    """
    user_request = state["user_request"]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    fallback_used = False
    conversation_history = _format_conversation_history(state.get("messages", []))

    if api_key:
        try:
            normalized_task, task_type = _llm_normalize(user_request, api_key, conversation_history)
        except Exception as e:
            normalized_task, task_type = _fallback_normalize(user_request)
            fallback_used = True
            _fallback_reason = f"LLM normalize failed ({e}), using keyword fallback"
        else:
            _fallback_reason = None
    else:
        normalized_task, task_type = _fallback_normalize(user_request)
        fallback_used = True
        _fallback_reason = "No ANTHROPIC_API_KEY — using keyword fallback"

    required_subgraphs = determine_required_subgraphs(task_type)

    # Record user message
    user_message: Message = {
        "role": "user",
        "content": user_request,
        "timestamp": datetime.utcnow(),
        "metadata": {"task_type": task_type},
    }

    output_summary = f"task_type: '{task_type}', required_subgraphs: {required_subgraphs}"
    if fallback_used:
        output_summary += f" [{_fallback_reason}]"

    trace_entry = TraceEntry(
        step_id=f"trace_{len(state.get('trace', []))}",
        node_name="normalize_task",
        subgraph=None,
        timestamp=datetime.utcnow(),
        input_summary=f"user_request: '{user_request[:50]}...'",
        output_summary=output_summary,
        status="success",
    )

    return {
        **state,
        "normalized_task": normalized_task,
        "task_type": task_type,
        "required_subgraphs": required_subgraphs,
        "messages": [*state.get("messages", []), user_message],
        "status": "running",
        "trace": [*state.get("trace", []), trace_entry],
    }


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

def _format_conversation_history(messages: list) -> str:
    """Format up to the last 4 messages as context for normalize_task.

    Returns an empty string when there are no prior messages so the prompt
    template renders cleanly without extra whitespace.
    """
    if not messages:
        return ""
    recent = messages[-4:]
    lines = ["Recent conversation context:"]
    for m in recent:
        role = m.get("role", "unknown")
        content = str(m.get("content", ""))[:300]
        lines.append(f"[{role}] {content}")
    return "\n" + "\n".join(lines)


def _llm_normalize(user_request: str, api_key: str, conversation_history: str = ""):
    """Call Claude to classify and extract NormalizedTask fields.

    Returns (normalized_task, task_type).
    Raises on API error or JSON parse failure — caller falls back to heuristic.
    """
    client = Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=NORMALIZE_TASK_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": NORMALIZE_TASK_USER_PROMPT.format(
                user_request=user_request,
                conversation_history=conversation_history,
            ),
        }],
    )

    raw = response.content[0].text.strip()
    data = _parse_json(raw)

    task_type = _validated_task_type(data.get("task_type", ""))
    priority   = _validated_priority(data.get("priority", "medium"))

    normalized_task: NormalizedTask = {
        "objective":         str(data.get("objective", user_request)),
        "constraints":       _as_str_list(data.get("constraints", [])),
        "requested_outputs": _as_str_list(data.get("requested_outputs", ["response"])),
        "assumptions":       _as_str_list(data.get("assumptions", [])),
        "priority":          priority,
    }

    return normalized_task, task_type


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    if "```json" in text:
        start = text.find("```json") + 7
        end   = text.find("```", start)
        text  = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end   = text.find("```", start)
        text  = text[start:end].strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object, got {type(result).__name__}")
    return result


def _validated_task_type(raw: str):
    """Return raw if it is a valid TaskType, else raise ValueError."""
    valid = {"research", "execution", "hybrid"}
    cleaned = raw.strip().lower()
    if cleaned not in valid:
        raise ValueError(f"Invalid task_type from LLM: {raw!r}")
    return cleaned


def _validated_priority(raw: str):
    """Return a valid priority Literal, defaulting to 'medium'."""
    valid = {"low", "medium", "high"}
    cleaned = raw.strip().lower()
    return cleaned if cleaned in valid else "medium"


def _as_str_list(value) -> list:
    """Coerce a value to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value] if value else []
    return []


# ---------------------------------------------------------------------------
# Keyword heuristic fallback (identical to Phase 1 behaviour)
# ---------------------------------------------------------------------------

def _fallback_normalize(user_request: str):
    """Keyword heuristic — used when no API key or on LLM error.

    Returns (normalized_task, task_type).
    Behaviour is identical to the original Phase 1 implementation.
    """
    normalized_task: NormalizedTask = {
        "objective":         user_request,
        "constraints":       [],
        "requested_outputs": ["response"],
        "assumptions":       [],
        "priority":          "medium",
    }

    request_lower = user_request.lower()
    has_research  = any(w in request_lower for w in ["research", "find", "search"])
    has_execution = any(w in request_lower for w in [
        "execute", "run", "create", "implement", "build", "write", "make",
    ])

    if has_research and has_execution:
        task_type = "hybrid"
    elif has_research:
        task_type = "research"
    elif has_execution:
        task_type = "execution"
    else:
        task_type = "hybrid"

    return normalized_task, task_type


def finalize_response(state: MainState) -> MainState:
    """Assemble final response and update status.

    Phase 3: Composes a user-facing response from whichever subgraphs ran
    and appends an assistant Message to state.messages.
    """
    review_result = state.get("review_result")
    execution_result = state.get("execution_result")
    research_result = state.get("research_result")

    # Compose user-facing response from available results
    final_response = _compose_response(review_result, execution_result, research_result)

    # Determine workflow status
    if review_result:
        verdict = review_result["verdict"]
        if verdict == "pass":
            final_status = "success"
        elif verdict == "blocked":
            final_status = "blocked"
        else:
            final_status = "partial"
    elif execution_result:
        exec_status = execution_result.get("status", "failed")
        if exec_status == "success":
            final_status = "success"
        elif exec_status == "no_op":
            final_status = "no_op"
        elif exec_status == "partial":
            final_status = "partial"
        else:
            final_status = "failed"
    elif research_result:
        final_status = "success"
    else:
        final_status = "partial"

    # Record assistant message
    assistant_message: Message = {
        "role": "assistant",
        "content": final_response,
        "timestamp": datetime.utcnow(),
        "metadata": {"status": final_status},
    }

    # Create trace entry
    trace_entry = TraceEntry(
        step_id=f"trace_{len(state.get('trace', []))}",
        node_name="finalize_response",
        subgraph=None,
        timestamp=datetime.utcnow(),
        input_summary="Collecting subgraph results",
        output_summary=f"final_status: '{final_status}'",
        status="success",
    )

    return {
        **state,
        "status": final_status,
        "messages": [*state.get("messages", []), assistant_message],
        "trace": [*state.get("trace", []), trace_entry],
    }


def _compose_response(review_result, execution_result, research_result) -> str:
    """Build a user-facing response from whichever subgraphs ran.

    Priority order:
    1. Review provides a ready-made final_response (includes research if present)
    2. Execution without review
    3. Research without execution
    """
    # Review provides a ready-made final_response — use it directly
    if review_result:
        return review_result["final_response"]

    # Execution without review - check if research also ran (hybrid case)
    if execution_result:
        lines = []

        # Include research results first if available
        if research_result:
            summary = research_result.get("summary", "")
            if summary:
                lines.append("Research findings:")
                lines.append(summary)
                lines.append("")  # blank line separator

        exec_status = execution_result.get("status", "failed")

        if exec_status == "no_op":
            lines.append("No actions were executed.")
            lines.append("The execution plan contained no executable steps.")
        elif exec_status == "success":
            lines.append("Task completed successfully.")
        elif exec_status == "partial":
            lines.append("Task completed with some errors.")
        else:
            lines.append("Task failed.")

        artifacts = execution_result.get("artifacts", [])
        if artifacts:
            lines.append("\nOutputs produced:")
            for a in artifacts:
                name = _attr(a, "name") or "unknown"
                path = _attr(a, "path")
                lines.append(f"  - {name} → {path or '(no path)'}")
        errors = execution_result.get("errors", [])
        if errors:
            lines.append("\nErrors:")
            for e in errors:
                msg = _attr(e, "message") or str(e)
                lines.append(f"  - {msg}")
        return "\n".join(lines)

    # Research without execution
    if research_result:
        lines = []
        summary = research_result.get("summary", "")
        if summary:
            lines.append(summary)
        confidence = research_result.get("confidence", 0.0)
        lines.append(f"\nConfidence: {confidence:.0%}")
        open_q = research_result.get("open_questions", [])
        if open_q:
            lines.append("\nOpen questions:")
            for q in open_q:
                lines.append(f"  - {q}")
        return "\n".join(lines) if lines else "Research completed."

    return "Task completed. No results available."


def _attr(obj, field: str):
    """Get a field from either a Pydantic model or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


def handle_error(state: MainState) -> MainState:
    """Handle workflow errors.

    Phase 1: Simple error state marker.
    Phase 2+: More sophisticated error recovery.
    """
    # Create trace entry
    trace_entry = TraceEntry(
        step_id=f"trace_{len(state.get('trace', []))}",
        node_name="handle_error",
        subgraph=None,
        timestamp=datetime.utcnow(),
        input_summary=f"errors: {len(state.get('errors', []))} error(s)",
        output_summary="workflow failed",
        status="failure",
    )

    return {
        **state,
        "status": "failed",
        "trace": [*state.get("trace", []), trace_entry],
    }
