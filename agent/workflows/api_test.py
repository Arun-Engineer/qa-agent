"""
agent/workflows/api_test.py — API Test Agent

Workflow:
  1. Enrich spec with RAG context (if available)
  2. Plan: LLM generates pytest test steps for API endpoints
  3. Execute: Generate test code → run pytest → collect results
  4. Report: PDF + Excel + JSON artifacts

Handles:
  - REST API testing (GET, POST, PUT, DELETE)
  - Status code validation
  - Response schema validation
  - Auth token injection
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Dict

from agent.core.base_workflow import BaseWorkflow
from agent.core.llm_client import LLMClient
from agent.core.errors import ToolError, ExecutionError


class ApiTestWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "api_test"

    @property
    def description(self) -> str:
        return "Generate and execute API tests from a spec"

    def __init__(self):
        self.llm = LLMClient()

    # ─── Planning ───

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        prompt = _API_PLAN_PROMPT.replace("{{SPEC}}", spec)

        return self.llm.chat_json(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": spec},
            ],
            temperature=0.2,
            service_name="qa-agent-api-planner",
        )

    # ─── Enrichment ───

    def enrich(self, spec: str, context: Dict[str, Any]) -> str:
        """Enrich with RAG if available."""
        try:
            from tenancy.rag_store import rag_available, query_chunks

            if not rag_available():
                return spec

            tenant_id = context.get("tenant_id", "default")
            results = query_chunks(tenant_id, spec, n_results=3)
            if results:
                rag_context = "\n".join(r.get("text", "") for r in results)
                return f"{spec}\n\n--- Related Context ---\n{rag_context}"
        except Exception:
            pass
        return spec

    # ─── Execution ───

    def execute_step(
        self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]
    ) -> Any:
        tool = step.get("tool", "")
        args = step.get("args", {}) or {}

        if tool == "pytest_runner":
            return self._run_pytest(step, spec, args)
        elif tool == "api_caller":
            return self._run_api_call(args)
        elif tool == "bug_reporter":
            return self._run_bug_reporter(args)
        else:
            return {"status": "skipped", "error": f"Unknown tool: {tool}"}

    def _run_pytest(self, step: Dict, spec: str, args: Dict) -> Dict:
        """Generate test code if needed, then run pytest."""
        from agent.tools import pytest_runner

        path = Path(args.get("path", "tests/test_generated.py"))

        # Auto-generate test file if missing
        if not path.exists() or os.getenv("FORCE_REGEN_TESTS", "0") == "1":
            self._generate_test_file(step, spec, path)

        safe_args = _filter_args(pytest_runner.run_pytest, args)
        return pytest_runner.run_pytest(**safe_args)

    def _run_api_call(self, args: Dict) -> Dict:
        """Direct API call for quick validation."""
        from agent.tools import api_caller
        return api_caller.call_api(**args)

    def _run_bug_reporter(self, args: Dict) -> Dict:
        """File a bug report."""
        from agent.tools import bug_reporter
        safe = {k: v for k, v in args.items()
                if k in ["title", "severity", "details", "steps_to_reproduce"]}
        return bug_reporter.file_bug(**safe)

    def _generate_test_file(self, step: Dict, spec: str, path: Path):
        """Use LLM to generate pytest test code."""
        from agent.codegen.generator import TestGenerator

        gen = TestGenerator()
        gen_kwargs = {"step": step, "spec": spec}

        sig = inspect.signature(gen.generate_test_code)
        if "site_model_path" in sig.parameters:
            gen_kwargs["site_model_path"] = args.get("site_model_path")
        if "fix_error" in sig.parameters:
            gen_kwargs["fix_error"] = None

        code = gen.generate_test_code(**gen_kwargs)
        gen.write_test_file(code, path)

    # ─── Verification ───

    def verify(self, run_result) -> None:
        """Flag flaky tests — tests that passed on retry."""
        for step in run_result.steps:
            if step.status == "passed" and step.retries > 0:
                step.diagnosis = "flaky_passed_on_retry"


def _filter_args(func, args_dict: dict) -> dict:
    sig = inspect.signature(func)
    return {k: v for k, v in (args_dict or {}).items() if k in sig.parameters}


# ─── Prompts ───

_API_PLAN_PROMPT = """You are a Senior QA Architect specializing in API testing.

Given a user spec, generate a test execution plan as JSON.

Rules:
- Output ONLY valid JSON, no markdown
- Each step uses tool "pytest_runner" or "api_caller"
- pytest_runner steps need: {"tool": "pytest_runner", "args": {"path": "tests/test_<name>.py"}}
- api_caller steps need: {"tool": "api_caller", "args": {"method": "GET/POST/PUT/DELETE", "url": "...", "headers": {}, "body": {}}}
- Include 3-8 test steps covering happy path, edge cases, and error cases
- If auth is mentioned, include auth token setup step

Output format:
{
  "goal": "Test <what>",
  "assumptions": ["assumption1", "assumption2"],
  "steps": [
    {"tool": "pytest_runner", "args": {"path": "tests/test_example.py"}},
    ...
  ]
}

Spec:
{{SPEC}}
"""
