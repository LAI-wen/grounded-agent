"""Synthesize Evidence node for Research Subgraph.

Uses LLM to extract structured evidence and synthesize findings from sources.
Phase 2: Real Claude integration for intelligent evidence synthesis.

Hybrid routing (factual+web path):
  When the query is factual/definitional AND at least one web source is present
  AND qwen2.5:7b is available in Ollama, synthesis runs locally via the Ollama
  shim.  All other paths (project-state/next-step queries, local-only sources,
  qwen unavailable) use the cloud (Anthropic) client.  If the local call fails
  or returns unparseable output, one retry with the cloud client is attempted
  before falling back to _fallback_synthesis.
"""

import os
import json
from anthropic import Anthropic

from ..state import ResearchState
from ..prompts.templates import (
    SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT,
    SYNTHESIZE_EVIDENCE_USER_PROMPT,
)


# ---------------------------------------------------------------------------
# Hybrid routing — constants
# ---------------------------------------------------------------------------

_LOCAL_MODEL   = "qwen2.5:7b"
_LOCAL_TIMEOUT = 90   # seconds; web queries have network latency

_PROGRESS_KEYWORDS = frozenset({
    "what should i do", "next step", "next steps",
    "what to do next", "current state", "state of",
    "status of", "what have we", "recommend", "what's the plan",
})

# Module-level cache so Ollama is probed at most once per process.
_qwen_cache: dict = {"checked": False, "available": False}


# ---------------------------------------------------------------------------
# Ollama client shim
# ---------------------------------------------------------------------------

class _OllamaContent:
    def __init__(self, text: str):
        self.text = text


class _OllamaResponse:
    def __init__(self, text: str):
        self.content = [_OllamaContent(text)]


