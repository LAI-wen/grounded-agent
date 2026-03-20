"""Plan node for Execution Subgraph.

Phase 2B: Uses LLM to decompose the task into typed ExecutionStep objects.
Falls back to a minimal stub plan if ANTHROPIC_API_KEY is not set.
"""

import json
import os

from anthropic import Anthropic

from ..state import ExecutionState, ExecutionStep
from ..prompts.templates import (
    PLAN_EXECUTION_SYSTEM_PROMPT,
    PLAN_EXECUTION_USER_PROMPT,
)


def create_execution_plan(state: ExecutionState) -> ExecutionState:
    """Create an execution plan with typed, actionable steps.

    Uses Claude to decompose the task into ExecutionStep objects that
    include the tool to invoke and the parameters needed.

    Falls back to a single no-op stub step if LLM is unavailable.
    """
    task_description = state["task_description"]
    context = state.get("context", {})
    workspace_context = context.get("workspace_context", "")
    context_str = str({k: v for k, v in context.items() if k != "workspace_context"}) or "None"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_plan(state, task_description)

    client = Anthropic(api_key=api_key)

    workspace_artifacts_block = (
        f"Known existing artifacts from workspace context:\n{workspace_context}\n"
        if workspace_context else ""
    )

    try:
        user_prompt = PLAN_EXECUTION_USER_PROMPT.format(
            task_description=task_description,
            context=context_str,
            workspace_artifacts=workspace_artifacts_block,
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=PLAN_EXECUTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        steps_data = _parse_json(response.content[0].text.strip())

        execution_plan: list[ExecutionStep] = []
        for raw in steps_data:
            step: ExecutionStep = {
                "step_id": raw.get("step_id", f"step_{len(execution_plan) + 1}"),
                "action": raw.get("action", ""),
                "tool": raw.get("tool"),
                "params": raw.get("params", {}),
                "tool_input": None,
                "status": "pending",
            }
            execution_plan.append(step)

        workspace_note = " (workspace context injected)" if workspace_context else ""
        return {
            **state,
            "execution_plan": execution_plan,
            "logs": [
                *state.get("logs", []),
                f"LLM plan created: {len(execution_plan)} step(s){workspace_note}",
            ],
        }

    except Exception as e:
        return {
            **_fallback_plan(state, task_description),
            "logs": [
                *state.get("logs", []),
                f"Plan LLM failed ({e}), using fallback plan",
            ],
        }


def _fallback_plan(state: ExecutionState, task_description: str) -> ExecutionState:
    """Minimal stub plan used when LLM is unavailable."""
    execution_plan: list[ExecutionStep] = [
        {
            "step_id": "step_1",
            "action": f"Execute: {task_description}",
            "tool": None,
            "params": {},
            "tool_input": None,
            "status": "pending",
        }
    ]
    return {
        **state,
        "execution_plan": execution_plan,
        "logs": [
            *state.get("logs", []),
            "Fallback plan created (no ANTHROPIC_API_KEY)",
        ],
    }


def _parse_json(text: str) -> list:
    """Extract and parse a JSON array from LLM response text."""
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end].strip()
    return json.loads(text)
