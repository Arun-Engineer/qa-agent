"""
agent/workflows/ui_test.py — UI Test Agent

Workflow:
  1. Enrich: Crawl target site (recon), build site model
  2. Plan: LLM generates Playwright test steps using site model
  3. Execute: Generate Playwright test code → run → collect results
  4. Report: PDF + Excel + JSON with screenshots

Handles:
  - Login flows, form submission, navigation
  - Element interaction (click, type, select)
  - Visual validation (element presence, text content)
  - Multi-page flows
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Dict

from agent.core.base_workflow import BaseWorkflow
from agent.core.llm_client import LLMClient
from agent.core.errors import ToolError


class UiTestWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "ui_test"

    @property
    def description(self) -> str:
        return "Generate and execute UI/browser tests from a spec"

    def __init__(self):
        self.llm = LLMClient()
        self._site_model_path = None

    # ─── Enrichment (Site Recon) ───

    def enrich(self, spec: str, context: Dict[str, Any]) -> str:
        """
        Crawl the target site to build a site model.
        The site model gives the LLM real page structure, selectors, forms.
        """
        try:
            from agent.understanding_layer import enrich_spec_with_understanding

            enriched_spec, ctx = enrich_spec_with_understanding(
                spec, max_pages=25, max_depth=2
            )
            self._site_model_path = ctx.site_model_path
            return enriched_spec

        except ImportError:
            # understanding_layer not available — try basic URL extraction
            return self._basic_enrich(spec, context)
        except Exception:
            return spec

    def _basic_enrich(self, spec: str, context: Dict[str, Any]) -> str:
        """Fallback: extract URL and set env vars."""
        import re

        from urllib.parse import urlparse
        match = re.search(r"https?://[^\s)]+", spec)
        if match:
            raw_url = match.group(0).rstrip(".,;")
            parsed = urlparse(raw_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        match = re.search(r"https?://[^\s)]+", spec)
        if match:
            base_url = match.group(0).rstrip(".,;")
            os.environ["APP_BASE_URL"] = base_url
            os.environ["BASE_URL"] = base_url
        return spec

    # ─── Planning ───

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        site_model = self._load_site_model()
        prompt = _UI_PLAN_PROMPT.replace("{{SPEC}}", spec).replace("{{SITE_MODEL}}", site_model)

        return self.llm.chat_json(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": spec},
            ],
            temperature=0.2,
            service_name="qa-agent-ui-planner",
        )

    # ─── Execution ───

    def execute_step(
        self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]
    ) -> Any:
        tool = step.get("tool", "")
        args = step.get("args", {}) or {}

        if tool == "playwright_runner":
            return self._run_playwright(step, spec, args)
        elif tool == "pytest_runner":
            return self._run_pytest(step, spec, args)
        elif tool == "bug_reporter":
            from agent.tools import bug_reporter
            safe = {k: v for k, v in args.items()
                    if k in ["title", "severity", "details", "steps_to_reproduce"]}
            return bug_reporter.file_bug(**safe)
        else:
            return {"status": "skipped", "error": f"Unknown tool: {tool}"}

    def _run_playwright(self, step: Dict, spec: str, args: Dict) -> Dict:
        """Generate Playwright test if needed, then run."""
        from agent.tools import playwright_runner

        path = Path(args.get("path", "tests/test_ui_generated.py"))

        if not path.exists() or os.getenv("FORCE_REGEN_TESTS", "0") == "1":
            self._generate_test_file(step, spec, path)

        safe_args = _filter_args(playwright_runner.run_playwright, args)
        return playwright_runner.run_playwright(**safe_args)

    def _run_pytest(self, step: Dict, spec: str, args: Dict) -> Dict:
        """Fallback to pytest for Playwright tests."""
        from agent.tools import pytest_runner

        path = Path(args.get("path", "tests/test_ui_generated.py"))
        if not path.exists() or os.getenv("FORCE_REGEN_TESTS", "0") == "1":
            self._generate_test_file(step, spec, path)

        safe_args = _filter_args(pytest_runner.run_pytest, args)
        return pytest_runner.run_pytest(**safe_args)

    def _generate_test_file(self, step: Dict, spec: str, path: Path):
        """Generate Playwright test code via LLM."""
        from agent.codegen.generator import TestGenerator

        gen = TestGenerator()
        gen_kwargs = {"step": step, "spec": spec}

        sig = inspect.signature(gen.generate_test_code)
        if "site_model_path" in sig.parameters:
            gen_kwargs["site_model_path"] = self._site_model_path
        if "fix_error" in sig.parameters:
            gen_kwargs["fix_error"] = None

        code = gen.generate_test_code(**gen_kwargs)
        gen.write_test_file(code, path)

    # ─── Helpers ───

    def _load_site_model(self) -> str:
        """Load crawled site model if available."""
        if not self._site_model_path:
            return "(no site model available — recon skipped or failed)"
        try:
            p = Path(self._site_model_path)
            if p.exists():
                return p.read_text(encoding="utf-8")[:12000]
        except Exception:
            pass
        return "(site model file not found)"


def _filter_args(func, args_dict: dict) -> dict:
    sig = inspect.signature(func)
    return {k: v for k, v in (args_dict or {}).items() if k in sig.parameters}


# ─── Prompts ───

_UI_PLAN_PROMPT = """You are a Senior QA Architect specializing in UI/browser testing with Playwright.

Given a user spec and optional site model, generate a test execution plan as JSON.

Rules:
- Output ONLY valid JSON, no markdown
- Each step uses tool "playwright_runner" or "pytest_runner"
- Steps need: {"tool": "playwright_runner", "args": {"path": "tests/test_ui_<n>.py"}}
- Include 3-8 test steps covering: page load, form interactions, navigation, error states
- If login is mentioned, first step should test login flow
- If site model is provided, use real selectors from it

Site Model (auto-discovered pages and elements):
{{SITE_MODEL}}

Output format:
{
  "goal": "Test <what>",
  "assumptions": ["assumption1", "assumption2"],
  "steps": [
    {"tool": "playwright_runner", "args": {"path": "tests/test_ui_login.py"}},
    ...
  ]
}

Spec:
{{SPEC}}
"""
