# tests/unit/test_tools.py
from agent.tools import pytest_runner, playwright_runner
from pathlib import Path


def test_run_pytest_handles_missing():
    result = pytest_runner.run_pytest("nonexistent_file.py")
    assert isinstance(result, dict)
    assert result["code"] != 0


def test_run_playwright_safely():
    result = playwright_runner.run_playwright("tests/ui/fake_test.py")
    assert "status" in result
