"""
agent/workflows/visual_qa.py — Visual QA Agent

Workflow:
  1. Enrich: Extract URLs from spec, determine pages to check
  2. Plan: Build a screenshot + analysis plan
  3. Execute: Capture screenshots → Send to GPT-4o vision → Analyze
  4. Report: Structured comparison report with annotated findings

Use cases:
  - "Which products have Quick Tag on PLP but not PDP?"
  - "Check if all product images load on the cart page"
  - "Compare mobile vs desktop layout for the checkout flow"
  - "Verify the sale badge appears on all discounted items"
"""
from __future__ import annotations

import os
import re
import json
from typing import Any, Dict, List
from pathlib import Path

from agent.core.base_workflow import BaseWorkflow
from agent.core.llm_client import LLMClient
from agent.core.errors import ToolError


class VisualQaWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "visual_qa"

    @property
    def description(self) -> str:
        return "Screenshot pages and analyze visuals with AI vision (GPT-4o)"

    def __init__(self):
        self.llm = LLMClient()

    # ─── Enrichment ───

    def enrich(self, spec: str, context: Dict[str, Any]) -> str:
        """Extract all URLs from spec."""
        urls = re.findall(r'https?://[^\s\)\]\"\'>]+', spec)
        if urls:
            context["extracted_urls"] = [u.rstrip(".,;") for u in urls]
        return spec

    # ─── Planning ───

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use LLM to decide:
        - Which pages to screenshot
        - What to look for on each page
        - Whether to do full-page or element-level capture
        - What comparisons to make
        """
        urls = context.get("extracted_urls", [])
        url_list = "\n".join(f"  - {u}" for u in urls) if urls else "  (no URLs found in spec)"

        prompt = _VISUAL_PLAN_PROMPT.replace("{{SPEC}}", spec).replace("{{URLS}}", url_list)

        plan = self.llm.chat_json(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": spec},
            ],
            temperature=0.2,
            service_name="qa-agent-visual-planner",
        )

        return plan

    # ─── Execution ───

    def execute_step(
        self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]
    ) -> Any:
        tool = step.get("tool", "")
        args = step.get("args", {}) or {}

        if tool == "screenshot_page":
            return self._screenshot_page(args)

        elif tool == "screenshot_elements":
            return self._screenshot_elements(args)

        elif tool == "analyze_screenshot":
            return self._analyze_screenshot(args, step_context)

        elif tool == "compare_pages":
            return self._compare_pages(args, step_context)

        elif tool == "analyze_elements":
            return self._analyze_elements(args, step_context)

        else:
            return {"status": "skipped", "error": f"Unknown tool: {tool}"}

    def _screenshot_page(self, args: Dict) -> Dict:
        """Capture full page screenshot."""
        from agent.tools.screenshot_capture import capture_page

        result = capture_page(
            url=args.get("url", ""),
            label=args.get("label", ""),
            viewport_width=args.get("viewport_width", 1440),
            viewport_height=args.get("viewport_height", 900),
            full_page=args.get("full_page", True),
        )

        return {
            "status": result.status,
            "url": result.url,
            "page_title": result.page_title,
            "screenshot_count": len(result.screenshots),
            "screenshots": result.screenshots,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }

    def _screenshot_elements(self, args: Dict) -> Dict:
        """Capture individual element screenshots."""
        from agent.tools.screenshot_capture import capture_elements

        result = capture_elements(
            url=args.get("url", ""),
            selector=args.get("selector", ""),
            label_prefix=args.get("label_prefix", "element"),
            max_elements=args.get("max_elements", 20),
        )

        return {
            "status": result.status,
            "url": result.url,
            "page_title": result.page_title,
            "element_count": len(result.screenshots),
            "screenshots": result.screenshots,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }

    def _analyze_screenshot(self, args: Dict, step_context: Dict) -> Dict:
        """Analyze a screenshot with vision AI."""
        from agent.tools.vision_analyzer import analyze_image

        # Get screenshot from a previous step
        source_step = args.get("source_step", "")
        question = args.get("question", "Describe what you see on this page")

        screenshots = self._get_screenshots_from_context(source_step, step_context)
        if not screenshots:
            return {"status": "error", "error": f"No screenshots found from step: {source_step}"}

        # Analyze the first/main screenshot
        img = screenshots[0]
        result = analyze_image(
            base64_image=img.get("base64", ""),
            question=question,
            context=f"Page: {img.get('url', '')} | Label: {img.get('label', '')}",
        )

        return {
            "status": result.status,
            "analysis": result.analysis,
            "structured_data": result.structured_data,
            "tokens_used": result.tokens_used,
            "duration_ms": result.duration_ms,
            "error": result.error,
        }

    def _compare_pages(self, args: Dict, step_context: Dict) -> Dict:
        """Compare screenshots from multiple pages."""
        from agent.tools.vision_analyzer import compare_images

        source_steps = args.get("source_steps", [])
        question = args.get("question", "Compare these pages and identify differences")

        images = []
        for step_key in source_steps:
            screenshots = self._get_screenshots_from_context(step_key, step_context)
            for img in screenshots:
                images.append({
                    "base64": img.get("base64", ""),
                    "label": img.get("label", step_key),
                })

        if len(images) < 2:
            return {"status": "error", "error": "Need at least 2 screenshots to compare"}

        result = compare_images(
            images=images,
            question=question,
        )

        return {
            "status": result.status,
            "analysis": result.analysis,
            "structured_data": result.structured_data,
            "tokens_used": result.tokens_used,
            "duration_ms": result.duration_ms,
            "error": result.error,
        }

    def _analyze_elements(self, args: Dict, step_context: Dict) -> Dict:
        """Analyze individual elements (e.g., each product card)."""
        from agent.tools.vision_analyzer import analyze_elements

        source_step = args.get("source_step", "")
        question = args.get("question", "Does this element have a badge or tag?")

        screenshots = self._get_screenshots_from_context(source_step, step_context)
        if not screenshots:
            return {"status": "error", "error": f"No element screenshots from: {source_step}"}

        elements = [
            {"base64": s.get("base64", ""), "label": s.get("label", ""), "text_content": s.get("text_content", "")}
            for s in screenshots
        ]

        result = analyze_elements(
            elements=elements,
            question=question,
        )

        return {
            "status": result.status,
            "analysis": result.analysis,
            "structured_data": result.structured_data,
            "tokens_used": result.tokens_used,
            "duration_ms": result.duration_ms,
            "error": result.error,
        }

    def _get_screenshots_from_context(self, step_key: str, step_context: Dict) -> List[Dict]:
        """Extract screenshots from a previous step's output."""
        # Try step_N_output format
        for key in [step_key, f"step_{step_key}_output", f"step_{step_key}"]:
            output = step_context.get(key)
            if isinstance(output, dict) and "screenshots" in output:
                return output["screenshots"]
        # Try last_output
        last = step_context.get("last_output", {})
        if isinstance(last, dict) and "screenshots" in last:
            return last["screenshots"]
        return []

    # ─── Evaluation ───

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        if isinstance(output, dict):
            if output.get("status") == "ok":
                return "passed"
            if output.get("status") == "error":
                return "failed"
            if output.get("analysis"):
                return "passed"
        return "failed"

    # ─── Reporting ───

    def report(self, spec: str, plan: Dict, run_result) -> Dict[str, Any]:
        """Generate a visual QA report."""
        import datetime as dt

        report = {
            "type": "visual_qa_report",
            "spec": spec,
            "goal": run_result.goal,
            "timestamp": run_result.started_at,
            "duration_ms": run_result.duration_ms,
            "steps": [],
            "summary": "",
        }

        for step in run_result.steps:
            step_data = {
                "tool": step.tool,
                "status": step.status,
                "duration_ms": step.duration_ms,
            }
            if isinstance(step.output, dict):
                step_data["analysis"] = step.output.get("analysis", "")
                step_data["structured_data"] = step.output.get("structured_data", {})
                step_data["screenshot_count"] = step.output.get("screenshot_count", step.output.get("element_count", 0))
            report["steps"].append(step_data)

        # Generate summary
        try:
            analyses = [
                s.get("analysis", "")
                for s in report["steps"]
                if s.get("analysis")
            ]
            if analyses:
                summary_resp = self.llm.chat(
                    messages=[
                        {"role": "system", "content": _SUMMARY_PROMPT},
                        {"role": "user", "content": "\n\n".join(analyses)},
                    ],
                    temperature=0.2,
                    service_name="qa-agent-visual-summary",
                )
                report["summary"] = summary_resp.text
        except Exception as e:
            report["summary"] = f"Summary generation failed: {e}"

        # Save JSON report
        out_dir = Path("data/logs")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = out_dir / f"visual_qa_{ts}.json"
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        return {
            "report_json": str(report_path.name),
            "report": report,
        }


