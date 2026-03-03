"""
Phase 5 · ADO Discussion Perception
Fetches Azure DevOps work-item comments/discussions, classifies
scope changes, stakeholder sentiment, and decision signals.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)


class DiscussionSignal(Enum):
    SCOPE_CHANGE = "SCOPE_CHANGE"
    REQUIREMENT_UPDATE = "REQUIREMENT_UPDATE"
    APPROVAL = "APPROVAL"
    REJECTION = "REJECTION"
    QUESTION = "QUESTION"
    BLOCKER = "BLOCKER"
    DECISION = "DECISION"
    INFORMATIONAL = "INFORMATIONAL"


@dataclass
class CommentRecord:
    """Single discussion comment with classification."""
    comment_id: int
    work_item_id: int
    author: str
    text: str
    created: datetime
    signals: List[DiscussionSignal] = field(default_factory=list)
    scope_delta: Optional[str] = None  # if SCOPE_CHANGE, what changed
    confidence: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscussionSummary:
    """Aggregated discussion analysis for a work item."""
    work_item_id: int
    total_comments: int
    scope_changes: List[CommentRecord]
    decisions: List[CommentRecord]
    blockers: List[CommentRecord]
    open_questions: List[CommentRecord]
    latest_activity: Optional[datetime] = None
    sentiment_score: float = 0.0  # -1.0 to 1.0


# ── keyword classifiers ────────────────────────────────────────

_SIGNAL_PATTERNS: Dict[DiscussionSignal, List[re.Pattern]] = {
    DiscussionSignal.SCOPE_CHANGE: [
        re.compile(r"\b(scope\s+change|out\s+of\s+scope|in\s+scope|descope|re-?scope)\b", re.I),
        re.compile(r"\b(added\s+requirement|removed\s+requirement|new\s+requirement)\b", re.I),
        re.compile(r"\b(acceptance\s+criteria\s+(changed|updated|added|removed))\b", re.I),
        re.compile(r"\b(feature\s+(added|removed|changed))\b", re.I),
    ],
    DiscussionSignal.REQUIREMENT_UPDATE: [
        re.compile(r"\b(requirement|spec|specification)\s+(update|change|modif)", re.I),
        re.compile(r"\b(updated?\s+the\s+(AC|acceptance\s+criteria))\b", re.I),
    ],
    DiscussionSignal.APPROVAL: [
        re.compile(r"\b(approved?|lgtm|looks?\s+good|sign\s*-?off|green\s*light)\b", re.I),
        re.compile(r"\b(\+1|thumbs?\s*up|go\s+ahead)\b", re.I),
    ],
    DiscussionSignal.REJECTION: [
        re.compile(r"\b(reject(ed)?|nack|not\s+approved|push\s*back)\b", re.I),
        re.compile(r"\b(-1|thumbs?\s*down|do\s+not\s+proceed)\b", re.I),
    ],
    DiscussionSignal.QUESTION: [
        re.compile(r"\?\s*$", re.M),
        re.compile(r"\b(can\s+(you|we)|could\s+(you|we)|what\s+(is|are|about)|how\s+(do|should))\b", re.I),
    ],
    DiscussionSignal.BLOCKER: [
        re.compile(r"\b(block(ed|er|ing)?|impediment|stuck|cannot\s+proceed)\b", re.I),
        re.compile(r"\b(depend(s|ency)\s+on|waiting\s+(for|on))\b", re.I),
    ],
    DiscussionSignal.DECISION: [
        re.compile(r"\b(decided?|decision|we\s+will|agreed?\s+(to|that)|conclusion)\b", re.I),
        re.compile(r"\b(going\s+(forward|with)|final\s+call)\b", re.I),
    ],
}

_NEGATIVE_WORDS = re.compile(
    r"\b(issue|problem|fail|error|broken|bug|wrong|bad|concern|risk|delay|miss)\b", re.I
)
_POSITIVE_WORDS = re.compile(
    r"\b(good|great|done|complete|fixed|resolved|pass|success|nice|excellent|progress)\b", re.I
)


class ADODiscussionAnalyser:
    """
    Fetches and classifies Azure DevOps work-item discussions.
    """

    def __init__(
        self,
        organisation: str,
        project: str,
        pat: str,
        base_url: str = "https://dev.azure.com",
    ):
        self.org = organisation
        self.project = project
        self._base = f"{base_url}/{organisation}/{project}/_apis"
        self._auth = aiohttp.BasicAuth("", pat)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(auth=self._auth)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── core API ───────────────────────────────────────────────

    async def fetch_comments(self, work_item_id: int) -> List[CommentRecord]:
        """Fetch all comments for a work item and classify each."""
        session = await self._get_session()
        url = f"{self._base}/wit/workitems/{work_item_id}/comments"
        params = {"api-version": "7.0-preview.3", "$top": 200}

        async with session.get(url, params=params) as resp:
            if resp.status >= 400:
                body = await resp.text()
                logger.error("ADO comments fetch %s → %s: %s", work_item_id, resp.status, body[:300])
                return []
            data = await resp.json()

        records = []
        for c in data.get("comments", []):
            text = self._strip_html(c.get("text", ""))
            signals = self._classify(text)
            scope_delta = self._extract_scope_delta(text) if DiscussionSignal.SCOPE_CHANGE in signals else None

            records.append(CommentRecord(
                comment_id=c.get("id", 0),
                work_item_id=work_item_id,
                author=c.get("createdBy", {}).get("displayName", "Unknown"),
                text=text,
                created=datetime.fromisoformat(
                    c.get("createdDate", "2000-01-01T00:00:00").rstrip("Z")
                ),
                signals=signals,
                scope_delta=scope_delta,
                confidence=self._signal_confidence(signals, text),
                raw=c,
            ))
        return records

    async def analyse_discussion(self, work_item_id: int) -> DiscussionSummary:
        """Full discussion analysis for a single work item."""
        comments = await self.fetch_comments(work_item_id)

        scope_changes = [c for c in comments if DiscussionSignal.SCOPE_CHANGE in c.signals]
        decisions = [c for c in comments if DiscussionSignal.DECISION in c.signals]
        blockers = [c for c in comments if DiscussionSignal.BLOCKER in c.signals]
        open_questions = [c for c in comments if DiscussionSignal.QUESTION in c.signals]
        latest = max((c.created for c in comments), default=None) if comments else None

        sentiment = self._aggregate_sentiment(comments)

        return DiscussionSummary(
            work_item_id=work_item_id,
            total_comments=len(comments),
            scope_changes=scope_changes,
            decisions=decisions,
            blockers=blockers,
            open_questions=open_questions,
            latest_activity=latest,
            sentiment_score=sentiment,
        )

    async def batch_analyse(self, work_item_ids: List[int]) -> Dict[int, DiscussionSummary]:
        """Analyse discussions across multiple work items."""
        import asyncio
        tasks = [self.analyse_discussion(wid) for wid in work_item_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output = {}
        for wid, result in zip(work_item_ids, results):
            if isinstance(result, Exception):
                logger.error("Discussion analysis failed for %s: %s", wid, result)
                continue
            output[wid] = result
        return output

    async def detect_scope_changes_since(
        self, work_item_id: int, since: datetime,
    ) -> List[CommentRecord]:
        """Return scope-change comments newer than a given timestamp."""
        comments = await self.fetch_comments(work_item_id)
        return [
            c for c in comments
            if DiscussionSignal.SCOPE_CHANGE in c.signals and c.created > since
        ]

    # ── classification helpers ─────────────────────────────────

    def _classify(self, text: str) -> List[DiscussionSignal]:
        signals = []
        for signal, patterns in _SIGNAL_PATTERNS.items():
            if any(p.search(text) for p in patterns):
                signals.append(signal)
        if not signals:
            signals.append(DiscussionSignal.INFORMATIONAL)
        return signals

    def _signal_confidence(self, signals: List[DiscussionSignal], text: str) -> float:
        if not signals or signals == [DiscussionSignal.INFORMATIONAL]:
            return 0.3
        # more pattern hits → higher confidence
        total_hits = 0
        for signal in signals:
            patterns = _SIGNAL_PATTERNS.get(signal, [])
            total_hits += sum(1 for p in patterns if p.search(text))
        return min(0.5 + total_hits * 0.15, 1.0)

    def _extract_scope_delta(self, text: str) -> Optional[str]:
        """Extract a brief summary of what scope changed."""
        lines = text.split("\n")
        for line in lines:
            for p in _SIGNAL_PATTERNS[DiscussionSignal.SCOPE_CHANGE]:
                if p.search(line):
                    return line.strip()[:200]
        return None

    def _aggregate_sentiment(self, comments: List[CommentRecord]) -> float:
        if not comments:
            return 0.0
        total = 0.0
        for c in comments:
            pos = len(_POSITIVE_WORDS.findall(c.text))
            neg = len(_NEGATIVE_WORDS.findall(c.text))
            denom = pos + neg
            if denom > 0:
                total += (pos - neg) / denom
        return round(total / len(comments), 3)

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags for plain-text classification."""
        clean = re.sub(r"<[^>]+>", " ", html)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean