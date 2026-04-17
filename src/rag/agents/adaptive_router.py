"""src/rag/agents/adaptive_router.py — Adaptive Source Selection Agent.

Scores each configured source against the query using:
  1. Keyword overlap with source `strengths` / `description`
  2. Source-type heuristics (code tokens -> code_index, error codes -> bug_database, etc.)
  3. Historical feedback (quality scores for past (query_type, source) pairs)

Returns a ranked list of sources + confidence + fallbacks.
"""
from __future__ import annotations
import re
import time
import structlog
from collections import defaultdict
from dataclasses import dataclass, field

logger = structlog.get_logger()


@dataclass
class SourceConfig:
    name: str
    description: str
    strengths: list[str]
    source_type: str
    is_available: bool = True


@dataclass
class RoutingDecision:
    selected_sources: list[str]
    reasoning: str
    confidence: float
    estimated_quality: float
    fallback_sources: list[str] = field(default_factory=list)


DEFAULT_SOURCES = [
    SourceConfig(name="qa_knowledge_base", description="Vector store of QA docs",
                 strengths=["methodology", "best practices", "how to", "guide"], source_type="vector"),
    SourceConfig(name="bug_database", description="SQL database of known bugs",
                 strengths=["error", "bug", "failure", "exception", "stacktrace"], source_type="sql"),
    SourceConfig(name="test_history", description="Previous test runs and results",
                 strengths=["past results", "previous", "history", "ran"], source_type="sql"),
    SourceConfig(name="code_index", description="Indexed application source code",
                 strengths=["function", "class", "method", "api endpoint", "import"], source_type="code"),
    SourceConfig(name="spec_archive", description="BM25 index of specifications",
                 strengths=["requirement", "spec", "acceptance criteria", "story"], source_type="bm25"),
]

_TYPE_PATTERNS = {
    "code": re.compile(r"\b(def |class |function |method |import |\.py|\.ts|\.js)\b", re.I),
    "sql": re.compile(r"\b(error|exception|traceback|[A-Z][a-zA-Z]*Error|\b\d{3}\b)\b"),
    "bm25": re.compile(r"\b[A-Z]{2,}[-_][A-Z0-9]+|[A-Z][A-Z0-9_]{3,}\b"),
}


class AdaptiveRouter:
    def __init__(self, sources: list[SourceConfig] | None = None, llm_provider=None,
                 top_n: int = 2, feedback_window: int = 500):
        self.sources = {s.name: s for s in (sources or DEFAULT_SOURCES)}
        self._llm = llm_provider
        self.top_n = top_n
        self._feedback: list[dict] = []
        self._feedback_window = feedback_window

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"\b\w{3,}\b", text.lower())}

    def _score_source(self, tokens: set[str], src: SourceConfig, query: str) -> tuple[float, list[str]]:
        reasons: list[str] = []
        strength_hits = sum(1 for s in src.strengths if any(w in tokens for w in s.lower().split()))
        desc_hits = sum(1 for w in self._tokens(src.description) if w in tokens)
        score = strength_hits * 0.4 + desc_hits * 0.1
        if strength_hits:
            reasons.append(f"{strength_hits} strength match(es)")

        pat = _TYPE_PATTERNS.get(src.source_type)
        if pat and pat.search(query):
            score += 0.3
            reasons.append(f"{src.source_type}-pattern")

        # Historical feedback boost (average quality for this source, recent)
        recent = [f for f in self._feedback if src.name in f["sources"]]
        if recent:
            avg = sum(f["quality"] for f in recent[-50:]) / min(len(recent), 50)
            score += (avg - 0.5) * 0.2
            reasons.append(f"history={avg:.2f}")

        return round(score, 3), reasons

    def route(self, query: str) -> RoutingDecision:
        available = [s for s in self.sources.values() if s.is_available]
        if not available:
            return RoutingDecision(selected_sources=[], reasoning="no sources available",
                                   confidence=0.0, estimated_quality=0.0)
        tokens = self._tokens(query)
        scored: list[tuple[float, SourceConfig, list[str]]] = []
        for s in available:
            score, reasons = self._score_source(tokens, s, query)
            scored.append((score, s, reasons))
        scored.sort(key=lambda x: x[0], reverse=True)

        top = scored[: self.top_n]
        selected = [s.name for _, s, _ in top]
        fallbacks = [s.name for _, s, _ in scored[self.top_n: self.top_n + 2]]

        top_score = top[0][0] if top else 0.0
        confidence = min(0.95, max(0.3, 0.5 + top_score / 2))
        est_quality = min(0.95, 0.5 + top_score / 3)

        reasoning_parts = [f"{s.name}({sc}): {', '.join(r) or 'default'}" for sc, s, r in top]
        return RoutingDecision(selected_sources=selected, reasoning="; ".join(reasoning_parts),
                               confidence=round(confidence, 2), estimated_quality=round(est_quality, 2),
                               fallback_sources=fallbacks)

    def record_feedback(self, query: str, sources_used: list[str], quality_score: float) -> None:
        self._feedback.append({"query": query, "sources": sources_used,
                               "quality": quality_score, "timestamp": time.time()})
        if len(self._feedback) > self._feedback_window:
            self._feedback = self._feedback[-self._feedback_window:]

    def stats(self) -> dict:
        by_source: dict[str, list[float]] = defaultdict(list)
        for f in self._feedback:
            for s in f["sources"]:
                by_source[s].append(f["quality"])
        return {
            "total_feedback": len(self._feedback),
            "sources": {name: {"count": len(vs), "avg_quality": round(sum(vs) / len(vs), 3)}
                        for name, vs in by_source.items()},
        }
