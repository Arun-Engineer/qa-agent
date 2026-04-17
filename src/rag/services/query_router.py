"""services/query_router.py — Intelligent Query Router"""
from __future__ import annotations
import re, structlog
from dataclasses import dataclass
from enum import Enum
logger = structlog.get_logger()

class RouteStrategy(str, Enum):
    VECTOR = "vector"; BM25 = "bm25"; HYBRID = "hybrid"; DIRECT_LLM = "direct"; CODE_SEARCH = "code"

@dataclass
class RouteDecision:
    strategy: RouteStrategy; confidence: float; reason: str; modified_query: str

class QueryRouter:
    KEYWORD_PATTERNS = [r"error\s+\d+", r"status\s*code\s*\d+", r"[A-Z_]{3,}", r"\b\w+\.(py|js|ts|html|css|json|yaml|yml|toml)\b", r"\b(traceback|stacktrace|exception)\b", r"[\w.]+Error\b", r"\bCVE-\d{4}-\d+\b", r"\b[A-Z]+-\d+\b"]
    DIRECT_PATTERNS = [r"^(what is|what are|explain|define|describe)\s", r"^(how do|how does|how to|how can)\s", r"^(why|when|where)\s.{0,30}$", r"^(hi|hello|hey|thanks|thank you)"]
    CODE_PATTERNS = [r"(function|method|class|def|import)\s+\w+", r"(implement|refactor|debug|fix)\s+(the|this|my)\s", r"\b(test_\w+|test\s+case|test\s+file)\b"]

    def __init__(self, use_llm_fallback: bool = True):
        self._ckw = [re.compile(p, re.IGNORECASE) for p in self.KEYWORD_PATTERNS]
        self._cdir = [re.compile(p, re.IGNORECASE) for p in self.DIRECT_PATTERNS]
        self._ccode = [re.compile(p, re.IGNORECASE) for p in self.CODE_PATTERNS]

    def route(self, query: str) -> RouteDecision:
        query = query.strip()
        if not query: return RouteDecision(strategy=RouteStrategy.DIRECT_LLM, confidence=1.0, reason="empty", modified_query=query)
        kw = sum(1 for p in self._ckw if p.search(query))
        dr = sum(1 for p in self._cdir if p.search(query))
        co = sum(1 for p in self._ccode if p.search(query))
        if kw >= 2: return RouteDecision(strategy=RouteStrategy.BM25, confidence=0.9, reason=f"{kw} keyword patterns", modified_query=query)
        if co >= 1: return RouteDecision(strategy=RouteStrategy.CODE_SEARCH, confidence=0.8, reason=f"{co} code patterns", modified_query=query)
        if dr >= 1 and len(query.split()) <= 8: return RouteDecision(strategy=RouteStrategy.DIRECT_LLM, confidence=0.7, reason="simple conversational", modified_query=query)
        if kw == 1: return RouteDecision(strategy=RouteStrategy.HYBRID, confidence=0.75, reason="mixed signals", modified_query=query)
        if len(query.split()) > 12: return RouteDecision(strategy=RouteStrategy.VECTOR, confidence=0.7, reason="complex query", modified_query=query)
        return RouteDecision(strategy=RouteStrategy.HYBRID, confidence=0.6, reason="general query", modified_query=query)

    def route_batch(self, queries: list[str]) -> list[RouteDecision]:
        return [self.route(q) for q in queries]
