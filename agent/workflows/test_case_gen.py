"""
agent/workflows/test_case_gen.py — Test Case Generation Agent

Produces a comprehensive, structured test-case backlog from a spec
(user stories, requirements doc, PRD) WITHOUT executing them.

Pipeline:
  1. plan()   — LLM extracts every discrete user story / requirement
  2. execute_step() — for each story, LLM generates happy / negative / edge /
                      security / perf cases with title, steps, expected, priority
  3. report() — aggregates into JSON + Markdown artifact
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from agent.core.base_workflow import BaseWorkflow
from agent.core.llm_client import LLMClient


class TestCaseGenerationWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "test_case_gen"

    @property
    def description(self) -> str:
        return "Generate structured test cases from a spec (no execution)"

    def __init__(self):
        self.llm = LLMClient()

    # ─── Planning ───

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        prompt = _EXTRACT_STORIES_PROMPT.replace("{{SPEC}}", spec[:16000])
        try:
            result = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": spec[:16000]},
                ],
                temperature=0.2,
                service_name="qa-agent-tc-extract",
            )
            stories = result.get("stories") if isinstance(result, dict) else None
            if not isinstance(stories, list) or not stories:
                raise ValueError("no stories extracted")
        except Exception:
            stories = [{"id": "US-1", "title": "Full spec", "text": spec[:4000]}]

        # Cap to 20 to keep run bounded; each becomes one step.
        stories = stories[:20]
        steps = [
            {
                "tool": "generate_cases",
                "args": {
                    "story_id": s.get("id") or f"US-{i+1}",
                    "title": s.get("title") or f"Story {i+1}",
                    "text": s.get("text") or s.get("description") or "",
                },
            }
            for i, s in enumerate(stories)
        ]
        return {
            "goal": f"Generate test cases for {len(steps)} user stories",
            "assumptions": [],
            "steps": steps,
        }

    # ─── Execution ───

    def execute_step(
        self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]
    ) -> Any:
        args = step.get("args") or {}
        story_id = args.get("story_id", "US-?")
        title = args.get("title", "")
        text = args.get("text", "") or spec[:2000]

        prompt = _GENERATE_CASES_PROMPT
        user_msg = (
            f"Story ID: {story_id}\n"
            f"Title: {title}\n\n"
            f"Story content:\n{text}\n\n"
            f"Generate test cases as JSON with a top-level \"cases\" array."
        )
        try:
            result = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                service_name="qa-agent-tc-generate",
            )
        except Exception as e:
            return {"status": "failed", "error": str(e), "story_id": story_id, "cases": []}

        cases = result.get("cases") if isinstance(result, dict) else []
        if not isinstance(cases, list):
            cases = []
        return {
            "status": "completed",
            "story_id": story_id,
            "story_title": title,
            "case_count": len(cases),
            "cases": cases,
        }

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        if isinstance(output, dict) and output.get("status") == "completed" and output.get("cases"):
            return "passed"
        return "failed"

    # ─── Reporting ───

    def report(self, spec: str, plan: Dict, run_result) -> Dict[str, Any]:
        import datetime as dt
        from pathlib import Path

        all_cases: List[Dict[str, Any]] = []
        per_story: List[Dict[str, Any]] = []
        for s in run_result.steps:
            out = s.output if isinstance(s.output, dict) else {}
            cases = out.get("cases") or []
            per_story.append({
                "story_id": out.get("story_id"),
                "story_title": out.get("story_title"),
                "case_count": len(cases),
                "status": s.status,
            })
            for c in cases:
                if isinstance(c, dict):
                    c = {**c, "story_id": out.get("story_id")}
                    all_cases.append(c)

        out_dir = Path("data/logs")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        json_path = out_dir / f"test_cases_{ts}.json"
        json_path.write_text(
            json.dumps({"stories": per_story, "cases": all_cases}, indent=2),
            encoding="utf-8",
        )

        md_lines = [f"# Test Cases ({len(all_cases)} total, {len(per_story)} stories)\n"]
        for story in per_story:
            md_lines.append(f"\n## {story['story_id']} — {story['story_title']}  ({story['case_count']} cases)")
            for c in all_cases:
                if c.get("story_id") != story["story_id"]:
                    continue
                md_lines.append(f"\n### {c.get('title','(untitled)')}  [{c.get('priority','P2')}]")
                md_lines.append(f"**Type:** {c.get('type','functional')}  ")
                if c.get("preconditions"):
                    md_lines.append(f"**Preconditions:** {c['preconditions']}  ")
                steps_list = c.get("steps") or []
                if steps_list:
                    md_lines.append("**Steps:**")
                    for i, st in enumerate(steps_list, 1):
                        md_lines.append(f"  {i}. {st}")
                if c.get("expected"):
                    md_lines.append(f"\n**Expected:** {c['expected']}")
        md_path = out_dir / f"test_cases_{ts}.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return {
            "report_json": json_path.name,
            "test_cases_md": md_path.name,
            "total_cases": len(all_cases),
            "total_stories": len(per_story),
        }


_EXTRACT_STORIES_PROMPT = """You are a Senior QA Architect. Read the spec below and extract EVERY discrete user story, requirement, or acceptance criterion as a separate item.

Return strict JSON:
{
  "stories": [
    {"id": "US-1", "title": "short title", "text": "the full story / requirement text verbatim from spec"},
    ...
  ]
}

Rules:
- Do NOT merge distinct stories.
- Preserve original wording in "text".
- If the spec has numbered user stories, keep their numbering as IDs.
- Return up to 30 stories; prefer granular over coarse.

Spec:
{{SPEC}}
"""

_GENERATE_CASES_PROMPT = """You are a Senior QA Architect. For the user story provided, generate a comprehensive test case backlog.

Return strict JSON:
{
  "cases": [
    {
      "title": "...",
      "type": "happy|negative|edge|security|performance|accessibility",
      "priority": "P0|P1|P2|P3",
      "preconditions": "...",
      "steps": ["step 1", "step 2", ...],
      "expected": "..."
    }
  ]
}

Coverage requirements per story:
- 2-3 happy path cases (P0/P1)
- 2-3 negative cases (invalid inputs, missing fields, unauthorized)
- 2 edge cases (boundaries, empty, max, special chars)
- 1 security case if the story involves auth / payment / PII
- 1 performance case if the story mentions load / scale / concurrency

Keep steps actionable and observable. Expected results must be verifiable.
"""
