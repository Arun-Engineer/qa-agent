"""
tests/test_orchestrator.py — Tests for the orchestration engine

Run: pytest tests/test_orchestrator.py -v
"""
import time
import pytest
from unittest.mock import MagicMock, patch

from agent.core.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    RunState,
    RunResult,
    StepResult,
    EventType,
)
from agent.core.errors import (
    AgentError,
    PlanningError,
    ExecutionError,
    TimeoutError,
    RetryExhaustedError,
)
from agent.core.base_workflow import BaseWorkflow


# ─── Test Workflow (mock) ───

class MockWorkflow(BaseWorkflow):
    """A workflow that returns predictable results for testing."""

    @property
    def name(self):
        return "mock_test"

    def __init__(self, plan_result=None, step_results=None, should_fail_plan=False):
        self._plan_result = plan_result or {
            "goal": "Test something",
            "assumptions": ["app is running"],
            "steps": [
                {"tool": "mock_tool", "args": {"check": "login"}},
                {"tool": "mock_tool", "args": {"check": "dashboard"}},
            ],
        }
        self._step_results = step_results or [
            {"status": "passed", "code": 0, "summary": {"passed": 3, "failed": 0}},
            {"status": "passed", "code": 0, "summary": {"passed": 2, "failed": 0}},
        ]
        self._step_index = 0
        self._should_fail_plan = should_fail_plan

    def plan(self, spec, context):
        if self._should_fail_plan:
            return {"error": "LLM unavailable"}
        return self._plan_result

    def execute_step(self, step, spec, step_context):
        idx = self._step_index
        self._step_index += 1
        if idx < len(self._step_results):
            result = self._step_results[idx]
            if isinstance(result, Exception):
                raise result
            return result
        return {"status": "passed", "code": 0}

    def report(self, spec, plan, run_result):
        return {"run_json": "mock_run.json", "pdf": None, "xlsx": None}


class FailingStepWorkflow(MockWorkflow):
    """Workflow where step 2 fails."""

    def __init__(self):
        super().__init__(step_results=[
            {"status": "passed", "code": 0, "summary": {"passed": 3, "failed": 0}},
            {"status": "failed", "code": 1, "summary": {"passed": 1, "failed": 2}},
        ])


class TimeoutStepWorkflow(MockWorkflow):
    """Workflow where a step takes too long."""

    def execute_step(self, step, spec, step_context):
        if self._step_index == 1:
            self._step_index += 1
            time.sleep(10)  # Will be killed by timeout
        return super().execute_step(step, spec, step_context)


class RetryableStepWorkflow(MockWorkflow):
    """Workflow where step fails once then succeeds."""

    def __init__(self):
        self._attempt = 0
        super().__init__()

    def execute_step(self, step, spec, step_context):
        self._attempt += 1
        if self._attempt == 1:
            raise ConnectionError("Temporary network error")
        return {"status": "passed", "code": 0, "summary": {"passed": 1, "failed": 0}}


# ─── Tests ───

class TestOrchestratorBasic:

    def test_successful_run(self):
        """Full successful run: plan → execute 2 steps → report."""
        orch = Orchestrator(config=OrchestratorConfig(enable_verification=False))
        result = orch.run(spec="Test the login page", workflow=MockWorkflow())

        assert result.state == RunState.DONE
        assert result.status == "completed"
        assert result.passed == 2
        assert result.failed == 0
        assert result.total_steps == 2
        assert len(result.steps) == 2
        assert result.goal == "Test something"
        assert result.workflow == "mock_test"
        assert result.duration_ms > 0
        assert result.started_at is not None
        assert result.finished_at is not None

    def test_run_with_failures(self):
        """Run where one step fails."""
        orch = Orchestrator(config=OrchestratorConfig(enable_verification=False))
        result = orch.run(spec="Test login", workflow=FailingStepWorkflow())

        assert result.state == RunState.DONE
        assert result.status == "completed_with_failures"
        assert result.passed == 1
        assert result.failed == 1

    def test_planning_failure(self):
        """Run where planning fails entirely."""
        orch = Orchestrator(config=OrchestratorConfig(
            max_retries=0, enable_verification=False,
        ))
        result = orch.run(
            spec="Test login",
            workflow=MockWorkflow(should_fail_plan=True),
        )

        assert result.state == RunState.FAILED
        assert result.status == "failed"
        assert len(result.errors) > 0
        assert "Planning failed" in result.errors[0]["message"]

    def test_stop_on_first_failure(self):
        """With stop_on_first_failure, remaining steps are skipped."""
        workflow = MockWorkflow(
            plan_result={
                "goal": "Test",
                "steps": [
                    {"tool": "t1", "args": {}},
                    {"tool": "t2", "args": {}},
                    {"tool": "t3", "args": {}},
                ],
            },
            step_results=[
                {"code": 1, "summary": {"passed": 0, "failed": 1}},  # fails
                {"code": 0},  # would pass but should be skipped
                {"code": 0},  # would pass but should be skipped
            ],
        )
        orch = Orchestrator(config=OrchestratorConfig(
            stop_on_first_failure=True, enable_verification=False,
        ))
        result = orch.run(spec="Test", workflow=workflow)

        assert result.failed == 1
        assert result.skipped == 2
        assert len(result.steps) == 3
        assert result.steps[1].status == "skipped"
        assert result.steps[2].status == "skipped"

    def test_empty_spec(self):
        """Empty spec should still attempt to plan (LLM might handle it)."""
        orch = Orchestrator(config=OrchestratorConfig(enable_verification=False))
        result = orch.run(spec="", workflow=MockWorkflow())
        # Plan succeeds because MockWorkflow doesn't check spec content
        assert result.state == RunState.DONE


