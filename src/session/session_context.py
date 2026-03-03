"""Session Context — Isolated execution unit per user + environment.

This is the core of multi-tenancy. Every test operation happens
within a SessionContext that enforces environment-specific rules.
"""
from dataclasses import dataclass, field
from typing import Optional, Literal
from datetime import datetime, timedelta
from enum import Enum
import uuid


class Environment(str, Enum):
    SIT = "sit"
    UAT = "uat"
    PROD = "prod"


class AccessMode(str, Enum):
    FULL = "full"              # SIT: anything goes
    CONTROLLED = "controlled"  # UAT: seeded data, limited writes
    READ_ONLY = "read_only"    # PROD: observe only, no mutations


class SessionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


# Environment-specific rules
ENV_RULES = {
    Environment.SIT: {
        "access_mode": AccessMode.FULL,
        "can_generate_data": True,
        "can_mutate_state": True,
        "can_run_destructive": True,
        "approval_required": False,
        "session_timeout_minutes": 480,  # 8 hours
    },
    Environment.UAT: {
        "access_mode": AccessMode.CONTROLLED,
        "can_generate_data": False,
        "can_mutate_state": True,
        "can_run_destructive": False,
        "approval_required": False,  # except bulk ops
        "session_timeout_minutes": 240,  # 4 hours
    },
    Environment.PROD: {
        "access_mode": AccessMode.READ_ONLY,
        "can_generate_data": False,
        "can_mutate_state": False,
        "can_run_destructive": False,
        "approval_required": True,
        "session_timeout_minutes": 30,
    },
}


@dataclass
class SessionContext:
    """Isolated execution context for each user + environment combination."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    environment: Environment = Environment.SIT
    task: str = ""
    feature_branch: Optional[str] = None
    priority: Literal["critical", "high", "normal", "low"] = "normal"
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    credentials_ref: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    # These are set automatically based on environment
    access_mode: AccessMode = AccessMode.FULL
    can_generate_data: bool = True
    can_mutate_state: bool = True
    can_run_destructive: bool = True
    approval_required: bool = False

    def __post_init__(self):
        """Apply environment-specific rules automatically."""
        rules = ENV_RULES.get(self.environment, ENV_RULES[Environment.SIT])
        self.access_mode = rules["access_mode"]
        self.can_generate_data = rules["can_generate_data"]
        self.can_mutate_state = rules["can_mutate_state"]
        self.can_run_destructive = rules["can_run_destructive"]
        self.approval_required = rules["approval_required"]

        if self.expires_at is None:
            timeout = rules["session_timeout_minutes"]
            self.expires_at = self.created_at + timedelta(minutes=timeout)

    @property
    def is_expired(self) -> bool:
        if self.status in (SessionStatus.CANCELLED, SessionStatus.COMPLETED):
            return True
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def can_write(self) -> bool:
        return self.access_mode != AccessMode.READ_ONLY and not self.is_expired

    def validate_action(self, action: str) -> tuple[bool, str]:
        """Check if an action is allowed in this session's environment."""
        if self.is_expired:
            return False, f"Session expired at {self.expires_at}"
        if self.status != SessionStatus.ACTIVE:
            return False, f"Session is {self.status.value}"
        if action == "write" and not self.can_write:
            return False, f"Write not allowed in {self.environment.value} (read-only)"
        if action == "generate_data" and not self.can_generate_data:
            return False, f"Data generation not allowed in {self.environment.value}"
        if action == "destructive" and not self.can_run_destructive:
            return False, f"Destructive tests not allowed in {self.environment.value}"
        if action in ("destructive", "write") and self.approval_required:
            return False, f"Action '{action}' requires approval in {self.environment.value}"
        return True, "OK"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "environment": self.environment.value,
            "task": self.task,
            "feature_branch": self.feature_branch,
            "priority": self.priority,
            "status": self.status.value,
            "access_mode": self.access_mode.value,
            "can_write": self.can_write,
            "can_generate_data": self.can_generate_data,
            "can_run_destructive": self.can_run_destructive,
            "approval_required": self.approval_required,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_expired": self.is_expired,
        }