# ─── Prompts ───

_VISUAL_PLAN_PROMPT = """You are a Senior QA Architect planning a visual inspection of a web application.

Given a user spec, create a plan to:
1. Screenshot the relevant pages
2. Analyze what's visible on each
3. Compare across pages if needed
4. Report findings

Available tools:
- "screenshot_page": {"url": "...", "label": "PLP"} — full page screenshot
- "screenshot_elements": {"url": "...", "selector": ".product-card", "label_prefix": "product"} — individual element screenshots
- "analyze_screenshot": {"source_step": "0", "question": "..."} — analyze a screenshot with AI vision
- "compare_pages": {"source_steps": ["0", "2"], "question": "..."} — compare screenshots
- "analyze_elements": {"source_step": "1", "question": "..."} — analyze each element individually

Rules:
- Output ONLY valid JSON
- First capture screenshots, then analyze them (order matters — analysis needs source_step reference)
- source_step is the step INDEX (0-based) that captured the screenshots
- Include 4-10 steps
- Always end with a comparison or summary analysis step

URLs found in spec:
{{URLS}}

Output format:
{
  "goal": "Visual QA: ...",
  "assumptions": [],
  "steps": [
    {"tool": "screenshot_page", "args": {"url": "...", "label": "PLP"}},
    {"tool": "analyze_screenshot", "args": {"source_step": "0", "question": "..."}},
    ...
  ]
}

Spec:
{{SPEC}}
"""

_SUMMARY_PROMPT = """You are a QA Lead summarizing visual inspection findings.

Given the analysis results below, write a concise summary (5-8 sentences) covering:
- What was checked
- Key findings (what's present, what's missing)
- Visual inconsistencies across pages
- Recommended actions

Use Markdown formatting."""
