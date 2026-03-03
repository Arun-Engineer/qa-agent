"""
Phase 5 · Bug Tracker Interface
Abstract interface for all bug-tracking integrations (ADO, Jira, GitHub Issues).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime


class BugSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TRIVIAL = "trivial"


class BugStatus(Enum):
    NEW = "new"
    ACTIVE = "active"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REACTIVATED = "reactivated"


@dataclass
class BugField:
    """Schema descriptor for a tracker-specific field."""
    name: str
    field_type: str  # "string", "integer", "html", "treepath", "identity"
    required: bool = False
    allowed_values: Optional[List[str]] = None
    default: Optional[Any] = None


@dataclass
class Evidence:
    """Binary or text evidence to attach to a bug."""
    filename: str
    content: bytes
    mime_type: str = "image/png"
    description: str = ""


@dataclass
class BugRecord:
    """Normalised bug representation across all trackers."""
    tracker_id: str  # e.g. "ADO-12345", "JIRA-TC-99"
    title: str
    description: str
    severity: BugSeverity = BugSeverity.MEDIUM
    status: BugStatus = BugStatus.NEW
    assigned_to: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    attachments: List[str] = field(default_factory=list)
    url: Optional[str] = None
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class IBugTracker(ABC):
    """
    Unified interface every bug-tracker adapter must implement.
    Methods raise TrackerError on failure.
    """

    @abstractmethod
    async def create_bug(
        self,
        title: str,
        description: str,
        severity: BugSeverity = BugSeverity.MEDIUM,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> BugRecord:
        """Create a new bug/work-item and return the normalised record."""
        ...

    @abstractmethod
    async def update_bug(
        self,
        tracker_id: str,
        updates: Dict[str, Any],
    ) -> BugRecord:
        """Patch fields on an existing bug."""
        ...

    @abstractmethod
    async def search_bugs(
        self,
        query: str,
        max_results: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[BugRecord]:
        """Full-text / structured search returning matching bugs."""
        ...

    @abstractmethod
    async def get_fields(self, work_item_type: str = "Bug") -> List[BugField]:
        """Return available fields + allowed values for the given type."""
        ...

    @abstractmethod
    async def attach_evidence(
        self,
        tracker_id: str,
        evidence: Evidence,
    ) -> str:
        """Upload binary evidence; return the attachment URL."""
        ...

    # ── convenience helpers (concrete) ──────────────────────────

    async def get_bug(self, tracker_id: str) -> Optional[BugRecord]:
        """Retrieve a single bug by ID (default: search with exact ID)."""
        results = await self.search_bugs(
            query=tracker_id, max_results=1
        )
        return results[0] if results else None

    async def add_comment(self, tracker_id: str, comment_html: str) -> BugRecord:
        """Append a discussion comment (maps to update on most trackers)."""
        return await self.update_bug(
            tracker_id, {"comment": comment_html}
        )

    async def link_bugs(
        self, source_id: str, target_id: str, link_type: str = "Related"
    ) -> BugRecord:
        """Create a relation/link between two bugs."""
        return await self.update_bug(
            source_id, {"add_link": {"target": target_id, "type": link_type}}
        )


class TrackerError(Exception):
    """Raised when a tracker operation fails."""
    def __init__(self, message: str, status_code: Optional[int] = None, raw: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw