"""
tests/test_workflows.py — Tests for workflow registry and workflow classes

Run: pytest tests/test_workflows.py -v
"""
import pytest
from unittest.mock import patch, MagicMock

from agent.core.base_workflow import BaseWorkflow
from agent.core.errors import AgentError


class TestWorkflowRegistry:

    def test_list_workflows(self):
        from agent.workflows import list_workflows
        wf = list_workflows()
        assert "api_test" in wf
        assert "ui_test" in wf
        assert "spec_review" in wf

    def test_get_known_workflow(self):
        from agent.workflows import get_workflow
        wf = get_workflow("api_test")
        assert isinstance(wf, BaseWorkflow)
        assert wf.name == "api_test"

    def test_get_unknown_workflow_raises(self):
        from agent.workflows import get_workflow
        with pytest.raises(ValueError, match="Unknown workflow"):
            get_workflow("nonexistent_workflow")

    def test_legacy_alias(self):
        """generate_testcases should map to api_test."""
        from agent.workflows import get_workflow
        wf = get_workflow("generate_testcases")
        assert wf.name == "api_test"

    def test_default_alias(self):
        from agent.workflows import get_workflow
        wf = get_workflow("default")
        assert wf.name == "api_test"


class TestBaseWorkflow:

    def test_default_enrich_returns_spec(self):
        """Default enrich() returns spec unchanged."""
        class MinimalWorkflow(BaseWorkflow):
            @property
            def name(self):
                return "minimal"
            def plan(self, spec, context):
                return {}
            def execute_step(self, step, spec, step_context):
                return {}

        wf = MinimalWorkflow()
        assert wf.enrich("my spec", {}) == "my spec"

    def test_default_verify_noop(self):
        """Default verify() does nothing."""
        class MinimalWorkflow(BaseWorkflow):
            @property
            def name(self):
                return "minimal"
            def plan(self, spec, context):
                return {}
            def execute_step(self, step, spec, step_context):
                return {}

        wf = MinimalWorkflow()
        wf.verify(MagicMock())  # Should not raise

    def test_evaluate_passed_code_zero(self):
        class W(BaseWorkflow):
            name = "w"
            def plan(self, s, c): return {}
            def execute_step(self, s, sp, sc): return {}

        wf = W()
        assert wf.evaluate_step_result({}, {"code": 0}) == "passed"
        assert wf.evaluate_step_result({}, {"code": 1}) == "failed"

    def test_evaluate_summary_counts(self):
        class W(BaseWorkflow):
            name = "w"
            def plan(self, s, c): return {}
            def execute_step(self, s, sp, sc): return {}

        wf = W()
        assert wf.evaluate_step_result({}, {"summary": {"passed": 5, "failed": 0}}) == "passed"
        assert wf.evaluate_step_result({}, {"summary": {"passed": 3, "failed": 2}}) == "failed"

    def test_evaluate_status_field(self):
        class W(BaseWorkflow):
            name = "w"
            def plan(self, s, c): return {}
            def execute_step(self, s, sp, sc): return {}

        wf = W()
        assert wf.evaluate_step_result({}, {"status": "ok"}) == "passed"
        assert wf.evaluate_step_result({}, {"status": "error"}) == "failed"
        assert wf.evaluate_step_result({}, {"status": "skipped"}) == "skipped"

    def test_evaluate_none_output(self):
        class W(BaseWorkflow):
            name = "w"
            def plan(self, s, c): return {}
            def execute_step(self, s, sp, sc): return {}

        wf = W()
        assert wf.evaluate_step_result({}, None) == "failed"

    def test_evaluate_http_response(self):
        class W(BaseWorkflow):
            name = "w"
            def plan(self, s, c): return {}
            def execute_step(self, s, sp, sc): return {}

        wf = W()
        assert wf.evaluate_step_result({}, {"ok": True, "status_code": 200}) == "passed"
        assert wf.evaluate_step_result({}, {"ok": False, "status_code": 500}) == "failed"


class TestApiTestWorkflow:

    def test_name(self):
        from agent.workflows.api_test import ApiTestWorkflow
        wf = ApiTestWorkflow()
        assert wf.name == "api_test"
        assert "API" in wf.description

    @patch("agent.workflows.api_test.LLMClient")
    def test_plan_calls_llm(self, mock_llm_cls):
        from agent.workflows.api_test import ApiTestWorkflow

        mock_client = MagicMock()
        mock_client.chat_json.return_value = {
            "goal": "Test login API",
            "steps": [{"tool": "pytest_runner", "args": {"path": "tests/test_login.py"}}],
        }
        mock_llm_cls.return_value = mock_client

        wf = ApiTestWorkflow()
        plan = wf.plan("Test the login API", {})

        assert plan["goal"] == "Test login API"
        assert len(plan["steps"]) == 1
        mock_client.chat_json.assert_called_once()


class TestUiTestWorkflow:

    def test_name(self):
        from agent.workflows.ui_test import UiTestWorkflow
        wf = UiTestWorkflow()
        assert wf.name == "ui_test"
        assert "UI" in wf.description

    def test_basic_enrich_extracts_url(self):
        from agent.workflows.ui_test import UiTestWorkflow
        import os

        wf = UiTestWorkflow()
        # When understanding_layer is not available, falls back to basic URL extraction
        result = wf._basic_enrich("Test https://example.com/login", {})
        # _basic_enrich now extracts base URL (scheme://host) without path
        assert os.environ.get("APP_BASE_URL") == "https://example.com"


class TestSpecReviewWorkflow:

    def test_name(self):
        from agent.workflows.spec_review import SpecReviewWorkflow
        wf = SpecReviewWorkflow()
        assert wf.name == "spec_review"

    def test_plan_returns_fixed_dimensions(self):
        from agent.workflows.spec_review import SpecReviewWorkflow

        wf = SpecReviewWorkflow()
        plan = wf.plan("Test login feature", {})

        assert "steps" in plan
        dimensions = [s["args"]["dimension"] for s in plan["steps"]]
        assert "completeness" in dimensions
        assert "ambiguity" in dimensions
        assert "testability" in dimensions
        assert "test_scenarios" in dimensions
        assert "risk_assessment" in dimensions

    def test_evaluate_always_passes_on_completed(self):
        from agent.workflows.spec_review import SpecReviewWorkflow

        wf = SpecReviewWorkflow()
        assert wf.evaluate_step_result({}, {"status": "completed", "dimension": "x", "analysis": "..."}) == "passed"
        assert wf.evaluate_step_result({}, {"status": "error"}) == "failed"
