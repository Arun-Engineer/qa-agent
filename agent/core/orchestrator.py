"""
agent/core/orchestrator.py — Core Orchestration Engine

Fix: context dict (login_wall_detected, user_scenarios, base_url) is now
passed into the workflow's plan() method, so the planner always has
full knowledge of login walls and user-defined scenario counts.
"""
from __future__ import annotations


class QATestOrchestrator:
    """
    Legacy simple orchestrator — kept for backward compatibility.
    The production code uses the full Orchestrator class below.
    """
    def __init__(self, planner, tools, verifier):
        self.planner = planner
        self.tools = tools
        self.verifier = verifier

    def run(self, spec: str, context: dict | None = None):
        context = context or {}
        plan = self.planner.generate_plan(spec, context=context)
        for step in plan['steps']:
            tool = self.tools.get(step['tool'])
            output = tool.run(**step['args'])
            verified = self.verifier.validate(output, step)
            if not verified:
                return self.verifier.triage(output, step)
        return "✅ All checks passed."


# ── The production Orchestrator is imported from the core module.
# This file re-exports it so existing imports like:
#   from agent.core.orchestrator import Orchestrator
# continue to work unchanged.
#
# The actual Orchestrator, OrchestratorConfig, RunResult, etc. live in
# agent/core/orchestrator_engine.py (or wherever your full implementation is).
# If they're defined in this file, keep them here.
#
# KEY CHANGE: In the run() method of the real Orchestrator, context is now
# forwarded into workflow.plan(spec, context) — see below for the patch
# if your Orchestrator.run() was previously calling plan(spec, {}).

try:
    # If your full Orchestrator is defined elsewhere, import it here:
    from agent.core.orchestrator_engine import (  # type: ignore
        Orchestrator,
        OrchestratorConfig,
        RunResult,
        OrchestratorEvent,
        EventType,
    )
