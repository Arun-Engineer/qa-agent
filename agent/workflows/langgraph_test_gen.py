"""agent/workflows/langgraph_test_gen.py — LangGraph-powered Test Case
Generation agent with self-correcting reflection loop.

Graph topology (per run):

       START
         │
         ▼
   extract_stories          (LLM: parse user stories from spec)
         │
         ▼
   ┌──► generate_cases      (LLM: produce test cases for current story)
   │     │
   │     ▼
   │   reflect              (LLM-as-judge: is coverage + specificity good?)
   │     │
   │     ├── if ok OR retries exhausted ──► advance ──► (more stories?)
   │     │                                     │
   │     │                                     ├── yes ──► back to generate_cases
   │     │                                     └── no  ──► finalize ──► END
   │     │
   │     └── if not ok AND retries left ──► back to generate_cases (with feedback)
   │

This mirrors the existing `test_case_gen` workflow but adds:
  * Reflection self-correction (better output quality)
  * Durable step-level state (resumable)
  * Transparent guardrails/cost/tracing via src/llm/compat.py chokepoint

Plan() extracts stories once (up-front) so the orchestrator's step counter
shows one step per story. Each execute_step runs the per-story subgraph
(generate → reflect → [retry] → finalize).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, TypedDict

from agent.core.base_workflow import BaseWorkflow
from src.agents.langgraph_runtime import llm_json, make_checkpointer


# ── Per-story subgraph state ─────────────────────────────────────────────────

class StoryState(TypedDict, total=False):
    story_id: str
    story_title: str
    story_text: str
    cases: list[dict]
    reflection: dict          # {"ok": bool, "feedback": str, "score": int}
    retries: int
    max_retries: int


def _node_generate_cases(state: StoryState) -> dict:
    fb = ""
    ref = state.get("reflection") or {}
    if ref and not ref.get("ok"):
        fb = f"\n\nPrevious attempt had issues. Address this feedback:\n{ref.get('feedback','')}\n"

    user = (
        f"Story ID: {state.get('story_id')}\n"
        f"Title: {state.get('story_title')}\n\n"
        f"Story content:\n{state.get('story_text','')}\n"
        f"{fb}"
        f"Return JSON with top-level \"cases\" array."
    )
    out = llm_json(
        messages=[
            {"role": "system", "content": _GENERATE_PROMPT},
            {"role": "user", "content": user},
        ],
        service="langgraph-tc-generate",
        temperature=0.25,
    )
    cases = out.get("cases") if isinstance(out, dict) else []
    if not isinstance(cases, list):
        cases = []
    return {"cases": cases}


def _node_reflect(state: StoryState) -> dict:
    cases = state.get("cases") or []
    preview = json.dumps(cases, ensure_ascii=False)[:6000]
    user = (
        f"Story: {state.get('story_title')}\n"
        f"Generated test cases (JSON):\n{preview}\n\n"
        "Judge quality. Return JSON: "
        "{\"ok\": bool, \"score\": 1-10, \"feedback\": \"specific issues\"}."
    )
    out = llm_json(
        messages=[
            {"role": "system", "content": _REFLECT_PROMPT},
            {"role": "user", "content": user},
        ],
        service="langgraph-tc-reflect",
        temperature=0.1,
    )
    ok = bool(out.get("ok")) if isinstance(out, dict) else False
    score = int(out.get("score", 0) or 0) if isinstance(out, dict) else 0
    feedback = str(out.get("feedback", "")) if isinstance(out, dict) else ""
    # Also accept "ok" if score is high even when LLM forgot the flag
    if not ok and score >= 8:
        ok = True
    return {"reflection": {"ok": ok, "score": score, "feedback": feedback}}


def _route_after_reflect(state: StoryState) -> str:
    ref = state.get("reflection") or {}
    retries = int(state.get("retries") or 0)
    max_retries = int(state.get("max_retries") or 2)
    if ref.get("ok"):
        return "finalize"
    if retries >= max_retries:
        return "finalize"
    return "retry"


def _node_retry(state: StoryState) -> dict:
    return {"retries": int(state.get("retries") or 0) + 1}


def _node_finalize(state: StoryState) -> dict:
    return {}


def _build_story_graph():
    from langgraph.graph import StateGraph, START, END
    g = StateGraph(StoryState)
    g.add_node("generate", _node_generate_cases)
    g.add_node("reflect", _node_reflect)
    g.add_node("retry", _node_retry)
    g.add_node("finalize", _node_finalize)
    g.add_edge(START, "generate")
    g.add_edge("generate", "reflect")
    g.add_conditional_edges("reflect", _route_after_reflect, {
        "retry": "retry",
        "finalize": "finalize",
    })
    g.add_edge("retry", "generate")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=make_checkpointer())


_STORY_GRAPH = None


def _story_graph():
    global _STORY_GRAPH
    if _STORY_GRAPH is None:
        _STORY_GRAPH = _build_story_graph()
    return _STORY_GRAPH


# ── Parallel fan-out ─────────────────────────────────────────────────────────

def _parallel_enabled() -> bool:
    return (os.getenv("LANGGRAPH_PARALLEL", "1") or "").strip().lower() not in ("0", "false", "no", "off")


def _max_workers() -> int:
    try:
        return max(1, min(10, int(os.getenv("LANGGRAPH_MAX_WORKERS", "5"))))
    except ValueError:
        return 5


def _make_init(args: Dict[str, Any], spec: str) -> StoryState:
    return {
        "story_id": args.get("story_id", "US-?"),
        "story_title": args.get("title", ""),
        "story_text": args.get("text", "") or spec[:2000],
        "cases": [],
        "reflection": {},
        "retries": 0,
        "max_retries": int(os.getenv("LANGGRAPH_MAX_REFLECT_RETRIES", "2")),
    }


def _story_output(final: Dict[str, Any]) -> Dict[str, Any]:
    ref = final.get("reflection") or {}
    return {
        "status": "completed",
        "story_id": final.get("story_id"),
        "story_title": final.get("story_title"),
        "case_count": len(final.get("cases") or []),
        "cases": final.get("cases") or [],
        "reflection_score": ref.get("score"),
        "retries_used": int(final.get("retries") or 0),
    }


def _run_batch_parallel(stories: list[dict], spec: str, run_id: str) -> Dict[str, Any]:
    """Fan out all stories across a thread pool — each runs the per-story
    self-correcting subgraph independently. LangGraph compiled graphs are
    thread-safe for .invoke() with distinct thread_ids."""
    from concurrent.futures import ThreadPoolExecutor
    from src.agents import progress_bus

    graph = _story_graph()
    results: list[dict] = []
    # threading.local doesn't cross thread boundaries — snapshot the parent
    # progress_id and re-activate it inside each worker so LLM calls from
    # fanned-out stories still surface to the live THINKING… indicator.
    parent_pid = progress_bus.current_id()

    def _one(story: dict) -> dict:
        if parent_pid:
            progress_bus.activate(parent_pid)
        try:
            init = _make_init(story, spec)
            thread_id = f"{run_id}:{story.get('story_id','US')}"
            try:
                final = graph.invoke(init, config={"configurable": {"thread_id": thread_id}})
                return _story_output(final)
            except Exception as e:
                return {"status": "failed", "error": str(e),
                        "story_id": story.get("story_id"), "cases": []}
        finally:
            if parent_pid:
                progress_bus.deactivate()

    with ThreadPoolExecutor(max_workers=_max_workers()) as pool:
        for r in pool.map(_one, stories):
            results.append(r)

    total_cases = sum(len(r.get("cases") or []) for r in results)
    return {
        "kind": "batch",
        "status": "completed",
        "story_count": len(stories),
        "total_cases": total_cases,
        "results": results,
    }


# ── Workflow ────────────────────────────────────────────────────────────────

class LangGraphTestGenWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "langgraph_test_gen"

    @property
    def description(self) -> str:
        return "LangGraph test case generation with self-correcting reflection"

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            res = llm_json(
                messages=[
                    {"role": "system", "content": _EXTRACT_PROMPT.replace("{{SPEC}}", spec[:16000])},
                    {"role": "user", "content": spec[:16000]},
                ],
                service="langgraph-tc-extract",
                temperature=0.2,
            )
            stories = res.get("stories") if isinstance(res, dict) else None
            if not isinstance(stories, list) or not stories:
                raise ValueError("no stories")
        except Exception:
            stories = [{"id": "US-1", "title": "Full spec", "text": spec[:4000]}]

        stories = stories[:20]
        normalized = [
            {
                "story_id": s.get("id") or f"US-{i+1}",
                "title": s.get("title") or f"Story {i+1}",
                "text": s.get("text") or s.get("description") or "",
            }
            for i, s in enumerate(stories)
        ]

        # Parallel mode: one batch step that fans out all stories concurrently.
        # Sequential mode: one step per story (granular UI progress).
        if _parallel_enabled():
            steps = [{"tool": "langgraph_batch", "args": {"stories": normalized}}]
        else:
            steps = [{"tool": "langgraph_story", "args": s} for s in normalized]

        return {
            "goal": f"Generate test cases for {len(normalized)} user stories (LangGraph)",
            "assumptions": [f"parallel={_parallel_enabled()}"],
            "steps": steps,
        }

    def execute_step(self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]) -> Any:
        tool = step.get("tool")
        args = step.get("args") or {}
        run_id = step_context.get("run_id", "run")

        if tool == "langgraph_batch":
            return _run_batch_parallel(args.get("stories") or [], spec, run_id)

        # Sequential per-story mode
        thread_id = f"{run_id}:{args.get('story_id','US')}"
        init: StoryState = _make_init(args, spec)
        try:
            final = _story_graph().invoke(init, config={"configurable": {"thread_id": thread_id}})
        except Exception as e:
            return {"status": "failed", "error": str(e), "story_id": init["story_id"], "cases": []}
        return _story_output(final)

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        if not isinstance(output, dict):
            return "failed"
        if output.get("status") == "completed" and output.get("cases"):
            return "passed"
        # Batch: pass if at least one story produced cases
        if output.get("kind") == "batch" and output.get("total_cases", 0) > 0:
            return "passed"
        return "failed"

    def report(self, spec: str, plan: Dict, run_result) -> Dict[str, Any]:
        import datetime as dt
        from pathlib import Path

        all_cases: List[Dict[str, Any]] = []
        per_story: List[Dict[str, Any]] = []
        for s in run_result.steps:
            out = s.output if isinstance(s.output, dict) else {}
            # Batch mode — one step yielded many stories
            if out.get("kind") == "batch":
                for sub in out.get("results") or []:
                    cases = sub.get("cases") or []
                    per_story.append({
                        "story_id": sub.get("story_id"),
                        "story_title": sub.get("story_title"),
                        "case_count": len(cases),
                        "reflection_score": sub.get("reflection_score"),
                        "retries_used": sub.get("retries_used"),
                        "status": "passed" if cases else "failed",
                    })
                    for c in cases:
                        if isinstance(c, dict):
                            all_cases.append({**c, "story_id": sub.get("story_id")})
                continue
            # Sequential mode — one step per story
            cases = out.get("cases") or []
            per_story.append({
                "story_id": out.get("story_id"),
                "story_title": out.get("story_title"),
                "case_count": len(cases),
                "reflection_score": out.get("reflection_score"),
                "retries_used": out.get("retries_used"),
                "status": s.status,
            })
            for c in cases:
                if isinstance(c, dict):
                    all_cases.append({**c, "story_id": out.get("story_id")})

        out_dir = Path("data/logs")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = out_dir / f"langgraph_test_cases_{ts}.json"
        json_path.write_text(
            json.dumps({"stories": per_story, "cases": all_cases}, indent=2),
            encoding="utf-8",
        )

        md = [f"# LangGraph Test Cases ({len(all_cases)} total, {len(per_story)} stories)\n"]
        for story in per_story:
            md.append(
                f"\n## {story['story_id']} — {story['story_title']}  "
                f"({story['case_count']} cases, score={story.get('reflection_score')}, "
                f"retries={story.get('retries_used')})"
            )
            for c in all_cases:
                if c.get("story_id") != story["story_id"]:
                    continue
                md.append(f"\n### {c.get('title','(untitled)')}  [{c.get('priority','P2')}]")
                md.append(f"**Type:** {c.get('type','functional')}  ")
                if c.get("preconditions"):
                    md.append(f"**Preconditions:** {c['preconditions']}  ")
                for i, st in enumerate(c.get("steps") or [], 1):
                    md.append(f"  {i}. {st}")
                if c.get("expected"):
                    md.append(f"\n**Expected:** {c['expected']}")
        md_path = out_dir / f"langgraph_test_cases_{ts}.md"
        md_path.write_text("\n".join(md), encoding="utf-8")

        return {
            "report_json": json_path.name,
            "test_cases_md": md_path.name,
            "total_cases": len(all_cases),
            "total_stories": len(per_story),
        }


# ── Prompts ─────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """You are a Senior QA Architect. Extract EVERY distinct user story, requirement, or acceptance criterion from the spec as a separate item.

