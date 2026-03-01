"""Tests for SessionContext — multi-tenant isolation."""
from src.session.session_context import SessionContext, Environment, AccessMode


class TestSessionContext:
    def test_sit_has_full_access(self):
        ctx = SessionContext(user_id="u1", environment=Environment.SIT, task="test")
        assert ctx.access_mode == AccessMode.FULL
        assert ctx.can_write is True
        assert ctx.can_generate_data is True
        assert ctx.can_run_destructive is True
        assert ctx.approval_required is False

    def test_uat_has_controlled_access(self):
        ctx = SessionContext(user_id="u1", environment=Environment.UAT, task="test")
        assert ctx.access_mode == AccessMode.CONTROLLED
        assert ctx.can_write is True
        assert ctx.can_generate_data is False
        assert ctx.can_run_destructive is False

    def test_prod_is_read_only(self):
        ctx = SessionContext(user_id="u1", environment=Environment.PROD, task="test")
        assert ctx.access_mode == AccessMode.READ_ONLY
        assert ctx.can_write is False
        assert ctx.can_generate_data is False
        assert ctx.can_run_destructive is False
        assert ctx.approval_required is True

    def test_prod_has_30min_timeout(self):
        ctx = SessionContext(user_id="u1", environment=Environment.PROD, task="test")
        diff = (ctx.expires_at - ctx.created_at).total_seconds()
        assert diff == 1800  # 30 minutes

    def test_validate_action_write_in_prod(self):
        ctx = SessionContext(user_id="u1", environment=Environment.PROD, task="test")
        allowed, reason = ctx.validate_action("write")
        assert allowed is False
        assert "read-only" in reason.lower()

    def test_validate_action_write_in_sit(self):
        ctx = SessionContext(user_id="u1", environment=Environment.SIT, task="test")
        allowed, reason = ctx.validate_action("write")
        assert allowed is True

    def test_validate_destructive_in_uat(self):
        ctx = SessionContext(user_id="u1", environment=Environment.UAT, task="test")
        allowed, reason = ctx.validate_action("destructive")
        assert allowed is False

    def test_session_serialization(self):
        ctx = SessionContext(user_id="u1", environment=Environment.SIT, task="test login")
        d = ctx.to_dict()
        assert d["user_id"] == "u1"
        assert d["environment"] == "sit"
        assert d["can_write"] is True
        assert "session_id" in d

    def test_unique_session_ids(self):
        s1 = SessionContext(user_id="u1", environment=Environment.SIT, task="a")
        s2 = SessionContext(user_id="u1", environment=Environment.SIT, task="b")
        assert s1.session_id != s2.session_id