class TestOrchestratorRetry:

    def test_step_retry_on_transient_error(self):
        """Step fails once, succeeds on retry."""
        orch = Orchestrator(config=OrchestratorConfig(
            max_retries=2,
            retry_base_delay=0.01,  # fast for tests
            enable_verification=False,
        ))
        result = orch.run(spec="Test", workflow=RetryableStepWorkflow())

        # First step retried and passed
        assert result.steps[0].retries > 0
        assert result.steps[0].status == "passed"

    def test_step_timeout(self):
        """Step exceeds timeout → error status."""
        orch = Orchestrator(config=OrchestratorConfig(
            step_timeout=0.5,  # 500ms timeout
            max_retries=0,
            enable_verification=False,
        ))
        result = orch.run(spec="Test", workflow=TimeoutStepWorkflow())

        # Second step should timeout
        timeout_step = [s for s in result.steps if "timeout" in (s.error or "").lower()]
        assert len(timeout_step) > 0


class TestOrchestratorEvents:

    def test_events_emitted(self):
        """Check that events are emitted during run."""
        events = []

        def capture(event):
            events.append(event)

        orch = Orchestrator(
            config=OrchestratorConfig(enable_verification=False),
            on_event=capture,
        )
        result = orch.run(spec="Test", workflow=MockWorkflow())

        event_types = [e.type for e in events]
        assert EventType.STATE_CHANGE in event_types
        assert EventType.STEP_START in event_types
        assert EventType.STEP_COMPLETE in event_types
        assert EventType.INFO in event_types

    def test_state_transitions(self):
        """Verify correct state transition order."""
        states = []

        def capture(event):
            if event.type == EventType.STATE_CHANGE:
                states.append(event.data.get("to"))

        orch = Orchestrator(
            config=OrchestratorConfig(enable_verification=False),
            on_event=capture,
        )
        orch.run(spec="Test", workflow=MockWorkflow())

        assert states == ["enriching", "planning", "executing", "reporting", "done"]


class TestOrchestratorConfig:

    def test_default_config(self):
        cfg = OrchestratorConfig()
        assert cfg.max_retries == 3
        assert cfg.step_timeout == 120.0
        assert cfg.planning_timeout == 60.0
        assert cfg.enable_enrichment is True
        assert cfg.stop_on_first_failure is False

    def test_custom_config(self):
        cfg = OrchestratorConfig(max_retries=5, step_timeout=30)
        assert cfg.max_retries == 5
        assert cfg.step_timeout == 30


class TestRunResult:

    def test_status_property(self):
        r = RunResult(run_id="test", state=RunState.DONE, failed=0)
        assert r.status == "completed"

        r.failed = 1
        assert r.status == "completed_with_failures"

        r.state = RunState.FAILED
        assert r.status == "failed"

        r.state = RunState.EXECUTING
        assert r.status == "running"


class TestErrorDiagnosis:

    def test_diagnose_timeout(self):
        orch = Orchestrator()
        assert orch._diagnose_error(Exception("timeout"), {}) == "timeout"

    def test_diagnose_auth(self):
        orch = Orchestrator()
        assert orch._diagnose_error(Exception("401 unauthorized"), {}) == "auth_error"

    def test_diagnose_rate_limit(self):
        orch = Orchestrator()
        assert orch._diagnose_error(Exception("429 rate limit"), {}) == "rate_limited"

    def test_diagnose_connection(self):
        orch = Orchestrator()
        assert orch._diagnose_error(Exception("connection refused"), {}) == "connection_error"

    def test_diagnose_unknown(self):
        orch = Orchestrator()
        assert orch._diagnose_error(Exception("something weird"), {}) == "unknown"
