"""Prompt templates for Research Subgraph.

Phase 2: Real LLM prompts for query normalization, source filtering, and evidence synthesis.
"""

# Query normalization prompt (Phase 2A: supports both simple and structured extraction)
NORMALIZE_QUERY_SYSTEM_PROMPT = """You are a research query optimizer. Your task is to analyze user queries and produce structured research queries.

Extract:
1. normalized_text: Clear, focused search query
2. key_concepts: Important terms/entities (2-5 items)
3. query_intent: Type of research
   - "factual": Seeks specific facts/data
   - "analytical": Seeks understanding/analysis
   - "exploratory": Open-ended investigation
   - "general": Broad overview
4. scope: Breadth of research
   - "narrow": Specific topic/question
   - "moderate": Related topics
   - "broad": Wide-ranging exploration

Return ONLY a JSON object with this structure."""

NORMALIZE_QUERY_USER_PROMPT = """Original query: {research_query}

Context: {context}

Analyze this query and return JSON:
{{
  "normalized_text": "clear search query",
  "key_concepts": ["concept1", "concept2"],
  "query_intent": "factual|analytical|exploratory|general",
  "scope": "narrow|moderate|broad"
}}"""

# Fallback simple prompt (backward compatibility)
NORMALIZE_QUERY_SIMPLE_SYSTEM_PROMPT = """You are a research query optimizer. Your task is to rewrite user queries into clear, focused search queries that will retrieve the most relevant information.

Guidelines:
1. Preserve the core intent and information need
2. Remove ambiguous or vague language
3. Add specificity where needed
4. Use clear, concise language
5. Focus on factual, searchable terms

Return ONLY the normalized query text, nothing else."""

NORMALIZE_QUERY_SIMPLE_USER_PROMPT = """Original query: {research_query}

Context: {context}

Rewrite this into a clear, focused search query:"""

# Source filtering prompt
FILTER_SOURCES_SYSTEM_PROMPT = """You are a research source evaluator. Your task is to assess the relevance of sources to a research query.

For each source, evaluate:
1. Relevance to the query (0.0-1.0)
2. Whether the content directly addresses the research question
3. Quality and specificity of information

Return a JSON array with assessments for each source."""

FILTER_SOURCES_USER_PROMPT = """Research query: {normalized_query}

Sources to evaluate:
{sources_text}

For each source, provide a relevance score (0.0-1.0) and brief reason.
Return a JSON array: [{{"source_index": 0, "relevance": 0.8, "reason": "..."}}]"""

# Evidence synthesis prompt
SYNTHESIZE_EVIDENCE_SYSTEM_PROMPT = """You are a research synthesizer. Your task is to extract structured evidence from multiple sources and synthesize coherent findings.

If prior conversation context is provided, use it to make your synthesis more relevant and specific to what the user is actually asking about. For example, if the user previously asked to create a file and now asks what file was created, use the conversation history to answer directly.

Your output must include:
1. Evidence claims (specific factual statements)
2. Citations (which source supports each claim)
3. Overall confidence (0.0-1.0 based on source agreement and quality)
4. Open questions (what remains unclear or needs further research)

Return ONLY a JSON object with this structure:
{
  "evidence": ["claim 1", "claim 2", ...],
  "citations": ["Source Title 1", "Source Title 2", ...],
  "confidence": 0.75,
  "open_questions": ["question 1", ...]
}"""

SYNTHESIZE_EVIDENCE_USER_PROMPT = """Research query: {normalized_query}
{workspace_context}{conversation_history}
Filtered sources:
{sources_text}

Extract structured evidence and synthesize findings. Return JSON only:"""
