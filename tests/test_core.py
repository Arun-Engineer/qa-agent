"""
tests/test_core.py — Tests for core error classes and LLM client

Run: pytest tests/test_core.py -v
"""
import pytest
from agent.core.errors import (
    AgentError,
    PlanningError,
    ExecutionError,
    ToolError,
    TimeoutError,
    RetryExhaustedError,
    LLMError,
)


class TestErrorHierarchy:

    def test_all_errors_inherit_from_agent_error(self):
        errors = [
            PlanningError("test"),
            ExecutionError("test"),
            ToolError("test", tool="pytest"),
            TimeoutError("test"),
            RetryExhaustedError("test"),
            LLMError("test", provider="openai"),
        ]
        for e in errors:
            assert isinstance(e, AgentError)
            assert isinstance(e, Exception)

    def test_agent_error_to_dict(self):
        cause = ValueError("bad input")
        e = AgentError("something failed", cause=cause)
        d = e.to_dict()
        assert d["type"] == "AgentError"
        assert d["message"] == "something failed"
        assert d["cause"]["type"] == "ValueError"
        assert d["cause"]["message"] == "bad input"

    def test_agent_error_without_cause(self):
        e = AgentError("simple error")
        d = e.to_dict()
        assert "cause" not in d

    def test_tool_error_has_tool_name(self):
        e = ToolError("pytest crashed", tool="pytest_runner")
        assert e.tool == "pytest_runner"
        assert "pytest crashed" in str(e)

    def test_llm_error_has_provider(self):
        e = LLMError("rate limited", provider="openai", model="gpt-4o-mini")
        assert e.provider == "openai"
        assert e.model == "gpt-4o-mini"

    def test_error_chaining(self):
        original = ConnectionError("network down")
        wrapped = ExecutionError("step failed", cause=original)
        assert wrapped.cause is original
        assert "step failed" in str(wrapped)

    def test_catch_specific_error(self):
        """Can catch PlanningError without catching ExecutionError."""
        try:
            raise PlanningError("bad plan")
        except PlanningError as e:
            assert "bad plan" in str(e)
        except AgentError:
            pytest.fail("Should have caught PlanningError specifically")

    def test_catch_base_error(self):
        """Can catch any agent error via AgentError."""
        for ErrorClass in [PlanningError, ExecutionError, ToolError, TimeoutError]:
            try:
                raise ErrorClass("test")
            except AgentError:
                pass  # Expected
            except Exception:
                pytest.fail(f"{ErrorClass.__name__} should be catchable as AgentError")
