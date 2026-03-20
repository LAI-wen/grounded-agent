"""Synthesize Evidence node for Research Subgraph.

Uses LLM to extract structured evidence and synthesize findings from sources.
Phase 2: Real Claude integration for intelligent evidence synthesis.
"""

import os
import json
from anthropic import Anthropic

from ..state import ResearchState
from ..prompts.templates import (
    SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT,
    SYNTHESIZE_EVIDENCE_USER_PROMPT,
)


def _format_conversation_history(messages: list) -> str:
    """Format up to the last 4 messages as context for synthesis.

    Returns an empty string when there are no prior messages so the prompt
    template renders cleanly without extra whitespace.
    """
    if not messages:
        return ""
    recent = messages[-4:]
    lines = ["Prior conversation context:"]
    for m in recent:
        role = m.get("role", "unknown")
        content = str(m.get("content", ""))[:300]
        lines.append(f"[{role}] {content}")
    return "\n" + "\n".join(lines) + "\n"


def synthesize_evidence(state: ResearchState) -> ResearchState:
    """Synthesize evidence from filtered sources using LLM.

    Phase 2: Uses Claude to intelligently extract and synthesize evidence.

    Takes filtered sources and uses LLM to:
    1. Extract specific evidence claims
    2. Map citations to sources
    3. Assess overall confidence
    4. Identify open questions

    Falls back to simple extraction if LLM fails.

    Args:
        state: Current ResearchState

    Returns:
        Updated ResearchState with evidence, citations, confidence, open_questions
    """
    filtered_sources = state.get("filtered_sources", [])
    normalized_query = state.get("normalized_query") or state["research_query"]

    # Extract context fields
    context = state.get("context") or {}
    workspace_context = context.get("workspace_context", "")

    if not filtered_sources and not workspace_context:
        # No sources and no workspace context — nothing to synthesize
        return {
            **state,
            "evidence": [],
            "summary": "No sources available for synthesis",
            "confidence": 0.0,
            "open_questions": ["No data retrieved for this query"],
            "logs": [
                *state.get("logs", []),
                "No filtered sources available for synthesis"
            ],
        }

    try:
        # Check for API key
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            # Fallback to simple extraction
            return _fallback_synthesis(state, filtered_sources, normalized_query)

        client = Anthropic(api_key=api_key)

        # Format sources for prompt
        sources_text = "\n\n".join([
            f"Source {idx + 1}: {s['title']}\n{s['content'][:500]}..."
            for idx, s in enumerate(filtered_sources[:10])  # Limit to 10 sources for token efficiency
        ]) if filtered_sources else "(no file sources retrieved)"

        # Extract conversation history and workspace context from context
        messages = context.get("messages", [])
        conversation_history = _format_conversation_history(messages)
        # Format workspace_context with a trailing newline so prompt renders cleanly
        workspace_context_block = f"\n{workspace_context}\n" if workspace_context else ""

        user_prompt = SYNTHESIZE_EVIDENCE_USER_PROMPT.format(
            normalized_query=normalized_query,
            sources_text=sources_text,
            conversation_history=conversation_history,
            workspace_context=workspace_context_block,
        )

        # Call Claude for evidence synthesis
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system=SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
        )

        # Parse JSON response
        response_text = response.content[0].text.strip()

        # Extract JSON from response (handle markdown code blocks)
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

        synthesis_result = json.loads(json_text)

        # Extract fields
        evidence = synthesis_result.get("evidence", [])
        citations = synthesis_result.get("citations", [])
        confidence = float(synthesis_result.get("confidence", 0.5))
        open_questions = synthesis_result.get("open_questions", [])

        # Ensure confidence is in valid range
        confidence = max(0.0, min(1.0, confidence))

        return {
            **state,
            "evidence": evidence,
            "citations": citations,
            "confidence": confidence,
            "open_questions": open_questions,
            "logs": [
                *state.get("logs", []),
                f"Synthesized {len(evidence)} evidence claims from {len(filtered_sources)} sources (confidence: {confidence:.2f})"
            ],
        }

    except Exception as e:
        # Fallback to simple extraction
        return {
            **_fallback_synthesis(state, filtered_sources, normalized_query),
            "logs": [
                *state.get("logs", []),
                f"LLM synthesis failed ({str(e)}), using fallback extraction"
            ],
        }


def _fallback_synthesis(state: ResearchState, filtered_sources: list, query: str) -> dict:
    """Fallback synthesis when LLM is unavailable.

    Args:
        state: Current ResearchState
        filtered_sources: List of filtered sources
        query: Research query

    Returns:
        State update dict with simple extraction
    """
    # Simple evidence extraction: one claim per source
    evidence = [
        f"Information from {s['title']}: {s['content'][:100]}..."
        for s in filtered_sources[:5]
    ]

    # Extract citations
    citations = [s['title'] for s in filtered_sources]

    # Simple confidence: average reliability
    if filtered_sources:
        avg_reliability = sum(
            s.get("reliability_score", 0.5) for s in filtered_sources
        ) / len(filtered_sources)
        confidence = avg_reliability
    else:
        confidence = 0.0

    # Generic open questions
    open_questions = [
        f"Further investigation needed on: {query}"
    ]

    return {
        **state,
        "evidence": evidence,
        "citations": citations,
        "confidence": confidence,
        "open_questions": open_questions,
        "logs": [
            *state.get("logs", []),
            f"Fallback synthesis: {len(evidence)} evidence from {len(filtered_sources)} sources"
        ],
    }
