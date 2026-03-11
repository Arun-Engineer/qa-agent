"""
agent/core/orchestrator.py — Production Orchestration Engine

Replaces the 15-line skeleton with a proper state machine:
  INIT → PLANNING → EXECUTING → VERIFYING → REPORTING → DONE/FAILED

Features:
  - Step-level retry with exponential backoff
  - Execution timeouts per step
  - Context passing between steps (output of step N feeds step N+1)
  - Graceful degradation (RAG down? skip enrichment)
  - Structured error capture at every level
  - Event hooks for console/logging
"""
from __future__ import annotations

import datetime as dt
from datetime import timezone as _tz
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.core.errors import (
    AgentError,
    PlanningError,
    ExecutionError,
    ToolError,
    TimeoutError,
    RetryExhaustedError,
)


# ─── Run States ───
class RunState(str, Enum):
    INIT = "init"
    PLANNING = "planning"
    ENRICHING = "enriching"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPORTING = "reporting"
    DONE = "done"
    FAILED = "failed"


# ─── Step Result ───
@dataclass
class StepResult:
    step_index: int
    tool: str
    status: str  # "passed" | "failed" | "skipped" | "error"
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    retries: int = 0
    diagnosis: Optional[str] = None


# ─── Run Result ───
@dataclass
class RunResult:
    run_id: str
    state: RunState
    goal: str = ""
    workflow: str = "default"
    plan: Dict[str, Any] = field(default_factory=dict)
    steps: List[StepResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_steps: int = 0
    artifacts: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: float = 0.0

    @property
    def status(self) -> str:
        if self.state == RunState.DONE:
            return "completed" if self.failed == 0 else "completed_with_failures"
        if self.state == RunState.FAILED:
            return "failed"
        return "running"


# ─── Orchestrator Config ───
@dataclass
class OrchestratorConfig:
    max_retries: int = 3
    retry_base_delay: float = 1.0  # seconds, doubles each retry
    step_timeout: float = 120.0  # seconds per step
    planning_timeout: float = 60.0  # seconds for plan generation
    enable_enrichment: bool = True  # RAG/recon enrichment
    enable_verification: bool = True  # post-execution verification
    stop_on_first_failure: bool = False  # abort remaining steps on failure
    artifact_dir: str = "data/logs"


# ─── Event Types ───
class EventType(str, Enum):
    STATE_CHANGE = "state_change"
    STEP_START = "step_start"
    STEP_COMPLETE = "step_complete"
    STEP_RETRY = "step_retry"
    STEP_FAILED = "step_failed"
    ERROR = "error"
    INFO = "info"


@dataclass
class OrchestratorEvent:
    type: EventType
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = dt.datetime.now(_tz.utc).isoformat()
            self.timestamp = dt.datetime.utcnow().isoformat()


# ─── Orchestrator ───
class Orchestrator:
    """
    Production orchestration engine for QA agent workflows.

    Usage:
        from agent.core.orchestrator import Orchestrator, OrchestratorConfig
        from agent.workflows.api_test import ApiTestWorkflow

        orch = Orchestrator(config=OrchestratorConfig())
        result = orch.run(
            spec="Test the login API at https://example.com/api/login",
            workflow=ApiTestWorkflow(),
        )
    """

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        on_event: Optional[Callable[[OrchestratorEvent], None]] = None,
    ):
        self.config = config or OrchestratorConfig()
        self.on_event = on_event or self._default_event_handler
        self._run: Optional[RunResult] = None

    # ─── Public API ───

    def run(
        self,
        spec: str,
        workflow: "BaseWorkflow",
        context: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        """
        Execute a full QA workflow from spec to report.
        """
        import uuid

        run_id = str(uuid.uuid4())[:8]
        start = time.time()

        self._run = RunResult(
            run_id=run_id,
            state=RunState.INIT,
            workflow=workflow.name,
            started_at=dt.datetime.now(_tz.utc).isoformat(),
            #started_at=dt.datetime.utcnow().isoformat(),
            context=context or {},
        )

        try:
            # Phase 1: Enrichment (optional)
            self._transition(RunState.ENRICHING)
            enriched_spec = self._enrich(spec, workflow)

            # Phase 2: Planning
            self._transition(RunState.PLANNING)
            plan = self._plan(enriched_spec, workflow)
            self._run.plan = plan
            self._run.goal = plan.get("goal", "")
            self._run.total_steps = len(plan.get("steps", []))

            # Phase 3: Execution
            self._transition(RunState.EXECUTING)
            self._execute_steps(plan, enriched_spec, workflow)

            # Phase 4: Verification (optional)
            if self.config.enable_verification:
                self._transition(RunState.VERIFYING)
                self._verify(workflow)

            # Phase 5: Reporting
            self._transition(RunState.REPORTING)
            self._report(spec, plan, workflow)

            # Done
            self._transition(RunState.DONE)

        except AgentError as e:
            self._handle_error(e)
        except Exception as e:
            self._handle_error(
                AgentError(f"Unexpected error: {type(e).__name__}: {str(e)}", cause=e)
            )

        elapsed = (time.time() - start) * 1000
        self._run.duration_ms = round(elapsed, 2)
        self._run.finished_at = dt.datetime.now(_tz.utc).isoformat()
        self._run.finished_at = dt.datetime.utcnow().isoformat()
        return self._run

    # ─── Phase Implementations ───

    def _enrich(self, spec: str, workflow: "BaseWorkflow") -> str:
        """Optional enrichment: RAG retrieval, site recon, context injection."""
        if not self.config.enable_enrichment:
            return spec

        try:
            enriched = workflow.enrich(spec, self._run.context)
            if enriched and enriched != spec:
                self._emit(EventType.INFO, "Spec enriched with additional context")
            return enriched or spec
        except Exception as e:
            # Enrichment failure is non-fatal — proceed with original spec
            self._emit(EventType.ERROR, f"Enrichment failed (non-fatal): {e}")
            return spec

    def _plan(self, spec: str, workflow: "BaseWorkflow") -> Dict[str, Any]:
        """Generate execution plan via LLM."""
        self._emit(EventType.INFO, f"Generating plan via {workflow.name}...")

        plan = self._retry_with_backoff(
            lambda: workflow.plan(spec, self._run.context),
            label="planning",
            timeout=self.config.planning_timeout,
        )

        if not plan or "steps" not in plan:
            error_msg = plan.get("error", "No steps in plan") if isinstance(plan, dict) else "Invalid plan"
            raise PlanningError(f"Planning failed: {error_msg}")

        steps = plan.get("steps", [])
        self._emit(
            EventType.INFO,
            f"Plan generated: {len(steps)} steps, goal: {plan.get('goal', 'N/A')}",
        )
        return plan

    def _execute_steps(
        self, plan: Dict[str, Any], spec: str, workflow: "BaseWorkflow"
    ):
        """Execute each step with retry and timeout."""
        steps = plan.get("steps", [])
        step_context = {"spec": spec, "plan": plan}

        for i, step in enumerate(steps):
            tool = step.get("tool", "unknown")
            self._emit(
                EventType.STEP_START,
                f"Step {i + 1}/{len(steps)}: {tool}",
                {"step_index": i, "tool": tool, "args": step.get("args", {})},
            )

            result = self._execute_single_step(i, step, spec, step_context, workflow)
            self._run.steps.append(result)

            # Update counters
            if result.status == "passed":
                self._run.passed += 1
            elif result.status == "failed":
                self._run.failed += 1
            elif result.status == "skipped":
                self._run.skipped += 1

            # Pass output to next step's context
            step_context[f"step_{i}_output"] = result.output
            step_context["last_output"] = result.output

            self._emit(
                EventType.STEP_COMPLETE,
                f"Step {i + 1} {result.status}: {tool} ({result.duration_ms:.0f}ms, {result.retries} retries)",
                {"step_index": i, "status": result.status},
            )

            # Stop on failure if configured
            if result.status in ("failed", "error") and self.config.stop_on_first_failure:
                self._emit(EventType.INFO, "Stopping: stop_on_first_failure is enabled")
                # Mark remaining steps as skipped
                for j in range(i + 1, len(steps)):
                    self._run.steps.append(
                        StepResult(
                            step_index=j,
                            tool=steps[j].get("tool", "unknown"),
                            status="skipped",
                            error="Skipped due to prior failure",
                        )
                    )
                    self._run.skipped += 1
                break

    def _execute_single_step(
        self,
        index: int,
        step: Dict[str, Any],
        spec: str,
        step_context: Dict[str, Any],
        workflow: "BaseWorkflow",
    ) -> StepResult:
        """Execute one step with retry logic."""
        tool = step.get("tool", "unknown")
        start = time.time()
        retries = 0

        for attempt in range(self.config.max_retries + 1):
            try:
                output = self._run_with_timeout(
                    lambda: workflow.execute_step(step, spec, step_context),
                    timeout=self.config.step_timeout,
                    label=f"step_{index}_{tool}",
                )

                # Determine pass/fail from output
                status = workflow.evaluate_step_result(step, output)

                elapsed = (time.time() - start) * 1000
                return StepResult(
                    step_index=index,
                    tool=tool,
                    status=status,
                    output=output,
                    duration_ms=round(elapsed, 2),
                    retries=attempt,
                )

            except TimeoutError:
                retries = attempt
                elapsed = (time.time() - start) * 1000
                return StepResult(
                    step_index=index,
                    tool=tool,
                    status="error",
                    error=f"Timeout after {self.config.step_timeout}s",
                    duration_ms=round(elapsed, 2),
                    retries=attempt,
                    #retries=retries,
                )

            except Exception as e:
                retries = attempt
                if attempt < self.config.max_retries:
                    delay = self.config.retry_base_delay * (2 ** attempt)
                    self._emit(
                        EventType.STEP_RETRY,
                        f"Step {index + 1} retry {attempt + 1}/{self.config.max_retries}: {e}",
                        {"delay": delay},
                    )
                    time.sleep(delay)
                else:
                    elapsed = (time.time() - start) * 1000
                    return StepResult(
                        step_index=index,
                        tool=tool,
                        status="error",
                        error=f"Failed after {self.config.max_retries} retries: {str(e)}",
                        duration_ms=round(elapsed, 2),
                        retries=attempt,
                        #retries=retries,
                        diagnosis=self._diagnose_error(e, step),
                    )

        # Should not reach here
        elapsed = (time.time() - start) * 1000
        return StepResult(
            step_index=index, tool=tool, status="error",
            error="Unexpected retry loop exit", duration_ms=round(elapsed, 2),
        )

    def _verify(self, workflow: "BaseWorkflow"):
        """Post-execution verification pass."""
        try:
            workflow.verify(self._run)
            self._emit(EventType.INFO, "Verification complete")
        except Exception as e:
            self._emit(EventType.ERROR, f"Verification failed (non-fatal): {e}")

    def _report(self, spec: str, plan: Dict, workflow: "BaseWorkflow"):
        """Generate artifacts (PDF, Excel, JSON)."""
        try:
            artifacts = workflow.report(spec, plan, self._run)
            self._run.artifacts = artifacts or {}
            self._emit(EventType.INFO, f"Artifacts generated: {list((artifacts or {}).keys())}")
        except Exception as e:
            self._emit(EventType.ERROR, f"Report generation failed: {e}")
            self._run.artifacts = {"error": str(e)}

    # ─── Utilities ───

    def _retry_with_backoff(
        self, fn: Callable, label: str = "", timeout: float = 60.0
    ) -> Any:
        """Retry a function with exponential backoff."""
        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._run_with_timeout(fn, timeout=timeout, label=label)
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries:
                    delay = self.config.retry_base_delay * (2 ** attempt)
                    self._emit(
                        EventType.STEP_RETRY,
                        f"{label} retry {attempt + 1}: {e} (waiting {delay}s)",
                    )
                    time.sleep(delay)

        raise RetryExhaustedError(
            f"{label} failed after {self.config.max_retries} retries: {last_error}",
            cause=last_error,
        )

    def _run_with_timeout(
        self, fn: Callable, timeout: float, label: str = ""
    ) -> Any:
        """
        Run a function with a timeout.
        Uses threading for cross-platform compatibility (Windows + Linux).
        """
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(f"{label} timed out after {timeout}s")

    def _diagnose_error(self, error: Exception, step: Dict) -> str:
        """Classify the error for triage."""
        msg = str(error).lower()
        if "timeout" in msg:
            return "timeout"
        if "assert" in msg:
            return "assertion_failed"
        if "connection" in msg or "refused" in msg:
            return "connection_error"
        if "401" in msg or "unauthorized" in msg:
            return "auth_error"
        if "404" in msg or "not found" in msg:
            return "not_found"
        if "rate limit" in msg or "429" in msg:
            return "rate_limited"
        if "openai" in msg or "api key" in msg:
            return "llm_error"
        return "unknown"

    def _transition(self, new_state: RunState):
        """State machine transition with event emission."""
        old = self._run.state if self._run else RunState.INIT
        if self._run:
            self._run.state = new_state
        self._emit(
            EventType.STATE_CHANGE,
            f"{old.value} → {new_state.value}",
            {"from": old.value, "to": new_state.value},
        )

    def _handle_error(self, error: AgentError):
        """Handle fatal errors — transition to FAILED state."""
        self._run.state = RunState.FAILED
        self._run.errors.append({
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "timestamp": dt.datetime.now(_tz.utc).isoformat(),
            "timestamp": dt.datetime.utcnow().isoformat(),
        })
        self._emit(EventType.ERROR, f"Run failed: {error}")

    def _emit(self, event_type: EventType, message: str, data: Dict = None):
        """Emit an event to the registered handler."""
        event = OrchestratorEvent(type=event_type, message=message, data=data or {})
        try:
            self.on_event(event)
        except Exception:
            pass  # Never let event handling crash the orchestrator

    @staticmethod
    def _default_event_handler(event: OrchestratorEvent):
        """Default: print to console."""
        prefix = {
            EventType.STATE_CHANGE: "⚡",
            EventType.STEP_START: "▶",
            EventType.STEP_COMPLETE: "✓",
            EventType.STEP_RETRY: "🔄",
            EventType.STEP_FAILED: "✗",
            EventType.ERROR: "❌",
            EventType.INFO: "ℹ",
        }.get(event.type, "•")
        print(f"  {prefix} [{event.type.value}] {event.message}")
