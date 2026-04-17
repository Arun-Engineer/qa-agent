"""components/reranker.py — Document Reranker (LLM + Cross-Encoder)"""
from __future__ import annotations
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
logger = structlog.get_logger()

@dataclass
class RankedDoc:
    doc_id: str; text: str; metadata: dict[str, Any]; original_score: float; rerank_score: float; source: str

class BaseReranker(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: list[dict], top_k: int = 5) -> list[RankedDoc]: ...

class LLMReranker(BaseReranker):
    RERANK_PROMPT = "You are a relevance judge. Rate how relevant this document is to the query.\nQuery: {query}\nDocument:\n{document}\nRate 1-10. Respond ONLY with JSON: {{\"score\": <number>, \"reason\": \"<brief>\"}}"
    def __init__(self, llm_provider=None): self._llm = llm_provider
    @property
    def llm(self):
        if self._llm is None:
            from src.llm.provider import get_llm; self._llm = get_llm()
        return self._llm
    def rerank(self, query: str, documents: list[dict], top_k: int = 5) -> list[RankedDoc]:
        scored = []
        for doc in documents:
            try:
                resp = self.llm.chat_json([{"role":"user","content":self.RERANK_PROMPT.format(query=query,document=doc.get("text","")[:2000])}], temperature=0.0)
                score = float(resp.get("score", 0)) / 10.0
            except Exception:
                score = doc.get("score", 0.0)
            scored.append(RankedDoc(doc_id=doc.get("doc_id",""), text=doc.get("text",""), metadata=doc.get("metadata",{}), original_score=doc.get("score",0.0), rerank_score=score, source=doc.get("source","unknown")))
        scored.sort(key=lambda d: d.rerank_score, reverse=True)
        return scored[:top_k]

class CrossEncoderReranker(BaseReranker):
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self._model_name = model_name; self._model = None
    def _load_model(self):
        if self._model is not None: return
        try:
            from sentence_transformers import CrossEncoder; self._model = CrossEncoder(self._model_name)
        except ImportError: pass
    def rerank(self, query: str, documents: list[dict], top_k: int = 5) -> list[RankedDoc]:
        self._load_model()
        if self._model is None:
            return [RankedDoc(doc_id=d.get("doc_id",""), text=d.get("text",""), metadata=d.get("metadata",{}), original_score=d.get("score",0.0), rerank_score=d.get("score",0.0), source=d.get("source","unknown")) for d in sorted(documents, key=lambda x: x.get("score",0), reverse=True)[:top_k]]
        pairs = [(query, d.get("text","")[:1000]) for d in documents]
        scores = self._model.predict(pairs)
        scored = [RankedDoc(doc_id=d.get("doc_id",""), text=d.get("text",""), metadata=d.get("metadata",{}), original_score=d.get("score",0.0), rerank_score=float(s), source=d.get("source","unknown")) for d, s in zip(documents, scores)]
        scored.sort(key=lambda d: d.rerank_score, reverse=True)
        return scored[:top_k]

class RerankerFactory:
    @staticmethod
    def create(strategy: str = "llm", **kwargs) -> BaseReranker:
        if strategy == "cross_encoder": return CrossEncoderReranker(**kwargs)
        return LLMReranker(**kwargs)
