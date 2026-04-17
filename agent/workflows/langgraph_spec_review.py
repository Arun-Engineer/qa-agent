"""agent/workflows/langgraph_spec_review.py — LangGraph parallel spec review.

Graph topology:

        START
          │
          ├──► completeness ─┐
          ├──► ambiguity     │
          ├──► testability   ├──► synthesize ──► END
          ├──► test_scenarios│
          └──► risk          ┘

All five dimensions run in parallel (LangGraph native fan-out via multiple
edges from START). A synthesis node then produces an executive summary.

Contrast with the legacy spec_review workflow, which runs them sequentially
and feeds each one's output as "context" into the next — that pattern gives
no real benefit AND is 5x slower. Here they are independent by design.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, TypedDict

from agent.core.base_workflow import BaseWorkflow
from src.agents.langgraph_runtime import llm_text, make_checkpointer


class ReviewState(TypedDict, total=False):
    spec: str
    completeness: str
    ambiguity: str
    testability: str
    test_scenarios: str
    risk_assessment: str
    summary: str


def _analyze(state: ReviewState, *, dimension: str, prompt: str) -> str:
    p = prompt.replace("{{SPEC}}", state.get("spec", "")[:14000])
    return llm_text(
        messages=[
            {"role": "system", "content": p},
            {"role": "user", "content": state.get("spec", "")[:14000]},
        ],
        service=f"langgraph-review-{dimension}",
        temperature=0.3,
    )


def _node_completeness(s: ReviewState) -> dict:
    return {"completeness": _analyze(s, dimension="completeness", prompt=_PROMPTS["completeness"])}


def _node_ambiguity(s: ReviewState) -> dict:
    return {"ambiguity": _analyze(s, dimension="ambiguity", prompt=_PROMPTS["ambiguity"])}


def _node_testability(s: ReviewState) -> dict:
    return {"testability": _analyze(s, dimension="testability", prompt=_PROMPTS["testability"])}


def _node_scenarios(s: ReviewState) -> dict:
    return {"test_scenarios": _analyze(s, dimension="test_scenarios", prompt=_PROMPTS["test_scenarios"])}


def _node_risk(s: ReviewState) -> dict:
    return {"risk_assessment": _analyze(s, dimension="risk_assessment", prompt=_PROMPTS["risk_assessment"])}


def _node_synthesize(s: ReviewState) -> dict:
    all_analysis = "\n\n".join(
        f"## {dim}\n{s.get(dim,'')}"
        for dim in ("completeness", "ambiguity", "testability", "test_scenarios", "risk_assessment")
        if s.get(dim)
    )
    summary = llm_text(
        messages=[
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": all_analysis},
        ],
        service="langgraph-review-summary",
        temperature=0.2,
    )
    return {"summary": summary}


def _build_graph():
    from langgraph.graph import StateGraph, START, END
    g = StateGraph(ReviewState)
    for name, fn in (
        ("completeness", _node_completeness),
        ("ambiguity", _node_ambiguity),
        ("testability", _node_testability),
        ("test_scenarios", _node_scenarios),
        ("risk", _node_risk),
    ):
        g.add_node(name, fn)
        # Parallel fan-out: every dimension runs from START concurrently
        g.add_edge(START, name)
        # Each dimension flows into synthesize; LangGraph waits for all.
        g.add_edge(name, "synthesize")
    g.add_node("synthesize", _node_synthesize)
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=make_checkpointer())


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


class LangGraphSpecReviewWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "langgraph_spec_review"

    @property
    def description(self) -> str:
        return "Parallel spec review (5 dimensions + synthesis via LangGraph)"

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "goal": "Parallel multi-dimensional spec review",
            "assumptions": ["parallel=true", "dimensions=5"],
            "steps": [{"tool": "langgraph_review_all", "args": {}}],
        }

    def execute_step(self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]) -> Any:
        run_id = step_context.get("run_id", "run")
        try:
            final = _graph().invoke(
                {"spec": spec},
                config={"configurable": {"thread_id": f"{run_id}:review"}},
            )
        except Exception as e:
            return {"status": "failed", "error": str(e)}
        return {
            "status": "completed",
            "dimensions": {
                "completeness": final.get("completeness", ""),
                "ambiguity": final.get("ambiguity", ""),
                "testability": final.get("testability", ""),
                "test_scenarios": final.get("test_scenarios", ""),
                "risk_assessment": final.get("risk_assessment", ""),
            },
            "summary": final.get("summary", ""),
        }

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        if isinstance(output, dict) and output.get("status") == "completed" and output.get("summary"):
            return "passed"
        return "failed"

    def report(self, spec: str, plan: Dict, run_result) -> Dict[str, Any]:
        import datetime as dt
        from pathlib import Path

        review: Dict[str, Any] = {"dimensions": {}, "summary": ""}
        for s in run_result.steps:
            out = s.output if isinstance(s.output, dict) else {}
            if out.get("dimensions"):
                review["dimensions"] = out["dimensions"]
            if out.get("summary"):
                review["summary"] = out["summary"]

        out_dir = Path("data/logs")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = out_dir / f"langgraph_spec_review_{ts}.json"
        json_path.write_text(json.dumps(review, indent=2), encoding="utf-8")

        md_lines = ["# Spec Review (LangGraph)\n"]
        if review["summary"]:
            md_lines.append("## Executive Summary\n")
            md_lines.append(review["summary"])
            md_lines.append("")
        for dim, txt in review["dimensions"].items():
            md_lines.append(f"\n## {dim}\n")
            md_lines.append(str(txt))
        md_path = out_dir / f"langgraph_spec_review_{ts}.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return {"report_json": json_path.name, "review_md": md_path.name, "review": review}


_PROMPTS = {
    "completeness": """You are a Senior QA Architect. Identify completeness gaps: missing acceptance criteria, undefined edge cases, missing error handling, unspecified roles/permissions, missing NFRs. Rate HIGH/MEDIUM/LOW. List specific gaps with suggestions.\n\nSpec:\n{{SPEC}}""",
    "ambiguity": """You are a Senior QA Architect. Find ambiguity: vague terms, missing quantification, unclear conditionals, conflicting requirements, undefined terms. Rate HIGH/MEDIUM/LOW. List each ambiguous phrase with a suggested clarification.\n\nSpec:\n{{SPEC}}""",
    "testability": """You are a Senior QA Architect. For each requirement, assess: can it be verified via automated tests? What type (unit/integration/API/UI/manual)? Are criteria measurable? Are test data requirements clear? Give an overall 1-10 score. List requirements that cannot be automated and why.\n\nSpec:\n{{SPEC}}""",
    "test_scenarios": """You are a Senior QA Architect. Generate test scenarios: 3-5 happy path, 3-5 negative, 2-3 edge, 1-2 security (if applicable), 1-2 performance (if applicable). For each: title, steps, expected, P0-P3 priority.\n\nSpec:\n{{SPEC}}""",
    "risk_assessment": """You are a Senior QA Architect. Identify high-risk areas (payment, auth, data loss), integration risks, performance risks, security risks, regression risks. Rate overall HIGH/MEDIUM/LOW. Provide mitigation recommendations per risk.\n\nSpec:\n{{SPEC}}""",
}

_SUMMARY_PROMPT = """You are a Senior QA Architect. Given the analysis results below, write a concise executive summary (5-8 sentences):
- Overall spec quality
- Top 3 concerns
- Recommended actions before development starts
- Go/no-go recommendation for testing
Use Markdown with ## headings."""
