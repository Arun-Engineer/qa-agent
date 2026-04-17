"""agent/workflows/langgraph_api_test.py — Self-healing API Test Agent.

Per-test subgraph:

    START
      │
      ▼
   generate_code          (LLM: pytest file for this step)
      │
      ▼
   run_pytest             (shell out to pytest)
      │
      ▼
   ┌── passed? ── yes ──► END
   │
   no
   │
   ▼
   diagnose               (LLM: analyse stderr + code, propose fix)
      │
      ▼
   regenerate_code        (LLM: new pytest file incorporating fix)
      │
      ▼
   run_pytest ↑ (loop up to LANGGRAPH_API_MAX_HEALS, default 2)

Plan() delegates to the legacy ApiTestWorkflow.plan() so prompt
maintenance stays in one place. Each generated step runs through the
self-healing subgraph instead of the linear generate-then-run path.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, TypedDict

from agent.core.base_workflow import BaseWorkflow
from agent.workflows.api_test import ApiTestWorkflow
from src.agents.langgraph_runtime import llm_text, make_checkpointer


class TestState(TypedDict, total=False):
    spec: str
    step: dict
    path: str
    code: str
    last_result: dict
    diagnosis: str
    heals: int
    max_heals: int


def _write_code(path: str, code: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code, encoding="utf-8")


def _node_generate(state: TestState) -> dict:
    """Generate initial pytest file via the existing TestGenerator."""
    from agent.codegen.generator import TestGenerator

    gen = TestGenerator()
    code = gen.generate_test_code(step=state.get("step") or {}, spec=state.get("spec", ""))
    path = state.get("path") or "tests/test_generated.py"
    _write_code(path, code)
    return {"code": code, "path": path}


def _node_run(state: TestState) -> dict:
    from agent.tools import pytest_runner
    path = state.get("path") or "tests/test_generated.py"
    result = pytest_runner.run_pytest(path=path, timeout=int(os.getenv("LANGGRAPH_API_PYTEST_TIMEOUT", "90")))
    return {"last_result": result or {}}


def _node_diagnose(state: TestState) -> dict:
    result = state.get("last_result") or {}
    code = state.get("code", "")
    stderr = (result.get("stderr") or "")[:4000]
    stdout = (result.get("stdout") or "")[:2000]
    user = (
        f"Test failed with exit code {result.get('exit_code')}.\n\n"
        f"--- pytest stderr ---\n{stderr}\n\n"
        f"--- pytest stdout ---\n{stdout}\n\n"
        f"--- current test code ---\n{code}\n"
    )
    diag = llm_text(
        messages=[
            {"role": "system", "content": _DIAGNOSE_PROMPT},
            {"role": "user", "content": user},
        ],
        service="langgraph-api-diagnose",
        temperature=0.2,
    )
    return {"diagnosis": diag}


def _node_regenerate(state: TestState) -> dict:
    from agent.codegen.generator import TestGenerator

    gen = TestGenerator()
    step = state.get("step") or {}
    # Prefer the generator's fix_error path if it supports it, otherwise
    # append the diagnosis as context into the spec seen by the LLM.
    try:
        import inspect as _inspect
        sig = _inspect.signature(gen.generate_test_code)
        kwargs: Dict[str, Any] = {"step": step, "spec": state.get("spec", "")}
        if "fix_error" in sig.parameters:
            kwargs["fix_error"] = state.get("diagnosis", "")
        code = gen.generate_test_code(**kwargs)
    except Exception:
        extended = (state.get("spec") or "") + "\n\n[Healing context]\n" + (state.get("diagnosis") or "")
        code = gen.generate_test_code(step=step, spec=extended)

    path = state.get("path") or "tests/test_generated.py"
    _write_code(path, code)
    return {"code": code, "heals": int(state.get("heals") or 0) + 1}


def _route_after_run(state: TestState) -> str:
    result = state.get("last_result") or {}
    if result.get("status") == "passed" or int(result.get("exit_code", 1) or 1) == 0:
        return "done"
    heals = int(state.get("heals") or 0)
    max_heals = int(state.get("max_heals") or 2)
    if heals >= max_heals:
        return "done"
    return "heal"


def _build_graph():
    from langgraph.graph import StateGraph, START, END
    g = StateGraph(TestState)
    g.add_node("generate", _node_generate)
    g.add_node("run", _node_run)
    g.add_node("diagnose", _node_diagnose)
    g.add_node("regenerate", _node_regenerate)
    g.add_edge(START, "generate")
    g.add_edge("generate", "run")
    g.add_conditional_edges("run", _route_after_run, {"done": END, "heal": "diagnose"})
    g.add_edge("diagnose", "regenerate")
    g.add_edge("regenerate", "run")
    return g.compile(checkpointer=make_checkpointer())


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


class LangGraphApiTestWorkflow(BaseWorkflow):

    @property
    def name(self) -> str:
        return "langgraph_api_test"

    @property
    def description(self) -> str:
        return "Self-healing API test agent (generate → run → diagnose → regenerate)"

    def __init__(self):
        # Reuse the legacy planner so prompt maintenance lives in one place.
        self._legacy = ApiTestWorkflow()

    def enrich(self, spec: str, context: Dict[str, Any]) -> str:
        return self._legacy.enrich(spec, context)

    def plan(self, spec: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return self._legacy.plan(spec, context)

    def execute_step(self, step: Dict[str, Any], spec: str, step_context: Dict[str, Any]) -> Any:
        tool = step.get("tool", "")
        # Only wrap pytest-driven steps. api_caller / bug_reporter fall through.
        if tool != "pytest_runner":
            return self._legacy.execute_step(step, spec, step_context)

        args = step.get("args") or {}
        path = args.get("path") or "tests/test_generated.py"
        init: TestState = {
            "spec": spec,
            "step": step,
            "path": path,
            "heals": 0,
            "max_heals": int(os.getenv("LANGGRAPH_API_MAX_HEALS", "2")),
        }
        thread_id = f"{step_context.get('run_id','run')}:{path}"
        try:
            final = _graph().invoke(init, config={"configurable": {"thread_id": thread_id}})
        except Exception as e:
            return {"status": "error", "error": str(e), "path": path}

        result = dict(final.get("last_result") or {})
        result["heals_used"] = int(final.get("heals") or 0)
        if final.get("diagnosis"):
            result["last_diagnosis"] = final["diagnosis"]
        return result

    def evaluate_step_result(self, step: Dict[str, Any], output: Any) -> str:
        return self._legacy.evaluate_step_result(step, output)

    def verify(self, run_result) -> None:
        # Preserve flaky detection semantics
        self._legacy.verify(run_result)


_DIAGNOSE_PROMPT = """You are a Senior QA engineer debugging a failing pytest run.

Analyse the stderr + current test code. Diagnose the root cause:
- Is it a test-code bug (wrong assertion, bad fixture, import error)?
- Is it an API contract issue (unexpected status, schema mismatch)?
- Is it an environment issue (missing env var, unreachable URL)?

Then describe EXACTLY what to change in the test code to make it pass.
Be concrete: which line, which function, which assertion. Do NOT paraphrase the error — identify the root cause.

Return 3-6 short bullet points. No code blocks."""
