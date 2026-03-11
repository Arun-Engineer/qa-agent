"""
tests/test_visual_qa.py — Tests for Visual QA Agent

Run: pytest tests/test_visual_qa.py -v
"""
import pytest
from unittest.mock import patch, MagicMock

from agent.tools.screenshot_capture import ScreenshotResult
from agent.tools.vision_analyzer import VisionResult, _try_parse_json


class TestScreenshotResult:

    def test_ok_result(self):
        r = ScreenshotResult(url="https://example.com", status="ok", page_title="Test")
        assert r.status == "ok"
        assert r.url == "https://example.com"

    def test_error_result(self):
        r = ScreenshotResult(url="https://example.com", status="error", error="timeout")
        assert r.status == "error"
        assert r.error == "timeout"

    def test_screenshots_list(self):
        r = ScreenshotResult(
            url="https://example.com", status="ok",
            screenshots=[
                {"label": "page", "base64": "abc123", "width": 1440, "height": 900},
            ],
        )
        assert len(r.screenshots) == 1
        assert r.screenshots[0]["label"] == "page"


class TestVisionResult:

    def test_ok_result(self):
        r = VisionResult(status="ok", analysis="Found 3 products with Quick Tag")
        assert r.status == "ok"
        assert "Quick Tag" in r.analysis

    def test_error_result(self):
        r = VisionResult(status="error", error="API key invalid")
        assert r.status == "error"

    def test_structured_data(self):
        r = VisionResult(
            status="ok", analysis="test",
            structured_data={"products": [{"name": "A", "has_tag": True}]},
        )
        assert r.structured_data["products"][0]["has_tag"] is True


class TestJsonParsing:

    def test_direct_json(self):
        result = _try_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = _try_parse_json(text)
        assert result == {"key": "value"}

    def test_invalid_json(self):
        result = _try_parse_json("not json at all")
        assert result == {}

    def test_empty_string(self):
        result = _try_parse_json("")
        assert result == {}


class TestVisualQaWorkflow:

    def test_name(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        assert wf.name == "visual_qa"
        assert "vision" in wf.description.lower() or "visual" in wf.description.lower()

    def test_enrich_extracts_urls(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        context = {}
        wf.enrich("Check https://example.com/plp and https://example.com/pdp", context)
        assert "extracted_urls" in context
        assert len(context["extracted_urls"]) == 2

    def test_enrich_no_urls(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        context = {}
        wf.enrich("Check the product page for tags", context)
        assert "extracted_urls" not in context

    def test_evaluate_ok_status(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        assert wf.evaluate_step_result({}, {"status": "ok", "analysis": "found stuff"}) == "passed"

    def test_evaluate_error_status(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        assert wf.evaluate_step_result({}, {"status": "error", "error": "timeout"}) == "failed"

    def test_evaluate_analysis_present(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        assert wf.evaluate_step_result({}, {"analysis": "some findings"}) == "passed"

    def test_evaluate_empty(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        assert wf.evaluate_step_result({}, {}) == "failed"
        assert wf.evaluate_step_result({}, None) == "failed"

    def test_get_screenshots_from_context(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()

        ctx = {
            "step_0_output": {
                "screenshots": [{"base64": "abc", "label": "PLP"}],
            }
        }
        result = wf._get_screenshots_from_context("0", ctx)
        assert len(result) == 1
        assert result[0]["label"] == "PLP"

    def test_get_screenshots_fallback_last_output(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()

        ctx = {
            "last_output": {
                "screenshots": [{"base64": "xyz", "label": "Cart"}],
            }
        }
        result = wf._get_screenshots_from_context("missing", ctx)
        assert len(result) == 1

    def test_get_screenshots_missing(self):
        from agent.workflows.visual_qa import VisualQaWorkflow
        wf = VisualQaWorkflow()
        result = wf._get_screenshots_from_context("missing", {})
        assert result == []


class TestWorkflowRegistryWithVisualQa:

    def test_visual_qa_registered(self):
        from agent.workflows import get_workflow, list_workflows
        wf = get_workflow("visual_qa")
        assert wf.name == "visual_qa"

    def test_list_includes_visual_qa(self):
        from agent.workflows import list_workflows
        wfs = list_workflows()
        assert "visual_qa" in wfs
