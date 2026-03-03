"""Unit tests for PROD safety guardrails."""
import pytest
from src.guardrails.prod_safety import check_action


class TestProdSafety:

    def test_sit_allows_everything(self):
        for action in ["write", "generate_data", "destructive_test", "form_submit", "navigate"]:
            result = check_action(action, "sit")
            assert result.allowed, f"SIT should allow {action}"

    def test_prod_blocks_mutations(self):
        blocked = ["form_submit", "delete", "write", "generate_data", "destructive_test",
                    "create_account", "modify_state", "post_data"]
        for action in blocked:
            result = check_action(action, "prod")
            assert not result.allowed, f"PROD should block {action}"

    def test_prod_allows_read_only(self):
        allowed = ["navigate", "screenshot", "read_dom", "capture_network",
                    "classify_page", "fingerprint_components", "observe"]
        for action in allowed:
            result = check_action(action, "prod")
            assert result.allowed, f"PROD should allow {action}"

    def test_prod_unknown_action_blocked(self):
        result = check_action("some_new_action", "prod")
        assert not result.allowed
        assert "fail-safe" in result.reason

    def test_prod_override(self):
        result = check_action("write", "prod", override=True)
        assert result.allowed
        assert "override" in result.reason

    def test_uat_blocks_destructive(self):
        result = check_action("destructive_test", "uat")
        assert not result.allowed

    def test_uat_allows_normal(self):
        result = check_action("navigate", "uat")
        assert result.allowed

    def test_unknown_env_treated_as_prod(self):
        result = check_action("write", "staging")
        assert not result.allowed
        assert "fail-safe" in result.reason

    def test_case_insensitive(self):
        result = check_action("NAVIGATE", "PROD")
        assert result.allowed

    def test_result_fields(self):
        result = check_action("navigate", "sit")
        assert result.action == "navigate"
        assert result.environment == "sit"
        assert isinstance(result.reason, str)
