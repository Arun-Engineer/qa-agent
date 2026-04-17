"""services/semantic_cache.py — Semantic Query Cache"""
from __future__ import annotations
import hashlib, time, structlog
from dataclasses import dataclass, field
from typing import Any, Optional
logger = structlog.get_logger()

@dataclass
class CacheEntry:
    query: str; query_hash: str; embedding: list[float]; response: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0; ttl_seconds: int = 3600; hit_count: int = 0
    @property
    def is_expired(self) -> bool: return (time.time() - self.created_at) > self.ttl_seconds

class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.92, max_entries: int = 1000, default_ttl: int = 3600):
        self.threshold = similarity_threshold; self.max_entries = max_entries; self.default_ttl = default_ttl
        self._cache: dict[str, CacheEntry] = {}; self._stats = {"hits": 0, "misses": 0, "evictions": 0}

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b): return 0.0
        dot = sum(x*y for x,y in zip(a,b))
        na = sum(x*x for x in a)**0.5; nb = sum(x*x for x in b)**0.5
        return dot / max(na*nb, 1e-10)

    @staticmethod
    def _hash_query(query: str) -> str: return hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]

    def lookup(self, query: str, embedding: list[float]) -> Optional[CacheEntry]:
        self._evict_expired()
        best, best_score = None, 0.0
        for entry in self._cache.values():
            sim = self._cosine_sim(embedding, entry.embedding)
            if sim > best_score and sim >= self.threshold: best_score = sim; best = entry
        if best: best.hit_count += 1; self._stats["hits"] += 1; return best
        self._stats["misses"] += 1; return None

    def store(self, query: str, embedding: list[float], response: str, metadata: dict|None=None, ttl: int|None=None) -> CacheEntry:
        if len(self._cache) >= self.max_entries: self._evict_lru()
        h = self._hash_query(query)
        entry = CacheEntry(query=query, query_hash=h, embedding=embedding, response=response, metadata=metadata or {}, created_at=time.time(), ttl_seconds=ttl or self.default_ttl)
        self._cache[h] = entry; return entry

    def invalidate(self, query: str) -> bool:
        h = self._hash_query(query)
        if h in self._cache: del self._cache[h]; return True
        return False

    def clear(self) -> int:
        c = len(self._cache); self._cache.clear(); return c

    def _evict_expired(self):
        for k in [k for k,v in self._cache.items() if v.is_expired]: del self._cache[k]; self._stats["evictions"] += 1

    def _evict_lru(self):
        if not self._cache: return
        k = min(self._cache, key=lambda k: (self._cache[k].hit_count, self._cache[k].created_at))
        del self._cache[k]; self._stats["evictions"] += 1

    @property
    def stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        return {**self._stats, "size": len(self._cache), "hit_rate": round(self._stats["hits"]/max(total,1)*100, 1)}
