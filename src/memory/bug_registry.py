"""
src/memory/bug_registry.py — Bug indexing, dedup, and matching.

Combines SQL store + vector store for intelligent bug management:
  - Register new bugs from test failures
  - Deduplicate using error signatures + vector similarity
  - Match new failures against known bugs
"""
from __future__ import annotations

import hashlib, structlog
from typing import Optional

logger = structlog.get_logger()


class BugRegistry:
    """Bug registration and matching using SQL + vector stores."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._sql = None
        self._vectors = None

    @property
    def sql(self):
        if self._sql is None:
            from src.memory.sql_store import SQLStore
            self._sql = SQLStore()
        return self._sql

    @property
    def vectors(self):
        if self._vectors is None:
            from src.memory.vector_store import VectorStore
            self._vectors = VectorStore(collection=f"bugs_{self.tenant_id}")
        return self._vectors

    @staticmethod
    def _error_signature(error_message: str, test_name: str = "") -> str:
        """Create a stable signature for error dedup."""
        # Normalize: strip line numbers, memory addresses, timestamps
        import re
        normalized = re.sub(r"line \d+", "line N", error_message)
        normalized = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", normalized)
        normalized = re.sub(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}", "TIMESTAMP", normalized)
        key = f"{test_name}:{normalized[:500]}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def register_failure(
        self,
        test_name: str,
        error_message: str,
        severity: str = "medium",
        module: str = "",
        traceback: str = "",
        triage_category: str = "BUG",
    ) -> dict:
        """
        Register a test failure as a bug.
        Returns match info if it's a known bug.
        """
        signature = self._error_signature(error_message, test_name)

        # 1. Check SQL for exact signature match
        existing = self.sql.search_bugs(self.tenant_id, status="open")
        for bug in existing:
            if bug.signature == signature:
                # Known bug — increment count
                self.sql.upsert_bug(
                    self.tenant_id, bug.title, signature,
                    occurrence_count=bug.occurrence_count + 1,
                )
                return {
                    "match": "exact",
                    "bug_id": bug.id,
                    "title": bug.title,
                    "occurrences": bug.occurrence_count + 1,
                    "is_new": False,
                }

        # 2. Check vector similarity for near-matches
        similar = self.vectors.search(f"{test_name} {error_message}", top_k=3)
        for doc in similar:
            if doc.score > 0.85:
                return {
                    "match": "similar",
                    "bug_id": doc.metadata.get("bug_id", doc.id),
                    "title": doc.text[:100],
                    "similarity": round(doc.score, 3),
                    "is_new": False,
                }

        # 3. New bug — register it
        title = f"[{triage_category}] {test_name}: {error_message[:100]}"
        bug = self.sql.upsert_bug(
            self.tenant_id, title, signature,
            severity=severity, module=module,
            description=f"Test: {test_name}\nError: {error_message}\n\n{traceback[:1000]}",
        )

        # Index in vector store
        self.vectors.upsert(
            doc_id=f"bug:{bug.id}",
            text=f"{test_name} {error_message} {module}",
            metadata={"bug_id": bug.id, "severity": severity, "module": module},
        )

        logger.info("new_bug_registered", bug_id=bug.id, title=title[:80])
        return {
            "match": "none",
            "bug_id": bug.id,
            "title": title,
            "is_new": True,
        }

    def find_similar_bugs(self, query: str, top_k: int = 5) -> list[dict]:
        """Search for bugs similar to a query."""
        results = self.vectors.search(query, top_k=top_k)
        return [
            {
                "bug_id": r.metadata.get("bug_id", r.id),
                "text": r.text[:200],
                "score": round(r.score, 3),
                "severity": r.metadata.get("severity", "unknown"),
                "module": r.metadata.get("module", ""),
            }
            for r in results
        ]

    def close(self):
        if self._sql:
            self._sql.close()
