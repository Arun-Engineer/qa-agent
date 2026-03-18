"""
agent/workflows/ui_test.py — UI Test Agent

Workflow:
  1. Enrich: Crawl target site (recon), build site model
  2. Plan: LLM generates Playwright test steps — USER SPEC IS AUTHORITATIVE
  3. Execute: Generate Playwright test code → run → collect results
  4. Report: PDF + Excel + JSON with screenshots

Key fix: If recon only found a login wall, login becomes Step 0 (prerequisite),
and the plan covers all user-defined scenarios regardless of what was crawled.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Dict, List

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
        self._login_wall_detected = False
        self._user_scenarios: List[str] = []

    # ─── Enrichment (Site Recon) ───

    def enrich(self, spec: str, context: Dict[str, Any]) -> str:
        """
        Crawl the target site to build a site model.
        IMPORTANT: We detect login walls here and flag them.
        The enriched spec frames recon data as ADVISORY, spec as AUTHORITATIVE.
        """
        try:
            from agent.understanding_layer import enrich_spec_with_understanding

            enriched_spec, ctx = enrich_spec_with_understanding(
                spec, max_pages=25, max_depth=2
            )
            self._site_model_path = ctx.site_model_path
            self._login_wall_detected = ctx.login_wall_detected
            self._user_scenarios = ctx.user_scenarios

            # Store flags in context for plan() to use
            context["login_wall_detected"] = ctx.login_wall_detected
            context["user_scenarios"] = ctx.user_scenarios
            context["base_url"] = ctx.base_url

            return enriched_spec

        except ImportError:
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
            os.environ["APP_BASE_URL"] = base_url
            os.environ["BASE_URL"] = base_url
            context["base_url"] = base_url
        return spec

    # ─── Planning ───

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Delegate to the fixed Planner class which handles credentials,
        login wall detection, and spec-priority enforcement."""
        from agent.planner import Planner
        p = Planner()
        return p.generate_plan(spec, context=context)


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

        # Never regenerate auth prerequisite - it has verified working selectors
        spec_lower = (spec or '').lower()
        is_jiomart = 'jiomart' in spec_lower or 'jiomartjcp' in spec_lower
        is_auth = is_jiomart and ('auth' in str(path).lower() or 'prerequisite' in str(path).lower())
        should_regen = (not path.exists() or os.getenv("FORCE_REGEN_TESTS", "0") == "1") and not is_auth
        if should_regen:
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


def _login_wall_instructions(detected: bool) -> str:
    if not detected:
        return "(no login wall detected — proceed with normal test plan)"
    return (
        "⚠️  LOGIN WALL DETECTED — CRITICAL INSTRUCTION:\n"
        "The crawler was blocked by a login page. The REAL application is behind authentication.\n"
        "You MUST:\n"
        "  1. Insert a login prerequisite as Step 0 (test file: tests/test_step0_login_prerequisite.py)\n"
        "  2. Generate ALL remaining steps from the USER SPEC SCENARIOS — not from what was crawled\n"
        "  3. Mark remaining steps with requires_auth: true\n"
        "  4. Do NOT produce a plan that only tests the login form\n"
        "A plan with only login tests when the user asked for cart/checkout/product tests = FAILURE."
    )


def _scenario_count_instruction(scenarios: List[str]) -> str:
    if not scenarios:
        return "(no explicit scenario list detected — generate steps based on spec content)"
    n = len(scenarios)
    scenario_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(scenarios))
    return (
        f"The user defined {n} explicit scenario(s). "
        f"Your plan MUST include {n} test steps covering them (plus login prerequisite if needed).\n"
        f"Scenarios:\n{scenario_list}"
    )


def _filter_args(func, args_dict: dict) -> dict:
    sig = inspect.signature(func)
    return {k: v for k, v in (args_dict or {}).items() if k in sig.parameters}


# ─── Prompts ───

_UI_PLAN_PROMPT = """You are a Senior QA Architect specializing in UI/browser testing with Playwright.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 1 — SPEC IS AUTHORITATIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The user spec defines WHAT to test. Site model provides HOW (selectors/structure).
Never let site model override or reduce the user's test scenarios.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 2 — LOGIN WALL HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{LOGIN_WALL_FLAG}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 3 — SCENARIO COUNT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{SCENARIO_COUNT_INSTRUCTION}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Output ONLY valid JSON, no markdown.

{
  "goal": "Test <what>",
  "login_wall_detected": true | false,
  "user_scenario_count": <N>,
  "assumptions": ["assumption1", "assumption2"],
  "steps": [
    {
      "tool": "playwright_runner",
      "args": {
        "path": "tests/test_<N>_<slug>.py",
        "description": "<maps to specific user scenario>",
        "priority": "P0 | P1 | P2",
        "requires_auth": true | false,
        "is_prerequisite": true | false,
        "linked_scenario": "<exact user scenario text>",
        "base_url": "<url from spec>"
      }
    }
  ]
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SITE MODEL (advisory — use for selectors only)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{SITE_MODEL}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPEC (authoritative — defines all test scenarios)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{SPEC}}
"""
