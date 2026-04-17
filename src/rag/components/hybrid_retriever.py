"""components/hybrid_retriever.py — Hybrid Search (Vector + BM25 Keyword)"""
from __future__ import annotations
import math, re, structlog
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from abc import ABC, abstractmethod

logger = structlog.get_logger()

@dataclass
class RetrievedDoc:
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    source: str = ""

class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._docs: dict[str, str] = {}
        self._doc_lengths: dict[str, int] = {}
        self._avg_dl: float = 0.0
        self._term_freq: dict[str, dict[str, int]] = defaultdict(dict)
        self._doc_freq: dict[str, int] = defaultdict(int)
        self._N: int = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def add_document(self, doc_id: str, text: str) -> None:
        tokens = self._tokenize(text)
        self._docs[doc_id] = text
        self._doc_lengths[doc_id] = len(tokens)
        seen: set[str] = set()
        for t in tokens:
            self._term_freq[t][doc_id] = self._term_freq[t].get(doc_id, 0) + 1
            if t not in seen:
                self._doc_freq[t] += 1
                seen.add(t)
        self._N = len(self._docs)
        self._avg_dl = sum(self._doc_lengths.values()) / max(self._N, 1)

    def remove_document(self, doc_id: str) -> None:
        if doc_id not in self._docs:
            return
        tokens = self._tokenize(self._docs[doc_id])
        seen: set[str] = set()
        for t in tokens:
            if doc_id in self._term_freq.get(t, {}):
                del self._term_freq[t][doc_id]
                if t not in seen:
                    self._doc_freq[t] = max(self._doc_freq[t] - 1, 0)
                    seen.add(t)
        del self._docs[doc_id]
        del self._doc_lengths[doc_id]
        self._N = len(self._docs)
        self._avg_dl = sum(self._doc_lengths.values()) / max(self._N, 1)

    def search(self, query: str, top_k: int = 10) -> list[RetrievedDoc]:
        tokens = self._tokenize(query)
        scores: dict[str, float] = defaultdict(float)
        for term in tokens:
            if term not in self._term_freq: continue
            df = self._doc_freq.get(term, 0)
            idf = math.log((self._N - df + 0.5) / (df + 0.5) + 1.0)
            for doc_id, tf in self._term_freq[term].items():
                dl = self._doc_lengths[doc_id]
                num = tf * (self.k1 + 1)
                den = tf + self.k1 * (1 - self.b + self.b * dl / max(self._avg_dl, 1))
                scores[doc_id] += idf * (num / den)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [RetrievedDoc(doc_id=d, text=self._docs.get(d, ""), score=s, source="bm25") for d, s in ranked]

    @property
    def doc_count(self) -> int:
        return self._N

class VectorRetrieverAdapter(ABC):
    @abstractmethod
    def search(self, query: str, top_k: int = 10) -> list[RetrievedDoc]: ...
    @abstractmethod
    def add_document(self, doc_id: str, text: str, metadata: dict | None = None) -> None: ...

class ChromaVectorRetriever(VectorRetrieverAdapter):
    def __init__(self, collection_name: str = "qa_docs", persist_dir: str = "data/chroma"):
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=persist_dir)
            self._collection = self._client.get_or_create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
        except ImportError:
            self._client = self._collection = None

    def search(self, query: str, top_k: int = 10) -> list[RetrievedDoc]:
        if not self._collection: return []
        try:
            r = self._collection.query(query_texts=[query], n_results=top_k)
            ids = (r.get("ids") or [[]])[0]
            docs = (r.get("documents") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            dists = (r.get("distances") or [[]])[0]
            return [RetrievedDoc(
                doc_id=doc_id,
                text=docs[i] if i < len(docs) else "",
                metadata=metas[i] if i < len(metas) else {},
                score=1.0 - (dists[i] if i < len(dists) else 0.0),
                source="vector",
            ) for i, doc_id in enumerate(ids)]
        except Exception: return []

    def add_document(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        if self._collection:
            self._collection.upsert(ids=[doc_id], documents=[text], metadatas=[metadata or {}])

class HybridRetriever:
    def __init__(self, vector_retriever: VectorRetrieverAdapter, bm25_index: BM25Index,
                 vector_weight: float = 0.6, bm25_weight: float = 0.4, rrf_k: int = 60):
        self.vector, self.bm25 = vector_retriever, bm25_index
        self.vector_weight, self.bm25_weight, self.rrf_k = vector_weight, bm25_weight, rrf_k

    def add_document(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        self.vector.add_document(doc_id, text, metadata)
        self.bm25.add_document(doc_id, text)

    def search(self, query: str, top_k: int = 10, vector_top_k: int = 20, bm25_top_k: int = 20) -> list[RetrievedDoc]:
        vr = self.vector.search(query, top_k=vector_top_k)
        br = self.bm25.search(query, top_k=bm25_top_k)
        rrf: dict[str, float] = defaultdict(float)
        doc_map: dict[str, RetrievedDoc] = {}
        for rank, d in enumerate(vr, 1):
            rrf[d.doc_id] += self.vector_weight / (self.rrf_k + rank); doc_map[d.doc_id] = d
        for rank, d in enumerate(br, 1):
            rrf[d.doc_id] += self.bm25_weight / (self.rrf_k + rank)
            if d.doc_id not in doc_map: doc_map[d.doc_id] = d
        ranked = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for did, score in ranked:
            doc = doc_map[did]; doc.score = score; doc.source = "hybrid"; results.append(doc)
        return results