Return strict JSON:
{"stories": [{"id": "US-1", "title": "...", "text": "verbatim requirement text"}, ...]}

Rules:
- Do not merge distinct stories.
- Preserve original wording.
- Up to 30 items; prefer granular.

Spec:
{{SPEC}}
"""

_GENERATE_PROMPT = """You are a Senior QA Architect. For the user story provided, generate a comprehensive test case backlog.

Return strict JSON:
{"cases": [
  {
    "title": "...",
    "type": "happy|negative|edge|security|performance|accessibility",
    "priority": "P0|P1|P2|P3",
    "preconditions": "...",
    "steps": ["step 1", "step 2", ...],
    "expected": "..."
  }
]}

Coverage per story:
- 2-3 happy path cases (P0/P1)
- 2-3 negative cases (invalid inputs, missing fields, unauthorized)
- 2 edge cases (boundaries, empty, max, special chars)
- 1 security case if auth/payment/PII involved
- 1 performance case if load/scale mentioned

Steps must be actionable and observable. Expected results must be verifiable.
"""

_REFLECT_PROMPT = """You are a Senior QA reviewer judging a generated test case backlog for a single user story.

Evaluate:
1. Coverage — happy + negative + edge all present?
2. Specificity — are steps observable and expected results verifiable?
3. Traceability — do cases clearly map to the story?
4. Priority — reasonable P0-P3 assignment?

Return strict JSON:
{"ok": true|false, "score": 1-10, "feedback": "specific actionable critique if not ok"}

Pass threshold: score >= 8. If below, feedback MUST explain what to add/change.
"""
