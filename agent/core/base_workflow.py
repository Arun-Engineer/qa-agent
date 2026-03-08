"""
agent/core/base_workflow.py — Base class for all agent workflows

Every workflow (API Test, UI Test, Spec Review) implements this interface.
The orchestrator calls these methods in order:
  1. enrich()  — optional spec enrichment
  2. plan()    — LLM generates execution plan
  3. execute_step()  — run each step
  4. evaluate_step_result() — determine pass/fail
  5. verify()  — post-execution checks
  6. report()  — generate artifacts
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from agent.core.errors import ToolError


class BaseWorkflow(ABC):
    """Abstract base for all QA agent workflows."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique workflow identifier, e.g. 'api_test', 'ui_test', 'spec_review'."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description."""
        return self.name

    # ─── Phase 1: Enrichment (optional) ───

    def enrich(self, spec: str, context: Dict[str, Any]) -> str:
        """
        Optionally enrich the spec with RAG, site recon, or other context.
        Default: return spec unchanged.
        Override in workflows that need it (e.g., UI test needs site recon).
        """
        return spec

    # ─── Phase 2: Planning ───

    @abstractmethod
    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate an execution plan from the spec.
        Must return: {"goal": "...", "steps": [...], "assumptions": [...]}
        Each step: {"tool": "...", "args": {...}}
        """
        ...

    # ─── Phase 3: Execution ───

    @abstractmethod
    def execute_step(
        self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]
    ) -> Any:
        """
        Execute a single step. Returns the tool output.
        step_context contains outputs from previous steps.
        """
        ...

    # ─── Phase 4: Evaluate ───

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        """
        Evaluate whether a step passed or failed.
        Returns: "passed" | "failed" | "skipped"
        Default implementation checks common patterns.
        """
        if output is None:
            return "failed"

        if isinstance(output, dict):
            # Check exit code (pytest/playwright)
            if "code" in output:
                return "passed" if output["code"] == 0 else "failed"

            # Check summary counts
            summary = output.get("summary", {})
            if isinstance(summary, dict) and "failed" in summary:
                return "passed" if int(summary.get("failed", 0)) == 0 else "failed"

            # Check status field
            status = output.get("status", "")
            if status in ("passed", "ok", "success"):
                return "passed"
            if status in ("failed", "error"):
                return "failed"
            if status == "skipped":
                return "skipped"

            # Check HTTP status for API calls
            if "status_code" in output or "ok" in output:
                ok = output.get("ok", False)
                code = output.get("status_code", output.get("status", 0))
                if ok or (isinstance(code, int) and 200 <= code < 300):
                    return "passed"
                return "failed"

        # String output — assume passed if non-empty
        if isinstance(output, str):
            return "passed" if output.strip() else "failed"

        return "passed"

    # ─── Phase 5: Verification (optional) ───

    def verify(self, run_result: "RunResult") -> None:
        """
        Post-execution verification.
        Can analyze results, flag flaky tests, etc.
        Default: no-op.
        """
        pass

    # ─── Phase 6: Reporting ───

    def report(
        self, spec: str, plan: Dict, run_result: "RunResult"
    ) -> Dict[str, Any]:
        """
        Generate artifacts (PDF, Excel, JSON).
        Default implementation uses existing reporting.py.
        Override for custom report formats.
        """
        try:
            from agent.utils.reporting import export_run_artifacts

            detailed_results = [
                {"step": s.tool, "result": s.output, "status": s.status}
                for s in run_result.steps
            ]
            artifacts = export_run_artifacts(spec, plan, detailed_results)
            return {
                "run_json": str(getattr(artifacts, "run_json", "")),
                "report_json": str(getattr(artifacts, "report_json", "") or ""),
                "pdf": str(getattr(artifacts, "pdf", "") or ""),
                "xlsx": str(getattr(artifacts, "xlsx", "") or ""),
            }
        except Exception as e:
            return {"error": str(e)}

    # ─── Helpers ───

    def _get_model(self) -> str:
        """Get configured LLM model."""
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def _get_provider(self) -> str:
        """Get configured LLM provider."""
        return os.getenv("LLM_PROVIDER", "openai")
