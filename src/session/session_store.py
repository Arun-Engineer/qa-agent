"""Session Store — In-memory storage for sessions and runs.

This will be replaced by PostgreSQL in Phase 4.
For Phase 1, in-memory is perfect for testing and development.
"""
from datetime import datetime
from typing import Optional
import uuid
import structlog

from src.session.session_context import SessionContext, Environment, SessionStatus

logger = structlog.get_logger()


class SessionStore:
    """Thread-safe in-memory session storage."""

    def __init__(self):
        self._sessions: dict[str, SessionContext] = {}
        self._runs: dict[str, dict] = {}
        logger.info("session_store_initialized", backend="in-memory")

    # --- Session Operations ---
    def create_session(self, user_id: str, environment: str, task: str,
                       priority: str = "normal", feature_branch: str = None) -> SessionContext:
        """Create a new isolated session."""
        ctx = SessionContext(
            user_id=user_id,
            environment=Environment(environment),
            task=task,
            priority=priority,
            feature_branch=feature_branch,
        )
        self._sessions[ctx.session_id] = ctx
        logger.info("session_created",
                    session_id=ctx.session_id,
                    user=user_id,
                    env=environment,
                    access_mode=ctx.access_mode.value)
        return ctx

    def get_session(self, session_id: str) -> Optional[SessionContext]:
        """Get a session by ID. Returns None if not found."""
        ctx = self._sessions.get(session_id)
        if ctx and ctx.is_expired and ctx.status == SessionStatus.ACTIVE:
            ctx.status = SessionStatus.EXPIRED
            logger.info("session_auto_expired", session_id=session_id)
        return ctx

    def list_sessions(self, user_id: str = None, environment: str = None,
                      status: str = None) -> list[SessionContext]:
        """List sessions with optional filters."""
        results = list(self._sessions.values())
        if user_id:
            results = [s for s in results if s.user_id == user_id]
        if environment:
            results = [s for s in results if s.environment.value == environment]
        if status:
            results = [s for s in results if s.status.value == status]
        return sorted(results, key=lambda s: s.created_at, reverse=True)

    def cancel_session(self, session_id: str) -> Optional[SessionContext]:
        """Cancel/stop a session."""
        ctx = self._sessions.get(session_id)
        if ctx:
            ctx.status = SessionStatus.CANCELLED
            logger.info("session_cancelled", session_id=session_id)
        return ctx

    def get_active_count(self) -> int:
        """Count active (non-expired) sessions."""
        return sum(1 for s in self._sessions.values()
                   if s.status == SessionStatus.ACTIVE and not s.is_expired)

    # --- Run Operations ---
    def create_run(self, session_id: str, test_type: str = "smoke",
                   target_url: str = None, description: str = None) -> Optional[dict]:
        """Create a test run within a session."""
        session = self.get_session(session_id)
        if not session:
            return None

        run = {
            "run_id": str(uuid.uuid4()),
            "session_id": session_id,
            "user_id": session.user_id,
            "environment": session.environment.value,
            "test_type": test_type,
            "target_url": target_url,
            "description": description,
            "status": "queued",
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "results_summary": None,
        }
        self._runs[run["run_id"]] = run
        logger.info("run_created", run_id=run["run_id"], session_id=session_id, type=test_type)
        return run

    def get_run(self, run_id: str) -> Optional[dict]:
        return self._runs.get(run_id)

    def list_runs(self, session_id: str = None, status: str = None) -> list[dict]:
        results = list(self._runs.values())
        if session_id:
            results = [r for r in results if r["session_id"] == session_id]
        if status:
            results = [r for r in results if r["status"] == status]
        return sorted(results, key=lambda r: r["created_at"], reverse=True)

    def update_run_status(self, run_id: str, status: str,
                          results_summary: dict = None) -> Optional[dict]:
        run = self._runs.get(run_id)
        if run:
            run["status"] = status
            if status in ("completed", "failed"):
                run["completed_at"] = datetime.utcnow().isoformat()
            if results_summary:
                run["results_summary"] = results_summary
            logger.info("run_updated", run_id=run_id, status=status)
        return run

    def get_total_runs(self) -> int:
        return len(self._runs)
