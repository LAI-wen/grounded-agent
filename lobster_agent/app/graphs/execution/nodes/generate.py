"""Generate node for Execution Subgraph.

Phase 2B: Uses LLM to produce actual tool_input for each step —
in particular, generating real file content for file_write steps.
Falls back to copying params directly if LLM is unavailable.
"""

import json
import os

from anthropic import Anthropic

from ..state import ExecutionState, ExecutionStep
from ..prompts.templates import (
    GENERATE_ACTIONS_SYSTEM_PROMPT,
    GENERATE_ACTIONS_USER_PROMPT,
)


def generate_actions(state: ExecutionState) -> ExecutionState:
    """Generate tool_input for every step in the execution plan.

    For file_write steps, the LLM produces the actual file content.
    For file_read steps, tool_input is resolved from params.
    For no-tool steps, tool_input is left as {}.

    Falls back to direct param copy if LLM is unavailable.
    """
    execution_plan = state.get("execution_plan", [])
    task_description = state["task_description"]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_generate(state, execution_plan)

    client = Anthropic(api_key=api_key)

    try:
        plan_json = json.dumps(execution_plan, indent=2, default=str)
        user_prompt = GENERATE_ACTIONS_USER_PROMPT.format(
            task_description=task_description,
            plan_json=plan_json,
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system=GENERATE_ACTIONS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        tool_inputs_data = _parse_json(response.content[0].text.strip())
        tool_input_map = {
            item["step_id"]: item["tool_input"]
            for item in tool_inputs_data
        }

        updated_plan: list[ExecutionStep] = []
        generated_actions: list[str] = []

        for step in execution_plan:
            step_id = step["step_id"]
            tool_input = tool_input_map.get(step_id, {})
            updated_plan.append({**step, "tool_input": tool_input})
            generated_actions.append(
                f"{step['action']} (tool: {step.get('tool') or 'none'})"
            )

        return {
            **state,
            "execution_plan": updated_plan,
            "generated_actions": generated_actions,
            "logs": [
                *state.get("logs", []),
                f"Generated tool inputs for {len(updated_plan)} step(s)",
            ],
        }

    except Exception as e:
        return {
            **_fallback_generate(state, execution_plan),
            "logs": [
                *state.get("logs", []),
                f"Generate LLM failed ({e}), using fallback",
            ],
        }


def _fallback_generate(state: ExecutionState, execution_plan: list) -> ExecutionState:
    """Fallback: derive tool_input by copying params directly."""
    updated_plan: list[ExecutionStep] = []
    generated_actions: list[str] = []

    for step in execution_plan:
        tool = step.get("tool")
        params = step.get("params", {})

        if tool == "file_read":
            tool_input = {"path": params.get("path", "")}
        elif tool == "file_write":
            tool_input = {
                "path": params.get("path", ""),
                "content": params.get("content", ""),
            }
        else:
            tool_input = {}

        updated_plan.append({**step, "tool_input": tool_input})
        generated_actions.append(
            f"{step['action']} (tool: {tool or 'none'})"
        )

    return {
        **state,
        "execution_plan": updated_plan,
        "generated_actions": generated_actions,
        "logs": [
            *state.get("logs", []),
            f"Fallback generate: {len(updated_plan)} step(s)",
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
