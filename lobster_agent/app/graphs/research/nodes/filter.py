"""Filter node for Research Subgraph.

Filters sources by relevance using LLM-based assessment.
Phase 2: Real Claude integration for intelligent filtering.
Phase 2A: Enhanced with explainable FilterDecision objects.
"""

import os
import json
from anthropic import Anthropic

from ..state import ResearchState, Source, FilterDecision
from ..prompts.templates import (
    FILTER_SOURCES_SYSTEM_PROMPT,
    FILTER_SOURCES_USER_PROMPT,
)


def filter_sources(state: ResearchState) -> ResearchState:
    """Filter sources by relevance using LLM assessment.

    Phase 2: Uses Claude to intelligently assess source relevance.

    Processes sources in batches of up to 10, using LLM to score relevance.
    Keeps sources with relevance >= 0.5. Falls back to keeping all sources
    if LLM fails.

    Args:
        state: Current ResearchState

    Returns:
        Updated ResearchState with filtered_sources populated
    """
    sources = state.get("sources", [])
    normalized_query = state.get("normalized_query") or state["research_query"]

    if not sources:
        return {
            **state,
            "filtered_sources": [],
            "filter_decisions": [],
            "logs": [
                *state.get("logs", []),
                "No sources to filter"
            ],
        }

    try:
        # Check for API key
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            # Fallback: filter based on reliability_score threshold (0.6)
            threshold = 0.6
            filtered = []
            filter_decisions = []

            for idx, source in enumerate(sources):
                reliability = source.get("reliability_score", 0)
                keep = reliability >= threshold

                # Create FilterDecision
                decision: FilterDecision = {
                    "source_index": idx,
                    "keep": keep,
                    "relevance_score": reliability,
                    "reasoning": f"Reliability-based: {reliability:.2f} {'≥' if keep else '<'} {threshold}"
                }
                filter_decisions.append(decision)

                if keep:
                    filtered.append(source)

            return {
                **state,
                "filtered_sources": filtered,
                "filter_decisions": filter_decisions,
                "logs": [
                    *state.get("logs", []),
                    f"No API key, using reliability-based filtering: {len(filtered)}/{len(sources)} sources"
                ],
            }

        client = Anthropic(api_key=api_key)

        # Process sources in batches of up to 10
        batch_size = 10
        all_assessments = []

        for i in range(0, len(sources), batch_size):
            batch = sources[i:i + batch_size]

            # Format sources for prompt
            sources_text = "\n\n".join([
                f"[{idx}] Title: {s['title']}\nContent preview: {s['content'][:200]}..."
                for idx, s in enumerate(batch, start=i)
            ])

            user_prompt = FILTER_SOURCES_USER_PROMPT.format(
                normalized_query=normalized_query,
                sources_text=sources_text
            )

            # Call Claude for relevance assessment
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                system=FILTER_SOURCES_SYSTEM_PROMPT,
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

            batch_assessments = json.loads(json_text)
            all_assessments.extend(batch_assessments)

        # Apply filtering based on LLM assessments
        threshold = 0.5
        filtered_sources = []
        filter_decisions = []

        # Create FilterDecision for each assessment
        for assessment in all_assessments:
            source_idx = assessment.get("source_index", 0)
            relevance = assessment.get("relevance", 0.0)
            reason = assessment.get("reason", "No reason provided")

            if source_idx < len(sources):
                keep = relevance >= threshold

                # Create FilterDecision
                decision: FilterDecision = {
                    "source_index": source_idx,
                    "keep": keep,
                    "relevance_score": relevance,
                    "reasoning": f"LLM assessment: {reason}"
                }
                filter_decisions.append(decision)

                if keep:
                    # Update source with LLM-assessed relevance
                    source = sources[source_idx].copy()
                    source["metadata"] = {
                        **source.get("metadata", {}),
                        "llm_relevance": relevance,
                        "llm_reason": reason
                    }
                    filtered_sources.append(source)

        return {
            **state,
            "filtered_sources": filtered_sources,
            "filter_decisions": filter_decisions,
            "logs": [
                *state.get("logs", []),
                f"LLM filtering: {len(filtered_sources)}/{len(sources)} sources passed (threshold: {threshold})"
            ],
        }

    except Exception as e:
        # Fallback: filter based on reliability_score threshold (0.6)
        threshold = 0.6
        filtered = []
        filter_decisions = []

        for idx, source in enumerate(sources):
            reliability = source.get("reliability_score", 0)
            keep = reliability >= threshold

            # Create FilterDecision
            decision: FilterDecision = {
                "source_index": idx,
                "keep": keep,
                "relevance_score": reliability,
                "reasoning": f"LLM failed, reliability fallback: {reliability:.2f} {'≥' if keep else '<'} {threshold}"
            }
            filter_decisions.append(decision)

            if keep:
                filtered.append(source)

        return {
            **state,
            "filtered_sources": filtered,
            "filter_decisions": filter_decisions,
            "logs": [
                *state.get("logs", []),
                f"LLM filtering failed ({str(e)}), using reliability fallback: {len(filtered)}/{len(sources)} sources"
            ],
        }