class _OllamaMessages:
    def __init__(self, model: str, timeout: int):
        self._model   = model
        self._timeout = timeout

    def create(self, model=None, max_tokens=None, system=None,
               messages=None, **kwargs) -> "_OllamaResponse":
        import requests as _req
        payload = {
            "model":    self._model,
            "messages": [{"role": "system", "content": system or ""}]
                        + (messages or []),
            "stream":   False,
            "options":  {"num_predict": max_tokens or 3000, "temperature": 0},
        }
        resp = _req.post(
            "http://localhost:11434/api/chat",
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return _OllamaResponse(resp.json()["message"]["content"])


class _OllamaClient:
    def __init__(self, model: str = _LOCAL_MODEL, timeout: int = _LOCAL_TIMEOUT):
        self.messages = _OllamaMessages(model, timeout)


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _qwen_available() -> bool:
    """Return True if qwen2.5:7b is reachable in Ollama. Cached per process."""
    if _qwen_cache["checked"]:
        return _qwen_cache["available"]
    available = False
    try:
        import requests as _req
        r    = _req.get("http://localhost:11434/api/tags", timeout=3)
        tags = r.json().get("models", [])
        available = any(
            t.get("name", "").startswith(_LOCAL_MODEL.split(":")[0])
            for t in tags
        )
    except Exception:
        pass
    _qwen_cache["checked"]   = True
    _qwen_cache["available"] = available
    return available


def _has_web_sources(sources: list) -> bool:
    """Return True if any source came from the web_search adapter."""
    return any(
        s.get("metadata", {}).get("adapter") == "web_search"
        for s in sources
    )


def _routing_decision(state: ResearchState) -> tuple:
    """Return (path, skip_reason).

    path:        "local" | "cloud"
    skip_reason: empty string when path == "local";
                 one of "no web sources", "progress query", "qwen unavailable"
                 when path == "cloud".
    Gates are ordered cheapest-first: list scan → string scan → network call.
    """
    if not _has_web_sources(state.get("filtered_sources", [])):
        return "cloud", "no web sources"

    query = (
        state.get("normalized_query") or state.get("research_query", "")
    ).lower()
    if any(kw in query for kw in _PROGRESS_KEYWORDS):
        return "cloud", "progress query"

    if not _qwen_available():
        return "cloud", "qwen unavailable"

    return "local", ""


# ---------------------------------------------------------------------------
# Preference helpers
# ---------------------------------------------------------------------------

def _extract_response_length_pref(workspace_context: str) -> "str | None":
    """Return 'concise', 'detailed', or None from the rendered workspace_context block."""
    if "Keep responses concise" in workspace_context:
        return "concise"
    if "Provide detailed responses" in workspace_context:
        return "detailed"
    return None


_CONCISE_INSTRUCTION = (
    "\n\nIMPORTANT: The user has requested concise responses. "
    "Hard limits: return at most 3 evidence claims; each claim must be 12 words or fewer. "
    "Prefer short noun phrases over full sentences."
)

_DETAILED_INSTRUCTION = (
    "\n\nIMPORTANT: The user has requested detailed responses. "
    "Provide thorough evidence with full explanations."
)


# ---------------------------------------------------------------------------
# Confidence over-claim guard
# ---------------------------------------------------------------------------

def _confidence_ceiling(n_sources: int, has_web: bool) -> tuple:
    """Return (ceiling, bucket_label) or (None, '') if no ceiling applies.

    This is an over-claim guard — it prevents the model from reporting
    overconfident synthesis when concrete source evidence is thin.  It is
    not a full calibration model.

    Buckets:
      ≤1 source                → 0.60
      2–3 sources, local-only  → 0.75
      2–3 sources, web present → 0.80
      4+ sources               → no ceiling
    """
    if n_sources <= 1:
        return 0.60, "<=1 source"
    if n_sources <= 3:
        if has_web:
            return 0.80, "2-3 sources with web"
        return 0.75, "2-3 local-only"
    return None, ""


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------

def _parse_synthesis_json(response_text: str) -> dict:
    """Extract and validate JSON from an LLM synthesis response.

    Handles markdown code-block wrapping.

    Raises:
        ValueError: if JSON cannot be parsed or required fields are absent
                    or wrong type.  Callers treat ValueError as a signal to
                    retry with the cloud client.
    """
    text = response_text.strip()

    if "```json" in text:
        start = text.find("```json") + 7
        end   = text.find("```", start)
        text  = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end   = text.find("```", start)
        text  = text[start:end].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(result.get("evidence"), list):
        raise ValueError(
            f"'evidence' field missing or not a list (got {type(result.get('evidence')).__name__})"
        )
    if not isinstance(result.get("confidence"), (int, float)):
        raise ValueError(
            f"'confidence' field missing or not a number (got {type(result.get('confidence')).__name__})"
        )

    return result


def _attempt_synthesis(client, system_prompt: str, user_prompt: str) -> dict:
    """Call client.messages.create and return the parsed synthesis dict.

    Raises:
        Exception:  if the API/network call fails.
        ValueError: if the response cannot be parsed into the expected shape.
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _parse_synthesis_json(response.content[0].text)


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------

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
        role    = m.get("role", "unknown")
        content = str(m.get("content", ""))[:300]
        lines.append(f"[{role}] {content}")
    return "\n" + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------

def synthesize_evidence(state: ResearchState) -> ResearchState:
    """Synthesize evidence from filtered sources using LLM.

    Phase 2: Uses Claude (cloud) or qwen2.5:7b (local) to extract and
    synthesize evidence.  Local routing fires only for factual/definitional
    queries when web sources are present and qwen2.5:7b is available.

    Falls back to simple extraction if all LLM paths fail.
    """
    filtered_sources = state.get("filtered_sources", [])
    normalized_query = state.get("normalized_query") or state["research_query"]

    context           = state.get("context") or {}
    workspace_context = context.get("workspace_context", "")

    if not filtered_sources and not workspace_context:
        return {
            **state,
            "evidence":       [],
            "summary":        "No sources available for synthesis",
            "confidence":     0.0,
            "open_questions": ["No data retrieved for this query"],
            "logs": [
                *state.get("logs", []),
                "No filtered sources available for synthesis",
            ],
        }

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return _fallback_synthesis(state, filtered_sources, normalized_query)

        # Build prompts (shared by both local and cloud paths)
        sources_text = "\n\n".join([
            f"Source {idx + 1}: {s['title']}\n{s['content'][:500]}..."
            for idx, s in enumerate(filtered_sources[:10])
        ]) if filtered_sources else "(no file sources retrieved)"

        messages             = context.get("messages", [])
        conversation_history = _format_conversation_history(messages)
        workspace_context_block = f"\n{workspace_context}\n" if workspace_context else ""

        # Build system prompt — append hard length instruction when preference active
        pref = _extract_response_length_pref(workspace_context)
        if pref == "concise":
            system_prompt = SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT + _CONCISE_INSTRUCTION
        elif pref == "detailed":
            system_prompt = SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT + _DETAILED_INSTRUCTION
        else:
            system_prompt = SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT

        # Append confidence over-claim guard instruction when source evidence is thin
        has_web = _has_web_sources(filtered_sources)
        ceiling, bucket = _confidence_ceiling(len(filtered_sources), has_web)
        if ceiling is not None:
            system_prompt += (
                f"\n\nIMPORTANT: Only {len(filtered_sources)} source(s) available. "
                f"Confidence must not exceed {ceiling:.2f}."
            )

        if filtered_sources and workspace_context:
            source_log = (
                f"Synthesizing from {len(filtered_sources)} file source(s) "
                "and workspace history (combined)"
            )
        elif filtered_sources:
            source_log = f"Synthesizing from {len(filtered_sources)} file source(s)"
        else:
            source_log = "Synthesizing from workspace history only (no file sources)"

        user_prompt = SYNTHESIZE_EVIDENCE_USER_PROMPT.format(
            normalized_query=normalized_query,
            sources_text=sources_text,
            conversation_history=conversation_history,
            workspace_context=workspace_context_block,
        )

        # Routing decision
        path, skip_reason = _routing_decision(state)

        if path == "local":
            try:
                synthesis_result = _attempt_synthesis(
                    _OllamaClient(),
                    system_prompt,
                    user_prompt,
                )
                path_log = f"synthesis: local ({_LOCAL_MODEL})"
            except Exception as exc:
                # Local call failed (network error) or response unparseable —
                # retry once with cloud before giving up.
                synthesis_result = _attempt_synthesis(
                    Anthropic(api_key=api_key),
                    system_prompt,
                    user_prompt,
                )
                path_log = (
                    f"synthesis: local→cloud fallback "
                    f"({type(exc).__name__}: {str(exc)[:60]})"
                )
        else:
            synthesis_result = _attempt_synthesis(
                Anthropic(api_key=api_key),
                system_prompt,
                user_prompt,
            )
            reason   = f" ({skip_reason})" if skip_reason else ""
            path_log = f"synthesis: cloud{reason}"

        # Extract fields
        evidence            = synthesis_result.get("evidence", [])
        citations           = synthesis_result.get("citations", [])
        confidence          = float(synthesis_result.get("confidence", 0.5))
        open_questions      = synthesis_result.get("open_questions", [])
        suggested_next_step = synthesis_result.get("suggested_next_step") or None

        confidence = max(0.0, min(1.0, confidence))

        # Post-parse confidence over-claim guard — enforces ceiling regardless of
        # model output, complementing the system-prompt instruction above.
        clamp_log = None
        if ceiling is not None and confidence > ceiling:
            original_confidence = confidence
            confidence = ceiling
            clamp_log = (
                f"confidence over-claim guard: {original_confidence:.2f} → "
                f"{ceiling:.2f} (bucket: {bucket})"
            )

        return {
            **state,
            "evidence":            evidence,
            "citations":           citations,
            "confidence":          confidence,
            "open_questions":      open_questions,
            "suggested_next_step": suggested_next_step,
            "logs": [
                *state.get("logs", []),
                source_log,
                path_log,
                *([] if clamp_log is None else [clamp_log]),
                f"Synthesized {len(evidence)} evidence claims (confidence: {confidence:.2f})",
            ],
        }

    except Exception as e:
        return {
            **_fallback_synthesis(state, filtered_sources, normalized_query),
            "logs": [
                *state.get("logs", []),
                f"LLM synthesis failed ({str(e)}), using fallback extraction",
            ],
        }


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_synthesis(state: ResearchState, filtered_sources: list, query: str) -> dict:
    """Fallback synthesis when LLM is unavailable."""
    evidence = [
        f"Information from {s['title']}: {s['content'][:100]}..."
        for s in filtered_sources[:5]
    ]
    citations = [s["title"] for s in filtered_sources]

    if filtered_sources:
        avg_reliability = sum(
            s.get("reliability_score", 0.5) for s in filtered_sources
        ) / len(filtered_sources)
        confidence = avg_reliability
    else:
        confidence = 0.0

    open_questions = [f"Further investigation needed on: {query}"]

    return {
        **state,
        "evidence":       evidence,
        "citations":      citations,
        "confidence":     confidence,
        "open_questions": open_questions,
        "logs": [
            *state.get("logs", []),
            f"Fallback synthesis: {len(evidence)} evidence from {len(filtered_sources)} sources",
        ],
    }