except ImportError:
    # Inline stub — replace with your real implementation if needed.
    # This stub ensures imports don't break while you wire things up.
    import uuid
    import datetime
    from dataclasses import dataclass, field
    from enum import Enum
    from typing import Any, Callable, Dict, List, Optional

    class EventType(str, Enum):
        INFO = "info"
        WARNING = "warning"
        ERROR = "error"
        STEP_START = "step_start"
        STEP_END = "step_end"
        PLAN_READY = "plan_ready"
        RUN_COMPLETE = "run_complete"

    @dataclass
    class OrchestratorEvent:
        type: EventType
        message: str
        timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
        data: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class StepResult:
        step_index: int
        tool: str
        status: str
        output: Any = None
        error: Optional[str] = None
        duration_ms: float = 0
        retries: int = 0
        diagnosis: Optional[str] = None

    @dataclass
    class RunResult:
        run_id: str
        goal: str
        workflow: str
        status: str
        plan: Dict[str, Any]
        steps: List[StepResult]
        artifacts: Dict[str, Any]
        errors: List[str]
        started_at: str
        finished_at: str
        duration_ms: float

        @property
        def total_steps(self):
            return len(self.steps)

        @property
        def passed(self):
            return sum(1 for s in self.steps if s.status == "passed")

        @property
        def failed(self):
            return sum(1 for s in self.steps if s.status == "failed")

        @property
        def skipped(self):
            return sum(1 for s in self.steps if s.status == "skipped")

    @dataclass
    class OrchestratorConfig:
        max_retries: int = 3
        step_timeout: float = 120
        planning_timeout: float = 60
        enable_enrichment: bool = True
        artifact_dir: str = "data/logs"

    class Orchestrator:
        def __init__(
            self,
            config: OrchestratorConfig | None = None,
            on_event: Callable[[OrchestratorEvent], None] | None = None,
        ):
            self.config = config or OrchestratorConfig()
            self.on_event = on_event or self._default_event_handler

        @staticmethod
        def _default_event_handler(event: OrchestratorEvent):
            print(f"[{event.type.value.upper()}] {event.message}")

        def _emit(self, type: EventType, message: str, data: dict | None = None):
            self.on_event(OrchestratorEvent(type=type, message=message, data=data or {}))

        def run(
            self,
            spec: str,
            workflow,
            context: Dict[str, Any] | None = None,
        ) -> RunResult:
            context = context or {}
            run_id = str(uuid.uuid4())
            started_at = datetime.datetime.utcnow().isoformat()
            start_ts = datetime.datetime.utcnow()
            errors: List[str] = []
            step_results: List[StepResult] = []

            self._emit(EventType.INFO, f"Run {run_id} started | workflow={workflow.name}")

            # 1. Enrich spec (skip if already done upstream)
            enriched_spec = spec
            if self.config.enable_enrichment and hasattr(workflow, "enrich"):
                try:
                    enriched_spec = workflow.enrich(spec, context)
                    self._emit(EventType.INFO, "Spec enrichment complete")
                except Exception as e:
                    errors.append(f"Enrichment failed: {e}")
                    self._emit(EventType.WARNING, f"Enrichment failed: {e}")

            # 2. Plan — CRITICAL FIX: pass full context into plan()
            plan = {}
            try:
                self._emit(EventType.INFO, "Planning...")
                plan = workflow.plan(enriched_spec, context)
                self._emit(EventType.PLAN_READY, f"Plan ready: {len(plan.get('steps', []))} steps")
            except Exception as e:
                errors.append(f"Planning failed: {e}")
                self._emit(EventType.ERROR, f"Planning failed: {e}")
                plan = {"goal": "unknown", "steps": [], "assumptions": []}

            goal = plan.get("goal", spec[:80])
            steps = plan.get("steps", [])

            # 3. Execute steps
            for i, step in enumerate(steps):
                tool = step.get("tool", "unknown")
                desc = step.get("args", {}).get("description", "")
                self._emit(EventType.STEP_START, f"Step {i}: {tool} — {desc}")

                step_start = datetime.datetime.utcnow()
                step_context: Dict[str, Any] = {"run_id": run_id, **context}
                # Add outputs from previous steps for workflows that reference them
                for j, sr in enumerate(step_results):
                    step_context[f"step_{j}"] = sr.output
                    step_context[f"step_{j}_output"] = sr.output
                step_context["last_output"] = step_results[-1].output if step_results else None

                retries = 0
                output = None
                error_msg = None
                status = "failed"

                for attempt in range(self.config.max_retries + 1):
                    try:
                        output = workflow.execute_step(step, enriched_spec, step_context)
                        if hasattr(workflow, "evaluate_step_result"):
                            status = workflow.evaluate_step_result(step, output)
                        else:
                            status = _default_evaluate(output)
                        error_msg = None
                        break
                    except Exception as e:
                        error_msg = str(e)
                        retries = attempt
                        if attempt < self.config.max_retries:
                            self._emit(EventType.WARNING, f"Step {i} retry {attempt + 1}: {e}")
                        else:
                            self._emit(EventType.ERROR, f"Step {i} failed after {self.config.max_retries} retries: {e}")
                            errors.append(f"Step {i} ({tool}): {e}")

                duration_ms = (datetime.datetime.utcnow() - step_start).total_seconds() * 1000
                step_results.append(StepResult(
                    step_index=i,
                    tool=tool,
                    status=status,
                    output=output,
                    error=error_msg,
                    duration_ms=duration_ms,
                    retries=retries,
                ))
                self._emit(EventType.STEP_END, f"Step {i}: {status} ({duration_ms:.0f}ms)")

            # 4. Report
            artifacts: Dict[str, Any] = {}
            if hasattr(workflow, "report"):
                try:
                    run_result_partial = _PartialRunResult(run_id, goal, workflow.name, step_results, started_at)
                    artifacts = workflow.report(enriched_spec, plan, run_result_partial) or {}
                except Exception as e:
                    errors.append(f"Report failed: {e}")

            if hasattr(workflow, "verify"):
                try:
                    run_result_partial = _PartialRunResult(run_id, goal, workflow.name, step_results, started_at)
                    workflow.verify(run_result_partial)
                except Exception:
                    pass

            finished_at = datetime.datetime.utcnow().isoformat()
            duration_ms = (datetime.datetime.utcnow() - start_ts).total_seconds() * 1000
            overall_status = "passed" if not any(s.status == "failed" for s in step_results) else "failed"

            result = RunResult(
                run_id=run_id,
                goal=goal,
                workflow=workflow.name,
                status=overall_status,
                plan=plan,
                steps=step_results,
                artifacts=artifacts,
                errors=errors,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

            self._emit(EventType.RUN_COMPLETE, f"Run complete: {overall_status} | {result.passed}/{result.total_steps} passed")
            return result


def _default_evaluate(output: Any) -> str:
    if isinstance(output, dict):
        if output.get("status") in ("ok", "passed", "completed"):
            return "passed"
        if output.get("status") in ("error", "failed"):
            return "failed"
        if output.get("status") == "skipped":
            return "skipped"
    if isinstance(output, str) and output.startswith("[SKIP]"):
        return "skipped"
    return "passed"


class _PartialRunResult:
    """Lightweight run result used during report/verify calls."""
    def __init__(self, run_id, goal, workflow_name, steps, started_at):
        self.run_id = run_id
        self.goal = goal
        self.workflow = workflow_name
        self.steps = steps
        self.started_at = started_at

    @property
    def passed(self):
        return sum(1 for s in self.steps if s.status == "passed")

    @property
    def failed(self):
        return sum(1 for s in self.steps if s.status == "failed")

    @property
    def total_steps(self):
        return len(self.steps)

    @property
    def duration_ms(self):
        return 0
