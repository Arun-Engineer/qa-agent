"""observability/feedback.py — User Feedback Collector"""
from __future__ import annotations
import time, structlog
from collections import deque
from dataclasses import dataclass, field
from typing import Any
logger = structlog.get_logger()

@dataclass
class FeedbackEntry:
    run_id: str; workflow: str; tenant_id: str = ""; rating: int = 0
    category: str = ""; comment: str = ""; timestamp: float = 0.0

class FeedbackCollector:
    def __init__(self, max_entries: int = 2000):
        self._entries: deque[FeedbackEntry] = deque(maxlen=max_entries)
        self._by_wf: dict[str, list[FeedbackEntry]] = {}

    def record(self, entry: FeedbackEntry):
        if not entry.timestamp: entry.timestamp = time.time()
        self._entries.append(entry); self._by_wf.setdefault(entry.workflow, []).append(entry)

    def get_stats(self, workflow: str|None=None) -> dict[str, Any]:
        entries = self._by_wf.get(workflow, []) if workflow else list(self._entries)
        if not entries: return {"count":0,"avg_rating":0}
        ratings = [e.rating for e in entries if e.rating > 0]
        cats = {}
        for e in entries:
            if e.category: cats[e.category] = cats.get(e.category, 0) + 1
        return {"count":len(entries),"avg_rating":round(sum(ratings)/max(len(ratings),1),2),
                "rating_distribution":{str(i):sum(1 for r in ratings if r==i) for i in range(1,6)},
                "top_categories":dict(sorted(cats.items(),key=lambda x:x[1],reverse=True)[:5])}

    def get_low_rated_runs(self, threshold: int = 2, limit: int = 20) -> list[FeedbackEntry]:
        return [e for e in self._entries if e.rating <= threshold][-limit:]
