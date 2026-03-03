"""Unit tests for SessionStore — sessions + runs lifecycle."""
import pytest
from src.session.session_store import SessionStore
from src.session.session_context import SessionStatus, AccessMode


class TestSessionStore:

    @pytest.fixture
    def store(self):
        return SessionStore()

    def test_create_session(self, store):
        ctx = store.create_session("user1", "sit", "Test login flow")
        assert ctx.user_id == "user1"
        assert ctx.environment.value == "sit"
        assert ctx.status == SessionStatus.ACTIVE
        assert ctx.access_mode == AccessMode.FULL

    def test_get_session(self, store):
        ctx = store.create_session("user1", "sit", "task")
        fetched = store.get_session(ctx.session_id)
        assert fetched is not None
        assert fetched.session_id == ctx.session_id

    def test_get_nonexistent(self, store):
        assert store.get_session("does-not-exist") is None

    def test_list_sessions(self, store):
        store.create_session("user1", "sit", "task1")
        store.create_session("user2", "uat", "task2")
        store.create_session("user1", "uat", "task3")

        all_sessions = store.list_sessions()
        assert len(all_sessions) == 3

        user1_sessions = store.list_sessions(user_id="user1")
        assert len(user1_sessions) == 2

        uat_sessions = store.list_sessions(environment="uat")
        assert len(uat_sessions) == 2

    def test_cancel_session(self, store):
        ctx = store.create_session("user1", "sit", "task")
        store.cancel_session(ctx.session_id)
        fetched = store.get_session(ctx.session_id)
        assert fetched.status == SessionStatus.CANCELLED

    def test_active_count(self, store):
        store.create_session("u1", "sit", "t1")
        store.create_session("u2", "sit", "t2")
        ctx3 = store.create_session("u3", "sit", "t3")
        store.cancel_session(ctx3.session_id)
        assert store.get_active_count() == 2

    def test_prod_session_is_read_only(self, store):
        ctx = store.create_session("user1", "prod", "observe")
        assert ctx.access_mode == AccessMode.READ_ONLY
        assert ctx.can_write is False
        assert ctx.approval_required is True


class TestRunLifecycle:

    @pytest.fixture
    def store_with_session(self):
        store = SessionStore()
        ctx = store.create_session("user1", "sit", "testing")
        return store, ctx.session_id

    def test_create_run(self, store_with_session):
        store, sid = store_with_session
        run = store.create_run(sid, test_type="smoke", target_url="https://example.com")
        assert run is not None
        assert run["status"] == "queued"
        assert run["session_id"] == sid

    def test_create_run_bad_session(self, store_with_session):
        store, _ = store_with_session
        run = store.create_run("nonexistent", test_type="smoke")
        assert run is None

    def test_update_run_status(self, store_with_session):
        store, sid = store_with_session
        run = store.create_run(sid)
        store.update_run_status(run["run_id"], "completed", {"passed": 5, "failed": 1})
        updated = store.get_run(run["run_id"])
        assert updated["status"] == "completed"
        assert updated["results_summary"]["passed"] == 5
        assert updated["completed_at"] is not None

    def test_list_runs(self, store_with_session):
        store, sid = store_with_session
        store.create_run(sid, test_type="smoke")
        store.create_run(sid, test_type="discovery")
        assert len(store.list_runs(session_id=sid)) == 2
        assert store.get_total_runs() == 2
