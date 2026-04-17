"""services/query_rewriter.py — Query Optimization & Expansion"""
from __future__ import annotations
import structlog
from dataclasses import dataclass
logger = structlog.get_logger()

@dataclass
class RewrittenQuery:
    original: str; rewritten: str; sub_queries: list[str]; strategy: str; confidence: float = 1.0

class QueryRewriter:
    REWRITE_PROMPT = "You are a QA testing search query optimizer. Rewrite the query to improve retrieval.\nOriginal query: {query}\nRespond ONLY with JSON: {{\"rewritten\": \"...\", \"sub_queries\": [...], \"strategy\": \"expansion|decomposition|passthrough\", \"confidence\": 0.0-1.0}}"
    def __init__(self, llm_provider=None, min_query_length: int = 5):
        self._llm = llm_provider; self.min_query_length = min_query_length
    @property
    def llm(self):
        if self._llm is None:
            from src.llm.provider import get_llm; self._llm = get_llm()
        return self._llm
    def rewrite(self, query: str) -> RewrittenQuery:
        query = query.strip()
        if len(query) < self.min_query_length or len(query.split()) <= 3:
            return RewrittenQuery(original=query, rewritten=query, sub_queries=[query], strategy="passthrough")
        try:
            resp = self.llm.chat_json([{"role":"user","content":self.REWRITE_PROMPT.format(query=query)}], temperature=0.1)
            return RewrittenQuery(original=query, rewritten=resp.get("rewritten",query), sub_queries=resp.get("sub_queries",[query]), strategy=resp.get("strategy","expansion"), confidence=float(resp.get("confidence",0.8)))
        except Exception:
            return RewrittenQuery(original=query, rewritten=query, sub_queries=[query], strategy="passthrough", confidence=0.5)
