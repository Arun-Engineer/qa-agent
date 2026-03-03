"""
Phase 6 · Feedback Handler
Human-in-the-loop reinforcement for test results.
Captures approvals, rejections, annotations, and feeds them
back into the agent learning loop.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    APPROVE = "approve"       # Human confirms result is correct
    REJECT = "reject"         # Human says result is wrong
    ANNOTATE = "annotate"     # Human adds context / notes
    RECLASSIFY = "reclassify" # Human changes severity/category
    SUPPRESS = "suppress"     # Human marks as known issue / false positive
    ESCALATE = "escalate"     # Human escalates for deeper investigation


class FeedbackSource(Enum):
    UI = "ui"
    SLACK = "slack"
    API = "api"
    EMAIL = "email"


@dataclass
class FeedbackEntry:
    """Single feedback item from a human reviewer."""
    feedback_id: str
    feedback_type: FeedbackType
    source: FeedbackSource
    target_type: str  # "test_result", "bug", "gate_decision", "test_case"
    target_id: str
    reviewer: str
    comment: str = ""
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    confidence_delta: float = 0.0  # how much to adjust confidence
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "type": self.feedback_type.value,
            "source": self.source.value,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reviewer": self.reviewer,
            "comment": self.comment,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "confidence_delta": self.confidence_delta,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class FeedbackSummary:
    """Aggregated feedback for a run or entity."""
    total_entries: int = 0
    approvals: int = 0
    rejections: int = 0
    annotations: int = 0
    suppressions: int = 0
    escalations: int = 0
    net_confidence_adjustment: float = 0.0
    reviewers: List[str] = field(default_factory=list)
    pending_count: int = 0


class FeedbackHandler:
    """
    Manages human feedback collection, storage, and integration
    with the agent learning loop.
    """

    def __init__(self, storage_backend: Optional[Any] = None):
        self._entries: List[FeedbackEntry] = []
        self._storage = storage_backend
        self._listeners: Dict[FeedbackType, List[Callable]] = {ft: [] for ft in FeedbackType}
        self._counter = 0

    # ── feedback submission ────────────────────────────────────

    async def submit(
        self,
        feedback_type: FeedbackType,
        target_type: str,
        target_id: str,
        reviewer: str,
        comment: str = "",
        source: FeedbackSource = FeedbackSource.UI,
        old_value: Optional[Any] = None,
        new_value: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackEntry:
        """Submit a new feedback entry."""
        self._counter += 1
        entry = FeedbackEntry(
            feedback_id=f"FB-{self._counter:06d}",
            feedback_type=feedback_type,
            source=source,
            target_type=target_type,
            target_id=target_id,
            reviewer=reviewer,
            comment=comment,
            old_value=old_value,
            new_value=new_value,
            confidence_delta=self._compute_confidence_delta(feedback_type),
            metadata=metadata or {},
        )
        self._entries.append(entry)

        # Persist
        if self._storage:
            await self._persist(entry)

        # Notify listeners
        await self._notify_listeners(entry)

        logger.info("Feedback %s: %s on %s/%s by %s",
                     entry.feedback_id, feedback_type.value, target_type, target_id, reviewer)
        return entry

    async def approve(self, target_type: str, target_id: str, reviewer: str, comment: str = "") -> FeedbackEntry:
        return await self.submit(FeedbackType.APPROVE, target_type, target_id, reviewer, comment)

    async def reject(self, target_type: str, target_id: str, reviewer: str, comment: str = "") -> FeedbackEntry:
        return await self.submit(FeedbackType.REJECT, target_type, target_id, reviewer, comment)

    async def annotate(self, target_type: str, target_id: str, reviewer: str, comment: str) -> FeedbackEntry:
        return await self.submit(FeedbackType.ANNOTATE, target_type, target_id, reviewer, comment)

    async def suppress(self, target_type: str, target_id: str, reviewer: str, reason: str = "") -> FeedbackEntry:
        return await self.submit(
            FeedbackType.SUPPRESS, target_type, target_id, reviewer, reason,
            metadata={"suppressed": True},
        )

    async def escalate(self, target_type: str, target_id: str, reviewer: str, reason: str = "") -> FeedbackEntry:
        return await self.submit(FeedbackType.ESCALATE, target_type, target_id, reviewer, reason)

    async def reclassify(
        self, target_type: str, target_id: str, reviewer: str,
        old_value: Any, new_value: Any, comment: str = "",
    ) -> FeedbackEntry:
        return await self.submit(
            FeedbackType.RECLASSIFY, target_type, target_id, reviewer, comment,
            old_value=old_value, new_value=new_value,
        )

    # ── querying ───────────────────────────────────────────────

    def get_feedback_for(self, target_type: str, target_id: str) -> List[FeedbackEntry]:
        return [
            e for e in self._entries
            if e.target_type == target_type and e.target_id == target_id
        ]

    def get_summary(self, target_type: Optional[str] = None) -> FeedbackSummary:
        entries = self._entries
        if target_type:
            entries = [e for e in entries if e.target_type == target_type]

        summary = FeedbackSummary(
            total_entries=len(entries),
            approvals=sum(1 for e in entries if e.feedback_type == FeedbackType.APPROVE),
            rejections=sum(1 for e in entries if e.feedback_type == FeedbackType.REJECT),
            annotations=sum(1 for e in entries if e.feedback_type == FeedbackType.ANNOTATE),
            suppressions=sum(1 for e in entries if e.feedback_type == FeedbackType.SUPPRESS),
            escalations=sum(1 for e in entries if e.feedback_type == FeedbackType.ESCALATE),
            net_confidence_adjustment=sum(e.confidence_delta for e in entries),
            reviewers=list(set(e.reviewer for e in entries)),
        )
        return summary

    def get_suppressed_ids(self, target_type: str) -> List[str]:
        """Return IDs of suppressed items (false positives / known issues)."""
        return list(set(
            e.target_id for e in self._entries
            if e.target_type == target_type and e.feedback_type == FeedbackType.SUPPRESS
        ))

    def get_confidence_adjustment(self, target_type: str, target_id: str) -> float:
        """Net confidence adjustment from all feedback on a target."""
        entries = self.get_feedback_for(target_type, target_id)
        return sum(e.confidence_delta for e in entries)

    # ── learning integration ───────────────────────────────────

    def generate_training_signal(self) -> List[Dict[str, Any]]:
        """
        Convert feedback entries into training signals for agent improvement.
        Returns a list of (input, expected_output, weight) tuples.
        """
        signals = []
        for entry in self._entries:
            if entry.feedback_type == FeedbackType.APPROVE:
                signals.append({
                    "type": "positive_reinforcement",
                    "target": f"{entry.target_type}/{entry.target_id}",
                    "weight": 1.0,
                    "context": entry.comment,
                })
            elif entry.feedback_type == FeedbackType.REJECT:
                signals.append({
                    "type": "negative_reinforcement",
                    "target": f"{entry.target_type}/{entry.target_id}",
                    "weight": -1.0,
                    "context": entry.comment,
                })
            elif entry.feedback_type == FeedbackType.RECLASSIFY:
                signals.append({
                    "type": "correction",
                    "target": f"{entry.target_type}/{entry.target_id}",
                    "old": entry.old_value,
                    "new": entry.new_value,
                    "weight": 1.5,
                    "context": entry.comment,
                })
            elif entry.feedback_type == FeedbackType.SUPPRESS:
                signals.append({
                    "type": "false_positive",
                    "target": f"{entry.target_type}/{entry.target_id}",
                    "weight": -0.5,
                    "context": entry.comment,
                })
        return signals

    # ── event listeners ────────────────────────────────────────

    def on_feedback(self, feedback_type: FeedbackType, callback: Callable):
        self._listeners[feedback_type].append(callback)

    async def _notify_listeners(self, entry: FeedbackEntry):
        for cb in self._listeners.get(entry.feedback_type, []):
            try:
                result = cb(entry)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error("Feedback listener error: %s", e)

    # ── internals ──────────────────────────────────────────────

    def _compute_confidence_delta(self, ft: FeedbackType) -> float:
        return {
            FeedbackType.APPROVE: 0.05,
            FeedbackType.REJECT: -0.15,
            FeedbackType.ANNOTATE: 0.0,
            FeedbackType.RECLASSIFY: -0.05,
            FeedbackType.SUPPRESS: -0.10,
            FeedbackType.ESCALATE: 0.0,
        }.get(ft, 0.0)

    async def _persist(self, entry: FeedbackEntry):
        try:
            if hasattr(self._storage, "save_feedback"):
                await self._storage.save_feedback(entry.to_dict())
        except Exception as e:
            logger.error("Failed to persist feedback: %s", e)