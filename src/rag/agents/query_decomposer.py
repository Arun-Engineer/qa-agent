"""src/rag/agents/query_decomposer.py — Query Decomposition Agent.

Breaks complex multi-part queries into sub-queries. Uses connector-based
splitting (and/also/plus/then/,) as a fast path; falls back to LLM when enabled.
"""
from __future__ import annotations
import re
import structlog
from dataclasses import dataclass

logger = structlog.get_logger()

_CONNECTORS = re.compile(
    r"\s+(?:and also|also|and then|then|plus|as well as|and|;)\s+|,\s+",
    re.IGNORECASE,
)
_COMPLEX_SIGNALS = re.compile(
    r"\b(and|also|plus|then|as well as|both|either|neither|multiple|each)\b",
    re.IGNORECASE,
)


@dataclass
class DecomposedQuery:
    original: str
    sub_queries: list[str]
    reasoning: str
    is_complex: bool


class QueryDecomposer:
    _PROMPT = (
        "Split this query into independent atomic sub-queries that together answer "
        "the original. Preserve all constraints. If already atomic, return just the "
        "original.\nQuery: {query}\nRespond ONLY with JSON: "
        '{{"sub_queries": ["..."], "reasoning": "..."}}'
    )

    def __init__(self, llm_provider=None, min_tokens: int = 7, use_llm: bool = False):
        self._llm = llm_provider
        self.min_tokens = min_tokens
        self.use_llm = use_llm

    @property
    def llm(self):
        if self._llm is None:
            from src.llm.provider import get_llm
            self._llm = get_llm()
        return self._llm

    def _heuristic_split(self, query: str) -> tuple[list[str], str]:
        parts = [p.strip(" .,") for p in _CONNECTORS.split(query) if p.strip()]
        parts = [p for p in parts if len(p.split()) >= 2]
        if len(parts) >= 2:
            return parts, f"split on connectors into {len(parts)} sub-queries"
        return [query], "no natural split points"

    def _llm_split(self, query: str) -> tuple[list[str], str]:
        try:
            resp = self.llm.chat_json(
                [{"role": "user", "content": self._PROMPT.format(query=query)}],
                temperature=0.1,
            )
            subs = resp.get("sub_queries") or [query]
            subs = [s.strip() for s in subs if isinstance(s, str) and s.strip()]
            reason = str(resp.get("reasoning", ""))[:160] or "llm decomposition"
            return (subs or [query]), reason
        except Exception as e:
            logger.warning("decomposer_llm_failed", error=str(e))
            return self._heuristic_split(query)

    def decompose(self, query: str) -> DecomposedQuery:
        query = query.strip()
        tokens = query.split()
        if len(tokens) < self.min_tokens:
            return DecomposedQuery(original=query, sub_queries=[query],
                                   reasoning="short query", is_complex=False)

        has_signal = bool(_COMPLEX_SIGNALS.search(query))
        if not has_signal and len(tokens) < 15:
            return DecomposedQuery(original=query, sub_queries=[query],
                                   reasoning="no complexity signals", is_complex=False)

        subs, reason = (self._llm_split(query) if self.use_llm
                        else self._heuristic_split(query))
        is_complex = len(subs) > 1
        return DecomposedQuery(original=query, sub_queries=subs,
                               reasoning=reason, is_complex=is_complex)
