"""Normalize Query node for Research Subgraph.

Uses LLM to rewrite user query into clear, focused search query.
Phase 2: Real Claude integration via Anthropic SDK.
Phase 2A: Enhanced with structured query extraction.
"""

import os
import json
from anthropic import Anthropic

from ..state import ResearchState, StructuredQuery
from ..prompts.templates import (
    NORMALIZE_QUERY_SYSTEM_PROMPT,
    NORMALIZE_QUERY_USER_PROMPT,
    NORMALIZE_QUERY_SIMPLE_SYSTEM_PROMPT,
    NORMALIZE_QUERY_SIMPLE_USER_PROMPT,
)


def normalize_query(state: ResearchState) -> ResearchState:
    """Normalize research query using Claude LLM.

    Phase 2A: Attempts structured query extraction with fallback to simple normalization.

    Tries to extract:
    1. Structured query (normalized_text, key_concepts, intent, scope)
    2. Falls back to simple normalized text
    3. Falls back to original query if LLM unavailable

    Args:
        state: Current ResearchState

    Returns:
        Updated ResearchState with normalized_query and structured_query set
    """
    research_query = state["research_query"]
    context = state.get("context", {})
    context_str = str(context) if context else "None"

    # Check for API key first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No LLM available: use original query
        return {
            **state,
            "normalized_query": research_query,
            "structured_query": None,
            "logs": [
                *state.get("logs", []),
                "No ANTHROPIC_API_KEY found, using original query"
            ],
        }

    client = Anthropic(api_key=api_key)

    # Try structured extraction first (Phase 2A)
    try:
        user_prompt = NORMALIZE_QUERY_USER_PROMPT.format(
            research_query=research_query,
            context=context_str
        )

        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=800,
            system=NORMALIZE_QUERY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )

        response_text = response.content[0].text.strip()

        # Parse JSON response
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            json_text = response_text[json_start:json_end].strip()
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            json_text = response_text[json_start:json_end].strip()
        else:
            json_text = response_text

        structured_data = json.loads(json_text)

        # Create StructuredQuery
        structured_query: StructuredQuery = {
            "normalized_text": structured_data["normalized_text"],
            "key_concepts": structured_data.get("key_concepts", []),
            "query_intent": structured_data.get("query_intent", "general"),
            "scope": structured_data.get("scope", "moderate"),
        }

        return {
            **state,
            "normalized_query": structured_query["normalized_text"],
            "structured_query": structured_query,
            "logs": [
                *state.get("logs", []),
                f"Structured query extracted: '{structured_query['normalized_text']}' "
                f"(intent: {structured_query['query_intent']}, scope: {structured_query['scope']})"
            ],
        }

    except Exception as e:
        # Structured extraction failed, try simple normalization (Phase 2 fallback)
        try:
            user_prompt = NORMALIZE_QUERY_SIMPLE_USER_PROMPT.format(
                research_query=research_query,
                context=context_str
            )

            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=500,
                system=NORMALIZE_QUERY_SIMPLE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )

            normalized_query = response.content[0].text.strip()

            return {
                **state,
                "normalized_query": normalized_query,
                "structured_query": None,
                "logs": [
                    *state.get("logs", []),
                    f"Simple normalization succeeded: '{normalized_query}' (structured extraction failed: {str(e)})"
                ],
            }

        except Exception as e2:
            # Both structured and simple extraction failed: use original query
            return {
                **state,
                "normalized_query": research_query,
                "structured_query": None,
                "logs": [
                    *state.get("logs", []),
                    f"Query normalization failed ({str(e2)}), using original query"
                ],
            }
