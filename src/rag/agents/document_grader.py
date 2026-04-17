"""src/rag/agents/document_grader.py — Document Relevance Grader.

Grades retrieved docs for relevance to a query. Uses the LLM if enabled,
falls back to token-overlap heuristic otherwise.
"""
from __future__ import annotations
import re
import structlog
from dataclasses import dataclass

logger = structlog.get_logger()


@dataclass
class GradeResult:
    doc_id: str
    is_relevant: bool
    score: float
    reason: str


class DocumentGrader:
    _GRADE_PROMPT = (
        "You are a relevance judge. Rate how well the document answers the query.\n"
        "Query: {query}\nDocument:\n{document}\n"
        "Respond ONLY with JSON: {{\"score\": <0.0-1.0>, \"reason\": \"<brief>\"}}"
    )

    def __init__(self, llm_provider=None, relevance_threshold: float = 0.5,
                 use_llm: bool = False):
        self._llm = llm_provider
        self.threshold = relevance_threshold
        self.use_llm = use_llm

    @property
    def llm(self):
        if self._llm is None:
            from src.llm.provider import get_llm
            self._llm = get_llm()
        return self._llm

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"\b\w{3,}\b", text.lower())}

    def _heuristic_grade(self, query: str, text: str) -> tuple[float, str]:
        q = self._tokens(query)
        d = self._tokens(text)
        if not q or not d:
            return 0.0, "empty tokens"
        overlap = len(q & d)
        score = overlap / max(len(q), 1)
        return round(min(score, 1.0), 3), f"token_overlap={overlap}/{len(q)}"

    def _llm_grade(self, query: str, text: str) -> tuple[float, str]:
        try:
            resp = self.llm.chat_json(
                [{"role": "user", "content": self._GRADE_PROMPT.format(
                    query=query, document=text[:2000])}],
                temperature=0.0,
            )
            score = float(resp.get("score", 0.0))
            return max(0.0, min(1.0, score)), str(resp.get("reason", ""))[:120]
        except Exception as e:
            logger.warning("grader_llm_failed", error=str(e))
            return self._heuristic_grade(query, text)

    def grade(self, query: str, doc: dict) -> GradeResult:
        text = doc.get("text", "")
        doc_id = doc.get("doc_id", "")
        score, reason = (self._llm_grade(query, text) if self.use_llm
                         else self._heuristic_grade(query, text))
        return GradeResult(doc_id=doc_id, is_relevant=score >= self.threshold,
                           score=score, reason=reason)

    def grade_batch(self, query: str, documents: list[dict]) -> list[GradeResult]:
        return [self.grade(query, d) for d in documents]

    def should_re_retrieve(self, grades: list[GradeResult], min_relevant: int = 2) -> bool:
        return sum(1 for g in grades if g.is_relevant) < min_relevant
