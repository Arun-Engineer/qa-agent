"""src/rag/tools/vector_search.py — Pluggable search tools for agents."""
from __future__ import annotations
import re
import structlog
from pathlib import Path
from typing import Any

logger = structlog.get_logger()


class VectorSearchTool:
    name = "vector_search"

    def __init__(self, retriever=None):
        self._retriever = retriever

    def run(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not self._retriever:
            return []
        results = self._retriever.search(query, top_k=top_k)
        return [{"doc_id": r.doc_id, "text": r.text[:500], "score": r.score} for r in results]


class CodeSearchTool:
    """Filesystem grep-based code search. Ranks files by match count."""
    name = "code_search"

    def __init__(self, root: str = ".", max_file_kb: int = 512):
        self.root = Path(root)
        self.max_bytes = max_file_kb * 1024

    def run(self, query: str, file_pattern: str = "*.py", top_k: int = 5) -> list[dict]:
        if not query.strip():
            return []
        try:
            pat = re.compile(re.escape(query), re.IGNORECASE)
        except re.error:
            return []
        scored: list[tuple[int, Path, str]] = []
        for path in self.root.rglob(file_pattern):
            if not path.is_file() or path.stat().st_size > self.max_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits = len(pat.findall(text))
            if hits:
                snippet = ""
                m = pat.search(text)
                if m:
                    s = max(0, m.start() - 80)
                    snippet = text[s: m.end() + 80].replace("\n", " ")
                scored.append((hits, path, snippet))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"doc_id": str(p), "text": snip[:500], "score": float(h)}
            for h, p, snip in scored[:top_k]
        ]


class WebSearchTool:
    """Web search placeholder. Plug in an HTTP search adapter (Bing/Brave/etc.)."""
    name = "web_search"

    def __init__(self, adapter=None):
        self._adapter = adapter

    def run(self, query: str, top_k: int = 3) -> list[dict]:
        if self._adapter is None:
            logger.debug("web_search_no_adapter")
            return []
        try:
            return self._adapter.search(query, top_k=top_k)
        except Exception as e:
            logger.warning("web_search_failed", error=str(e))
            return []
