"""
src/memory/vector_store.py — Vector similarity search for test artifacts.

Supports:
  - Qdrant (if QDRANT_URL configured)
  - In-memory fallback using numpy cosine similarity

Used by: failure_triage, bug_matcher, knowledge retrieval
"""
from __future__ import annotations

import os, json, hashlib, structlog
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

logger = structlog.get_logger()


@dataclass
class VectorDoc:
    """A document with embedding vector."""
    id: str
    text: str
    metadata: dict = field(default_factory=dict)
    embedding: list[float] = field(default_factory=list)
    score: float = 0.0


class VectorStore:
    """Unified vector store — Qdrant or in-memory."""

    def __init__(self, collection: str = "qa_knowledge"):
        self.collection = collection
        self._qdrant = None
        self._memory: dict[str, VectorDoc] = {}
        self._init_backend()

    def _init_backend(self):
        qdrant_url = (os.getenv("QDRANT_URL") or "").strip()
        if qdrant_url:
            try:
                from qdrant_client import QdrantClient
                self._qdrant = QdrantClient(url=qdrant_url, timeout=10)
                # Ensure collection exists
                try:
                    self._qdrant.get_collection(self.collection)
                except Exception:
                    from qdrant_client.models import Distance, VectorParams
                    self._qdrant.create_collection(
                        collection_name=self.collection,
                        vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
                    )
                logger.info("vector_store_init", backend="qdrant", url=qdrant_url)
                return
            except ImportError:
                logger.warning("qdrant_client not installed, using in-memory fallback")
            except Exception as e:
                logger.warning("qdrant_connect_failed", error=str(e))

        logger.info("vector_store_init", backend="in_memory")

    def get_embedding(self, text: str) -> list[float]:
        """Get embedding via LLM provider."""
        try:
            from src.llm.provider import get_llm, get_default_provider
            provider = get_default_provider()

            if provider == "openai":
                import openai
                client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
                resp = client.embeddings.create(
                    model="text-embedding-3-small", input=text
                )
                return resp.data[0].embedding
            else:
                # Anthropic doesn't have embeddings API — use a hash-based fallback
                return self._hash_embedding(text)
        except Exception as e:
            logger.warning("embedding_failed", error=str(e))
            return self._hash_embedding(text)

    @staticmethod
    def _hash_embedding(text: str, dim: int = 1536) -> list[float]:
        """Deterministic pseudo-embedding from text hash. Good for dedup, not for semantics."""
        import hashlib
        h = hashlib.sha512(text.encode()).digest()
        # Expand hash to fill dim
        while len(h) < dim * 4:
            h += hashlib.sha512(h).digest()
        import struct
        floats = struct.unpack(f"{dim}f", h[:dim * 4])
        # Normalize
        norm = max(sum(x * x for x in floats) ** 0.5, 1e-10)
        return [x / norm for x in floats]

    def upsert(self, doc_id: str, text: str, metadata: dict | None = None,
               embedding: list[float] | None = None) -> bool:
        """Insert or update a document."""
        emb = embedding or self.get_embedding(text)
        meta = metadata or {}

        if self._qdrant:
            try:
                from qdrant_client.models import PointStruct
                self._qdrant.upsert(
                    collection_name=self.collection,
                    points=[PointStruct(
                        id=hashlib.md5(doc_id.encode()).hexdigest()[:16],
                        vector=emb,
                        payload={"doc_id": doc_id, "text": text[:2000], **meta},
                    )]
                )
                return True
            except Exception as e:
                logger.error("qdrant_upsert_failed", error=str(e))
                return False
        else:
            self._memory[doc_id] = VectorDoc(id=doc_id, text=text, metadata=meta, embedding=emb)
            return True

    def search(self, query: str, top_k: int = 5, embedding: list[float] | None = None) -> list[VectorDoc]:
        """Search for similar documents."""
        query_emb = embedding or self.get_embedding(query)

        if self._qdrant:
            try:
                results = self._qdrant.search(
                    collection_name=self.collection,
                    query_vector=query_emb,
                    limit=top_k,
                )
                return [
                    VectorDoc(
                        id=r.payload.get("doc_id", str(r.id)),
                        text=r.payload.get("text", ""),
                        metadata={k: v for k, v in r.payload.items() if k not in ("doc_id", "text")},
                        score=r.score,
                    )
                    for r in results
                ]
            except Exception as e:
                logger.error("qdrant_search_failed", error=str(e))
                return []
        else:
            return self._memory_search(query_emb, top_k)

    def _memory_search(self, query_emb: list[float], top_k: int) -> list[VectorDoc]:
        """Cosine similarity search in memory."""
        if not self._memory:
            return []

        results = []
        for doc in self._memory.values():
            score = self._cosine_sim(query_emb, doc.embedding)
            results.append(VectorDoc(
                id=doc.id, text=doc.text, metadata=doc.metadata,
                embedding=[], score=score,
            ))

        results.sort(key=lambda d: d.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / max(na * nb, 1e-10)

    def delete(self, doc_id: str) -> bool:
        if self._qdrant:
            try:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                self._qdrant.delete(
                    collection_name=self.collection,
                    points_selector=Filter(must=[
                        FieldCondition(key="doc_id", match=MatchValue(value=doc_id))
                    ]),
                )
                return True
            except Exception:
                return False
        else:
            return self._memory.pop(doc_id, None) is not None

    def count(self) -> int:
        if self._qdrant:
            try:
                info = self._qdrant.get_collection(self.collection)
                return info.points_count
            except Exception:
                return 0
        return len(self._memory)
