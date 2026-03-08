"""
agent/workflows/spec_review.py — Spec Review Agent

Workflow:
  1. Enrich: RAG lookup for related specs/past test results
  2. Plan: LLM analyzes the spec for quality
  3. Execute: Each "step" is an analysis dimension (completeness, testability, etc.)
  4. Report: Structured review with recommendations

NO test execution — this is pure analysis.

Output:
  - Gap analysis (missing acceptance criteria, edge cases)
  - Ambiguity detection (vague requirements)
  - Testability score (can this be automated?)
  - Suggested test scenarios
  - Risk assessment
"""
from __future__ import annotations

from typing import Any, Dict, List

from agent.core.base_workflow import BaseWorkflow
from agent.core.llm_client import LLMClient


class SpecReviewWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "spec_review"

    @property
    def description(self) -> str:
        return "Analyze a spec for quality, gaps, and testability"

    def __init__(self):
        self.llm = LLMClient()

    # ─── Planning ───

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        The 'plan' for spec review is a fixed set of analysis dimensions.
        No LLM call needed — the dimensions are predefined.
        """
        return {
            "goal": "Review spec for quality and testability",
            "assumptions": [],
            "steps": [
                {
                    "tool": "llm_analyze",
                    "args": {
                        "dimension": "completeness",
                        "prompt_key": "completeness",
                    },
                },
                {
                    "tool": "llm_analyze",
                    "args": {
                        "dimension": "ambiguity",
                        "prompt_key": "ambiguity",
                    },
                },
                {
                    "tool": "llm_analyze",
                    "args": {
                        "dimension": "testability",
                        "prompt_key": "testability",
                    },
                },
                {
                    "tool": "llm_analyze",
                    "args": {
                        "dimension": "test_scenarios",
                        "prompt_key": "test_scenarios",
                    },
                },
                {
                    "tool": "llm_analyze",
                    "args": {
                        "dimension": "risk_assessment",
                        "prompt_key": "risk_assessment",
                    },
                },
            ],
        }

    # ─── Execution ───

    def execute_step(
        self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]
    ) -> Any:
        tool = step.get("tool", "")
        args = step.get("args", {})

        if tool != "llm_analyze":
            return {"status": "skipped", "error": f"Unknown tool: {tool}"}

        dimension = args.get("dimension", "general")
        prompt = _REVIEW_PROMPTS.get(dimension, _REVIEW_PROMPTS["general"])
        prompt = prompt.replace("{{SPEC}}", spec)

        # Include previous analysis results for context
        prev_results = []
        for key, val in step_context.items():
            if key.startswith("step_") and isinstance(val, dict):
                prev_results.append(val.get("analysis", ""))

        if prev_results:
            prompt += "\n\nPrevious analysis context:\n" + "\n".join(prev_results)

        response = self.llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": spec},
            ],
            temperature=0.3,
            service_name=f"qa-agent-review-{dimension}",
        )

        return {
            "status": "completed",
            "dimension": dimension,
            "analysis": response.text,
            "tokens_used": response.tokens_used,
        }

    # ─── Evaluation ───

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        """Spec review steps always 'pass' — they produce analysis, not pass/fail."""
        if isinstance(output, dict) and output.get("status") == "completed":
            return "passed"
        return "failed"

    # ─── Reporting ───

    def report(
        self, spec: str, plan: Dict, run_result: "RunResult"
    ) -> Dict[str, Any]:
        """Generate a structured review report instead of test artifacts."""
        review = {
            "spec": spec,
            "goal": run_result.goal,
            "review_date": run_result.started_at,
            "dimensions": {},
            "summary": "",
        }

        for step in run_result.steps:
            if isinstance(step.output, dict) and "dimension" in step.output:
                review["dimensions"][step.output["dimension"]] = {
                    "analysis": step.output.get("analysis", ""),
                    "status": step.status,
                }

        # Generate summary from all dimensions
        try:
            all_analysis = "\n\n".join(
                f"## {dim}\n{data['analysis']}"
                for dim, data in review["dimensions"].items()
            )
            summary_resp = self.llm.chat(
                messages=[
                    {"role": "system", "content": _SUMMARY_PROMPT},
                    {"role": "user", "content": all_analysis},
                ],
                temperature=0.2,
                service_name="qa-agent-review-summary",
            )
            review["summary"] = summary_resp.text
        except Exception as e:
            review["summary"] = f"Summary generation failed: {e}"

        # Save as JSON
        import json
        import datetime as dt
        from pathlib import Path

        out_dir = Path("data/logs")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        report_path = out_dir / f"spec_review_{ts}.json"
        report_path.write_text(json.dumps(review, indent=2), encoding="utf-8")

        return {
            "report_json": str(report_path.name),
            "review": review,
        }


# ─── Prompts ───

_REVIEW_PROMPTS = {
    "completeness": """You are a Senior QA Architect reviewing a requirement spec for completeness.

Analyze the spec and identify:
1. Missing acceptance criteria
2. Undefined edge cases (empty inputs, max limits, special characters)
3. Missing error handling requirements
4. Unspecified user roles/permissions
5. Missing non-functional requirements (performance, security, accessibility)

Rate completeness: HIGH / MEDIUM / LOW
List specific gaps with suggestions.

Spec:
{{SPEC}}
""",

    "ambiguity": """You are a Senior QA Architect reviewing a requirement spec for ambiguity.

Find:
1. Vague terms ("should handle gracefully", "fast response", "user-friendly")
2. Missing quantification ("large number of users" → how many?)
3. Unclear conditional logic ("if applicable" → when exactly?)
4. Conflicting requirements
5. Undefined terms or acronyms

Rate ambiguity: HIGH (many issues) / MEDIUM / LOW (clear spec)
List each ambiguous phrase with a suggested clarification.

Spec:
{{SPEC}}
""",

    "testability": """You are a Senior QA Architect assessing testability.

For each requirement in the spec, evaluate:
1. Can it be verified with automated tests? (Yes/Partial/No)
2. What type of test? (Unit/Integration/API/UI/Manual)
3. Are acceptance criteria measurable?
4. Are test data requirements clear?

Give an overall testability score: 1-10
List any requirements that CANNOT be automated and why.

Spec:
{{SPEC}}
""",

    "test_scenarios": """You are a Senior QA Architect generating test scenarios.

From the spec, generate:
1. Happy path scenarios (3-5)
2. Negative/error scenarios (3-5)
3. Edge case scenarios (2-3)
4. Security scenarios (1-2 if applicable)
5. Performance scenarios (1-2 if applicable)

For each scenario, provide:
- Title
- Steps
- Expected result
- Priority: P0 (critical) / P1 (high) / P2 (medium) / P3 (low)

Spec:
{{SPEC}}
""",

    "risk_assessment": """You are a Senior QA Architect performing risk assessment.

Analyze the spec for:
1. High-risk areas (payment, auth, data loss)
2. Integration risks (third-party APIs, external dependencies)
3. Performance risks (high traffic, large data)
4. Security risks (injection, auth bypass, data exposure)
5. Regression risks (what could break existing features?)

Rate overall risk: HIGH / MEDIUM / LOW
Provide mitigation recommendations for each risk.

Spec:
{{SPEC}}
""",

    "general": """You are a Senior QA Architect. Analyze this spec and provide feedback.

Spec:
{{SPEC}}
""",
}

_SUMMARY_PROMPT = """You are a Senior QA Architect. Given the analysis results below, write a concise executive summary (5-8 sentences) covering:
- Overall spec quality
- Top 3 concerns
- Recommended actions before development starts
- Go/no-go recommendation for testing

Use Markdown formatting with ## headings."""
