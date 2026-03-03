"""
src/memory/sql_store.py — SQL persistence for test runs, results, bugs.

Uses the existing SQLAlchemy setup (auth.db engine).
Adds new tables: test_runs, test_results, known_bugs.
"""
from __future__ import annotations

import datetime as dt
import uuid
import structlog
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import Base, SessionLocal

logger = structlog.get_logger()


# ── Models ────────────────────────────────────────────────────

class TestRun(Base):
    __tablename__ = "test_runs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    spec_text = Column(Text, nullable=True)
    target_url = Column(String, nullable=True)
    environment = Column(String, default="SIT")
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | running | passed | failed | error
    total_tests = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    duration_ms = Column(Float, default=0)
    strategy_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)


class TestResult(Base):
    __tablename__ = "test_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String, ForeignKey("test_runs.id"), nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    test_name = Column(String, nullable=False)
    test_file = Column(String, nullable=True)
    status = Column(String, default="unknown")  # passed | failed | skipped | error
    duration_ms = Column(Float, default=0)
    error_message = Column(Text, nullable=True)
    traceback = Column(Text, nullable=True)
    triage_category = Column(String, nullable=True)  # BUG | FLAKY | ENV | DATA | STALE
    triage_confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class KnownBug(Base):
    __tablename__ = "known_bugs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    module = Column(String, nullable=True)
    severity = Column(String, default="medium")
    status = Column(String, default="open")  # open | fixed | wontfix | duplicate
    external_id = Column(String, nullable=True)  # ADO/Jira work item ID
    external_url = Column(String, nullable=True)
    signature = Column(String, nullable=True)  # error signature for dedup
    embedding_id = Column(String, nullable=True)  # vector store ref
    first_seen = Column(DateTime, default=dt.datetime.utcnow)
    last_seen = Column(DateTime, default=dt.datetime.utcnow)
    occurrence_count = Column(Integer, default=1)
    metadata_json = Column(JSON, nullable=True)


# ── Repository ────────────────────────────────────────────────

class SQLStore:
    """CRUD operations for test persistence."""

    def __init__(self, db: Session | None = None):
        self._db = db

    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def create_run(self, tenant_id: str, **kwargs) -> TestRun:
        run = TestRun(tenant_id=tenant_id, **kwargs)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        logger.info("test_run_created", run_id=run.id, tenant=tenant_id)
        return run

    def update_run(self, run_id: str, **kwargs) -> TestRun | None:
        run = self.db.get(TestRun, run_id)
        if not run:
            return None
        for k, v in kwargs.items():
            if hasattr(run, k):
                setattr(run, k, v)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(self, run_id: str) -> TestRun | None:
        return self.db.get(TestRun, run_id)

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[TestRun]:
        return self.db.execute(
            select(TestRun)
            .where(TestRun.tenant_id == tenant_id)
            .order_by(TestRun.created_at.desc())
            .limit(limit)
        ).scalars().all()

    def add_result(self, run_id: str, tenant_id: str, **kwargs) -> TestResult:
        result = TestResult(run_id=run_id, tenant_id=tenant_id, **kwargs)
        self.db.add(result)
        self.db.commit()
        return result

    def get_results(self, run_id: str) -> list[TestResult]:
        return self.db.execute(
            select(TestResult).where(TestResult.run_id == run_id)
        ).scalars().all()

    def upsert_bug(self, tenant_id: str, title: str, signature: str, **kwargs) -> KnownBug:
        """Insert or increment occurrence of a known bug."""
        existing = self.db.execute(
            select(KnownBug).where(
                KnownBug.tenant_id == tenant_id,
                KnownBug.signature == signature,
            )
        ).scalar_one_or_none()

        if existing:
            existing.occurrence_count += 1
            existing.last_seen = dt.datetime.utcnow()
            for k, v in kwargs.items():
                if hasattr(existing, k) and v is not None:
                    setattr(existing, k, v)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        bug = KnownBug(tenant_id=tenant_id, title=title, signature=signature, **kwargs)
        self.db.add(bug)
        self.db.commit()
        self.db.refresh(bug)
        return bug

    def search_bugs(self, tenant_id: str, query: str = "", status: str = "open",
                    limit: int = 20) -> list[KnownBug]:
        stmt = select(KnownBug).where(KnownBug.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(KnownBug.status == status)
        if query:
            stmt = stmt.where(KnownBug.title.ilike(f"%{query}%"))
        return self.db.execute(stmt.order_by(KnownBug.last_seen.desc()).limit(limit)).scalars().all()

    def get_run_stats(self, tenant_id: str) -> dict:
        runs = self.list_runs(tenant_id, limit=1000)
        total = len(runs)
        total_passed = sum(r.passed for r in runs)
        total_failed = sum(r.failed for r in runs)
        return {
            "total_runs": total,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "pass_rate": round(total_passed / max(total_passed + total_failed, 1) * 100, 1),
        }

    def close(self):
        if self._db:
            self._db.close()
