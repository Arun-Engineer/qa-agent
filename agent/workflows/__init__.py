"""
agent/workflows/__init__.py — Workflow Registry

Maps workflow names to their implementations.
Used by the API layer to dispatch runs.
"""
from __future__ import annotations

from typing import Dict, Type

from agent.core.base_workflow import BaseWorkflow


# Lazy imports to avoid circular deps
_REGISTRY: Dict[str, Type[BaseWorkflow]] = {}


def _ensure_registry():
    if _REGISTRY:
        return
    from agent.workflows.api_test import ApiTestWorkflow
    from agent.workflows.ui_test import UiTestWorkflow
    from agent.workflows.spec_review import SpecReviewWorkflow

    _REGISTRY["api_test"] = ApiTestWorkflow
    _REGISTRY["ui_test"] = UiTestWorkflow
    _REGISTRY["spec_review"] = SpecReviewWorkflow
    # Legacy alias
    _REGISTRY["generate_testcases"] = ApiTestWorkflow
    _REGISTRY["default"] = ApiTestWorkflow


def get_workflow(name: str) -> BaseWorkflow:
    """Get a workflow instance by name."""
    _ensure_registry()
    cls = _REGISTRY.get(name)
    if not cls:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(f"Unknown workflow: '{name}'. Available: {available}")
    return cls()


def list_workflows() -> Dict[str, str]:
    """List available workflows with descriptions."""
    _ensure_registry()
    seen = set()
    result = {}
    for name, cls in _REGISTRY.items():
        if cls not in seen:
            instance = cls()
            result[name] = instance.description
            seen.add(cls)
    return result
